"""MQTT 3.1.1 (OASIS 표준) 브로커 / 클라이언트 - 표준 라이브러리만.

실제 IoT 장치(ESP32 PubSubClient, 스마트폰 MQTT 앱, Node-RED, Home Assistant,
mosquitto_pub/sub, paho-mqtt ...)가 그대로 접속할 수 있는 최소 구현이다.

지원: CONNECT(3.1 / 3.1.1, 사용자/비밀번호), PUBLISH QoS 0/1/2 수신, SUBSCRIBE(+, # 와일드카드),
      UNSUBSCRIBE, retained 메시지, PINGREQ, keepalive. 구독자에게는 QoS 0 으로 전달한다.
미지원: MQTT 5, 세션 유지(clean session=0), will 메시지 전달, TLS (필요하면 앞단에 TLS 프록시).
"""

from __future__ import annotations

import json
import queue
import socket
import socketserver
import struct
import threading
from typing import Callable

CONNECT, CONNACK, PUBLISH, PUBACK, PUBREC, PUBREL, PUBCOMP = 1, 2, 3, 4, 5, 6, 7
SUBSCRIBE, SUBACK, UNSUBSCRIBE, UNSUBACK, PINGREQ, PINGRESP, DISCONNECT = 8, 9, 10, 11, 12, 13, 14
MAX_PACKET = 256 * 1024

OnPublish = Callable[[str, bytes, str], None]  # (topic, payload, client_id)


def topic_matches(filt: str, topic: str) -> bool:
    """MQTT 토픽 필터 매칭: '+' 한 단계, '#' 나머지 전부. '$' 로 시작하는 토픽은 와일드카드로 안 잡힘."""
    if topic.startswith("$") and filt[:1] in ("+", "#"):
        return False
    f, t = filt.split("/"), topic.split("/")
    for i, part in enumerate(f):
        if part == "#":
            return True
        if i >= len(t) or (part != "+" and part != t[i]):
            return False
    return len(f) == len(t)


def _enc_len(n: int) -> bytes:
    out = bytearray()
    while True:
        b, n = n % 128, n // 128
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _str(s: str) -> bytes:
    b = s.encode()
    return struct.pack("!H", len(b)) + b


def _packet(ptype: int, flags: int, body: bytes = b"") -> bytes:
    return bytes([(ptype << 4) | flags]) + _enc_len(len(body)) + body


def _read_packet(rf) -> tuple[int, int, bytes] | None:
    head = rf.read(1)
    if not head:
        return None
    length, mult = 0, 1
    for _ in range(4):
        b = rf.read(1)
        if not b:
            return None
        length += (b[0] & 0x7F) * mult
        mult *= 128
        if not b[0] & 0x80:
            break
    if length > MAX_PACKET:
        raise ValueError("packet too large")
    body = rf.read(length) if length else b""
    if len(body) != length:
        return None
    return head[0] >> 4, head[0] & 0x0F, body


class _Reader:
    def __init__(self, data: bytes):
        self.data, self.i = data, 0

    def u8(self) -> int:
        self.i += 1
        return self.data[self.i - 1]

    def u16(self) -> int:
        self.i += 2
        return struct.unpack("!H", self.data[self.i - 2:self.i])[0]

    def bytes_(self) -> bytes:
        n = self.u16()
        self.i += n
        return self.data[self.i - n:self.i]

    def str_(self) -> str:
        return self.bytes_().decode("utf-8")

    def rest(self) -> bytes:
        return self.data[self.i:]

    @property
    def more(self) -> bool:
        return self.i < len(self.data)


def publish_packet(topic: str, payload: bytes, retain: bool = False) -> bytes:
    return _packet(PUBLISH, 1 if retain else 0, _str(topic) + payload)


# ---------------------------------------------------------------- broker
class _Session(socketserver.BaseRequestHandler):
    def setup(self) -> None:
        self.broker: MQTTBroker = self.server.broker  # type: ignore[attr-defined]
        self.subs: dict[str, int] = {}
        self.client_id = "?"
        self.out: queue.Queue[bytes | None] = queue.Queue(maxsize=2000)
        self.alive = True

    def send(self, data: bytes) -> None:
        try:
            self.out.put_nowait(data)
        except queue.Full:  # 느린 구독자는 메시지를 버린다 (QoS 0)
            self.broker.stats["dropped"] += 1

    def _writer(self) -> None:
        while True:
            data = self.out.get()
            if data is None:
                return
            try:
                self.request.sendall(data)
            except OSError:
                self.alive = False
                return

    def handle(self) -> None:
        rf = self.request.makefile("rb")
        try:
            first = _read_packet(rf)
            if not first or first[0] != CONNECT:
                return
            rc, keepalive = self._connect(_Reader(first[2]))
            self.request.sendall(_packet(CONNACK, 0, bytes([0, rc])))
            if rc != 0:
                return
            if keepalive:
                self.request.settimeout(keepalive * 1.5)
            threading.Thread(target=self._writer, daemon=True).start()
            self.broker._add(self)
            while self.alive:
                pkt = _read_packet(rf)
                if pkt is None:
                    return
                if not self._dispatch(*pkt):
                    return
        except (OSError, ValueError, IndexError, UnicodeDecodeError, struct.error):
            return
        finally:
            self.broker._remove(self)
            self.out.put(None)

    def _connect(self, r: _Reader) -> tuple[int, int]:
        proto, level, flags, keepalive = r.str_(), r.u8(), r.u8(), r.u16()
        if (proto, level) not in (("MQTT", 4), ("MQIsdp", 3)):
            return 1, 0  # unacceptable protocol version (MQTT 5 미지원)
        self.client_id = r.str_() or f"anon-{id(self) & 0xFFFF:x}"
        if flags & 0x04:  # will
            r.str_()
            r.bytes_()
        user = r.str_() if flags & 0x80 else None
        password = r.bytes_().decode("utf-8", "replace") if flags & 0x40 else None
        if self.broker.username is not None and (user != self.broker.username or password != self.broker.password):
            return 4, 0  # bad user name or password
        return 0, keepalive

    def _dispatch(self, ptype: int, flags: int, body: bytes) -> bool:
        r = _Reader(body)
        if ptype == PUBLISH:
            qos, retain = (flags >> 1) & 3, bool(flags & 1)
            topic = r.str_()
            pid = r.u16() if qos else None
            payload = r.rest()
            if "+" in topic or "#" in topic or not topic:
                return False
            self.broker._route(topic, payload, retain, self.client_id)
            if qos == 1:
                self.send(_packet(PUBACK, 0, struct.pack("!H", pid)))
            elif qos == 2:
                self.send(_packet(PUBREC, 0, struct.pack("!H", pid)))
        elif ptype == PUBREL:
            self.send(_packet(PUBCOMP, 0, body[:2]))
        elif ptype == SUBSCRIBE:
            pid, granted, filters = r.u16(), bytearray(), []
            while r.more:
                f, _qos = r.str_(), r.u8()
                self.subs[f] = 0
                granted.append(0)
                filters.append(f)
            self.send(_packet(SUBACK, 0, struct.pack("!H", pid) + bytes(granted)))
            for topic, payload in self.broker.retained_matching(filters):
                self.send(publish_packet(topic, payload, retain=True))
        elif ptype == UNSUBSCRIBE:
            pid = r.u16()
            while r.more:
                self.subs.pop(r.str_(), None)
            self.send(_packet(UNSUBACK, 0, struct.pack("!H", pid)))
        elif ptype == PINGREQ:
            self.send(_packet(PINGRESP, 0))
        elif ptype == DISCONNECT:
            return False
        return True

    def wants(self, topic: str) -> bool:
        return any(topic_matches(f, topic) for f in self.subs)


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class MQTTBroker:
    def __init__(self, host: str = "127.0.0.1", port: int = 1883, username: str | None = None,
                 password: str | None = None, on_publish: OnPublish | None = None):
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.on_publish = on_publish
        self.retained: dict[str, bytes] = {}
        self._sessions: set[_Session] = set()
        self._lock = threading.Lock()
        self._server: _Server | None = None
        self.stats = {"in": 0, "out": 0, "dropped": 0}

    def start(self, port_tries: int = 10) -> int:
        for p in range(self.port, self.port + port_tries):
            try:
                self._server = _Server((self.host, p), _Session)
                break
            except OSError:
                continue
        if self._server is None:
            raise OSError(f"MQTT 포트 {self.port}-{self.port + port_tries - 1} 사용 불가")
        self._server.broker = self  # type: ignore[attr-defined]
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, name="mqtt-broker", daemon=True).start()
        return self.port

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()

    @property
    def url(self) -> str:
        return f"mqtt://{self.host}:{self.port}"

    @property
    def clients(self) -> list[str]:
        with self._lock:
            return sorted(s.client_id for s in self._sessions)

    def _add(self, s: _Session) -> None:
        with self._lock:
            self._sessions.add(s)

    def _remove(self, s: _Session) -> None:
        with self._lock:
            self._sessions.discard(s)

    def retained_matching(self, filters: list[str]) -> list[tuple[str, bytes]]:
        with self._lock:
            return [(t, p) for t, p in self.retained.items() if any(topic_matches(f, t) for f in filters)]

    def _route(self, topic: str, payload: bytes, retain: bool, origin: str) -> None:
        self.stats["in"] += 1
        self._fanout(topic, payload, retain)
        if self.on_publish:
            try:
                self.on_publish(topic, payload, origin)
            except Exception as e:  # 장치 하나의 잘못된 데이터가 브로커를 멈추지 않게
                print(f"[mqtt] on_publish error ({topic}): {e}")

    def _fanout(self, topic: str, payload: bytes, retain: bool) -> None:
        with self._lock:
            if retain:
                if payload:
                    self.retained[topic] = payload
                else:
                    self.retained.pop(topic, None)
            targets = [s for s in self._sessions if s.wants(topic)]
        if targets:
            pkt = publish_packet(topic, payload)
            for s in targets:
                s.send(pkt)
                self.stats["out"] += 1

    def publish(self, topic: str, payload: bytes | str | dict, retain: bool = False) -> None:
        """브로커 내부(트윈)에서 바깥 장치들에게 발행."""
        if isinstance(payload, dict):
            payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if isinstance(payload, str):
            payload = payload.encode()
        self._fanout(topic, payload, retain)


# ---------------------------------------------------------------- client
class MQTTClient:
    """예제 장치 / 테스트용 최소 클라이언트 (QoS 0/1 발행, QoS 0 구독)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 1883, client_id: str = "qeaai-client",
                 username: str | None = None, password: str | None = None, keepalive: int = 30):
        self.host, self.port, self.client_id = host, port, client_id
        self.username, self.password, self.keepalive = username, password, keepalive
        self.on_message: Callable[[str, bytes], None] | None = None
        self._sock: socket.socket | None = None
        self._pid = 0
        self._acks: dict[int, threading.Event] = {}
        self._wlock = threading.Lock()
        self._running = False

    def connect(self, timeout: float = 5.0) -> None:
        self._sock = socket.create_connection((self.host, self.port), timeout=timeout)
        flags = 0x02
        payload = _str(self.client_id)
        if self.username is not None:
            flags |= 0x80
            payload += _str(self.username)
            if self.password is not None:
                flags |= 0x40
                payload += _str(self.password)
        body = _str("MQTT") + bytes([4, flags]) + struct.pack("!H", self.keepalive) + payload
        self._sock.sendall(_packet(CONNECT, 0, body))
        rf = self._sock.makefile("rb")
        pkt = _read_packet(rf)
        if not pkt or pkt[0] != CONNACK or pkt[2][1] != 0:
            raise ConnectionError(f"MQTT CONNACK 거부: {pkt[2][1] if pkt else 'no response'}")
        self._sock.settimeout(None)
        self._running = True
        threading.Thread(target=self._reader, args=(rf,), daemon=True).start()
        threading.Thread(target=self._pinger, daemon=True).start()

    def _send(self, data: bytes) -> None:
        with self._wlock:
            self._sock.sendall(data)

    def _next_pid(self) -> int:
        self._pid = self._pid % 65535 + 1
        return self._pid

    def _reader(self, rf) -> None:
        try:
            while self._running:
                pkt = _read_packet(rf)
                if pkt is None:
                    break
                ptype, flags, body = pkt
                if ptype == PUBLISH:
                    r = _Reader(body)
                    topic = r.str_()
                    if (flags >> 1) & 3:
                        r.u16()
                    if self.on_message:
                        self.on_message(topic, r.rest())
                elif ptype in (SUBACK, PUBACK, UNSUBACK):
                    ev = self._acks.pop(struct.unpack("!H", body[:2])[0], None)
                    if ev:
                        ev.set()
        except OSError:
            pass
        self._running = False

    def _pinger(self) -> None:
        stop = threading.Event()
        while self._running and not stop.wait(self.keepalive / 2):
            try:
                self._send(_packet(PINGREQ, 0))
            except OSError:
                return

    def subscribe(self, filt: str, timeout: float = 5.0) -> None:
        pid = self._next_pid()
        ev = self._acks[pid] = threading.Event()
        self._send(_packet(SUBSCRIBE, 2, struct.pack("!H", pid) + _str(filt) + b"\x00"))
        if not ev.wait(timeout):
            raise TimeoutError("SUBACK timeout")

    def publish(self, topic: str, payload: bytes | str | dict, qos: int = 0, retain: bool = False, timeout: float = 5.0) -> None:
        if isinstance(payload, dict):
            payload = json.dumps(payload, ensure_ascii=False)
        if isinstance(payload, str):
            payload = payload.encode()
        if qos == 0:
            self._send(publish_packet(topic, payload, retain))
            return
        pid = self._next_pid()
        ev = self._acks[pid] = threading.Event()
        flags = (1 << 1) | (1 if retain else 0)
        self._send(_packet(PUBLISH, flags, _str(topic) + struct.pack("!H", pid) + payload))
        if not ev.wait(timeout):
            raise TimeoutError("PUBACK timeout")

    def disconnect(self) -> None:
        self._running = False
        if self._sock:
            try:
                self._send(_packet(DISCONNECT, 0))
            except OSError:
                pass
            self._sock.close()
