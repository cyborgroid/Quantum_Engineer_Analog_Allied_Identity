"""MCP 서버 - "네가 컨트롤 한다": Claude 가 QEAAI 트윈을 직접 읽고 제어한다.

Model Context Protocol (JSON-RPC 2.0 over stdio). 실행 중인 QEAAI 서버(./run.sh)에 HTTP 로 붙는다.
프로젝트 루트의 .mcp.json 에 등록되어 있어서 Claude Code 를 이 폴더에서 열면 'qeaai' 도구가 생긴다.

    QEAAI_URL   기본 http://127.0.0.1:8765
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

from .. import __version__

BASE = os.environ.get("QEAAI_URL", "http://127.0.0.1:8765").rstrip("/")
PROTOCOL = "2025-06-18"

TOOLS = [
    {"name": "get_status", "description": "QEAAI 현재 상태 요약: 근육 의도, Allied Identity 신뢰도, SNSS 전원, 비행, 실제 환경(위치·날씨·태양), 에이전트 모드.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_things", "description": "디지털 트윈의 모든 장치 (실측 REAL / 가상 SIM 구분, 주요 값).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_thing", "description": "장치 하나의 W3C WoT Thing Description 과 현재 속성.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "set_device", "description": "실제 MQTT 장치에 명령 (qeaai/things/<id>/set). 예: {\"id\":\"desk-lamp\",\"properties\":{\"on\":true}}",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}, "properties": {"type": "object"},
                                                      "reason": {"type": "string"}}, "required": ["id", "properties"]}},
    {"name": "control", "description": "근육 입력과 같은 제어: gesture(rest|fist|open|wrist_left|wrist_right), mode(demo|manual), wearer(owner|impostor), recharge(true).",
     "inputSchema": {"type": "object", "properties": {
         "gesture": {"type": "string", "enum": ["rest", "fist", "open", "wrist_left", "wrist_right"]},
         "mode": {"type": "string", "enum": ["demo", "manual"]},
         "wearer": {"type": "string", "enum": ["owner", "impostor"]},
         "recharge": {"type": "boolean"}}}},
    {"name": "get_persona", "description": "Allied Identity 가 학습한 사용자 특성(페르소나)과 최근 자율 행동 기록.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "design_band", "description": "손목 둘레(mm)로 3D 펜 SNSS 손목밴드 설계 지표 계산.",
     "inputSchema": {"type": "object", "properties": {"wrist_mm": {"type": "number"}}}},
]


def _http(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def _summary() -> dict:
    s = _http("GET", "/api/state")
    d, p, f, e = s.get("decision") or {}, s["power"], s["flight"], s.get("env") or {}
    w = e.get("weather") or {}
    return {
        "t": s["t"], "mode": s["mode"], "wearer": s["wearer"],
        "intent": d.get("intent"), "trusted": d.get("trusted"), "trust": round(d.get("trust", 0), 2),
        "soc_pct": round(p["soc"] * 100, 1), "pack_v": round(p["pack_v"], 2), "brownout": p["brownout"],
        "flight": {k: round(f[k], 1) if isinstance(f[k], float) else f[k] for k in ("altitude_m", "speed_mps", "heading_deg", "throttle", "airborne")},
        "place": (e.get("location") or {}).get("name"), "local_time": e.get("local_time"),
        "sun_elevation_deg": (e.get("sun") or {}).get("elevation_deg"),
        "weather": {"desc": w.get("description"), "temp_c": w.get("temperature_2m"), "wind_mps": w.get("wind_speed_10m")} if w else None,
        "agent": {k: v for k, v in (s.get("agent") or {}).items() if k != "log"},
    }


def call_tool(name: str, args: dict):
    if name == "get_status":
        return _summary()
    if name == "list_things":
        twin = _http("GET", "/api/state")["twin"]
        skip = {"id", "kind", "source", "real", "age_s"}
        return [{"id": d["id"], "kind": d["kind"], "reality": "REAL" if d["real"] else "SIM",
                 "values": {k: v for k, v in d.items() if k not in skip}} for d in twin]
    if name == "get_thing":
        tid = args["id"]
        return {"td": _http("GET", f"/api/things/{tid}"), "properties": _http("GET", f"/api/things/{tid}/properties")}
    if name == "set_device":
        return _http("POST", "/api/agent/command", {"device": args["id"], "set": args["properties"],
                                                    "reason": args.get("reason", "Claude (MCP)")})
    if name == "control":
        return _http("POST", "/api/control", {k: v for k, v in args.items() if k in ("gesture", "mode", "wearer", "recharge")})
    if name == "get_persona":
        return _http("GET", "/api/agent")
    if name == "design_band":
        return _http("GET", f"/api/design/band.json?wrist={float(args.get('wrist_mm', 165))}")
    raise ValueError(f"unknown tool {name}")


def handle(msg: dict) -> dict | None:
    mid, method = msg.get("id"), msg.get("method")
    if mid is None:  # notification
        return None
    try:
        if method == "initialize":
            ver = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL
            result = {"protocolVersion": ver, "capabilities": {"tools": {}},
                      "serverInfo": {"name": "qeaai", "version": __version__},
                      "instructions": "QEAAI 디지털 트윈. 먼저 get_status / list_things 로 상황을 보고, 실제 장치는 set_device 로 제어한다."}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = msg.get("params") or {}
            try:
                out = call_tool(params.get("name", ""), params.get("arguments") or {})
                result = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=1)}]}
            except urllib.error.URLError as e:
                result = {"content": [{"type": "text", "text": f"QEAAI 서버({BASE})에 연결할 수 없습니다. ./run.sh 를 먼저 실행하세요. ({e})"}], "isError": True}
            except (ValueError, KeyError) as e:
                result = {"content": [{"type": "text", "text": f"오류: {e}"}], "isError": True}
        elif method == "ping":
            result = {}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    except Exception as e:  # 서버가 죽지 않도록
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
