"""Network - "이거 없으면 벙어리다".

모든 서브시스템(신경신호 -> 의도 -> 게임 -> 디바이스 -> 트윈)이 주고받는
토픽 기반 메시지 버스. SecureChannel 은 HMAC-SHA256 서명으로 메시지
위변조와 사칭을 막는다 (명세서 [결과]의 VPN 역할 중 '인증/무결성' 부분).

현재는 프로세스 내부 버스이고, WIFI/MQTT 같은 실제 전송 계층은
Message.to_json() / NetworkBus.receive() 를 경계로 붙이면 된다.
기밀성(암호화)은 전송 계층의 TLS 에 맡긴다.
"""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import time
from collections import deque
from dataclasses import asdict, dataclass, replace
from typing import Any, Callable

Handler = Callable[["Message"], None]


@dataclass(frozen=True)
class Message:
    topic: str
    payload: dict[str, Any]
    sender: str
    seq: int
    timestamp: float
    signature: str = ""

    def signing_bytes(self) -> bytes:
        body = {k: v for k, v in asdict(self).items() if k != "signature"}
        return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "Message":
        return cls(**json.loads(text))


class SecureChannel:
    def __init__(self, key: bytes):
        if len(key) < 16:
            raise ValueError("key must be at least 16 bytes")
        self._key = key

    def sign(self, msg: Message) -> Message:
        sig = hmac.new(self._key, msg.signing_bytes(), hashlib.sha256).hexdigest()
        return replace(msg, signature=sig)

    def verify(self, msg: Message) -> bool:
        expected = hmac.new(self._key, msg.signing_bytes(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, msg.signature)


class NetworkBus:
    def __init__(self, channel: SecureChannel | None = None, history: int = 256):
        self.channel = channel
        self._subs: list[tuple[str, Handler]] = []
        self._seq = 0
        self.history: deque[Message] = deque(maxlen=history)
        self.delivered = 0
        self.rejected = 0

    def subscribe(self, pattern: str, handler: Handler) -> None:
        """pattern 은 fnmatch 와일드카드: 'device/*/state'."""
        self._subs.append((pattern, handler))

    def publish(self, topic: str, payload: dict[str, Any], sender: str) -> Message:
        self._seq += 1
        msg = Message(topic, dict(payload), sender, self._seq, time.time())
        if self.channel:
            msg = self.channel.sign(msg)
        self._deliver(msg)
        return msg

    def receive(self, msg: Message) -> bool:
        """외부(전송 계층)에서 들어온 메시지. 서명 검증 실패 시 버린다."""
        if self.channel and not self.channel.verify(msg):
            self.rejected += 1
            return False
        self._deliver(msg)
        return True

    def _deliver(self, msg: Message) -> None:
        self.history.append(msg)
        for pattern, handler in self._subs:
            if fnmatch.fnmatchcase(msg.topic, pattern):
                handler(msg)
                self.delivered += 1
