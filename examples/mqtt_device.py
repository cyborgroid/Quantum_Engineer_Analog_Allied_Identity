"""MQTT 장치 예제 - 트윈에 실제 장치를 붙이는 방법.

QEAAI OS(./run.sh)를 켠 상태에서:

    python3 examples/mqtt_device.py --id desk-lamp --kind actuator
    python3 examples/mqtt_device.py --id room --kind env_sensor --prop temp_c=23.5 --prop humidity_pct=40
    python3 examples/mqtt_device.py --id rover --kind robot --x -90 --y 30      # VR 안 위치 지정

장치는 1 초마다 상태를 qeaai/things/<id>/state 로 보내고, qeaai/things/<id>/set 을 구독해서
Allied Identity 에이전트(또는 MCP 로 Claude)가 보내는 명령을 실행한다.
사용자는 말하지 않는다: 주먹을 1 초 유지하면 켜고, 손을 펴서 1 초 유지하면 에이전트가 끈다.

같은 일을 mosquitto 로 하면:
    mosquitto_pub -h 127.0.0.1 -t qeaai/things/room/state -m '{"kind":"env_sensor","temp_c":23.5}'
    mosquitto_sub -h 127.0.0.1 -t 'qeaai/twin/#' -v
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qeaai.iot.mqtt import MQTTClient  # noqa: E402


def parse_prop(text: str) -> tuple[str, object]:
    key, _, value = text.partition("=")
    try:
        return key, float(value)
    except ValueError:
        return key, value


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--id", default="example-device")
    ap.add_argument("--kind", default="mqtt_device")
    ap.add_argument("--prop", action="append", default=[], help="고정 속성 key=value (여러 번 가능)")
    ap.add_argument("--x", type=float)
    ap.add_argument("--y", type=float)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--user")
    ap.add_argument("--password")
    args = ap.parse_args()

    state = {"on": False, "last_intent": "rest"}

    def on_message(topic: str, payload: bytes) -> None:
        try:
            cmd = json.loads(payload)
        except json.JSONDecodeError:
            return
        if topic.endswith("/set") and isinstance(cmd, dict):
            if "on" in cmd:
                state["on"] = bool(cmd["on"])
            print(f"[{args.id}] 에이전트 명령 실행: {cmd} -> on={state['on']}", flush=True)
        elif topic == "qeaai/neural/intent":
            state["last_intent"] = cmd.get("intent", state["last_intent"])

    fixed = dict(parse_prop(p) for p in args.prop)
    if args.x is not None and args.y is not None:
        fixed.update(x=args.x, y=args.y)
    started = time.time()
    try:
        while True:  # 브로커가 재시작돼도 다시 접속 (실제 장치처럼)
            client = MQTTClient(args.host, args.port, client_id=args.id, username=args.user, password=args.password)
            client.on_message = on_message
            try:
                client.connect()
                client.subscribe(f"qeaai/things/{args.id}/set")
                client.subscribe("qeaai/neural/intent")
                print(f"[{args.id}] 연결됨 {args.host}:{args.port}. Ctrl+C 로 종료", flush=True)
                while True:
                    payload = {"kind": args.kind, **fixed, "on": state["on"], "last_intent": state["last_intent"],
                               "uptime_s": round(time.time() - started)}
                    client.publish(f"qeaai/things/{args.id}/state", payload, qos=1)
                    time.sleep(args.interval)
            except (OSError, TimeoutError, ConnectionError) as e:
                print(f"[{args.id}] 연결 끊김 ({e}), 3초 후 재접속", flush=True)
                client.disconnect()
                time.sleep(3)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
