"""VPN - 명세 [결과] "Software : 데이터 / VPN / WIFI".

WireGuard 설정을 만든다: 이 컴퓨터(게이트웨이) 1 + 장치(ESP32, 스마트폰, 다른 PC) N.
MQTT 브로커를 VPN 주소(10.77.0.1)에만 열면, WireGuard 키를 가진 장치만 암호화된 터널로 트윈에 들어온다.

    qeaai vpn --peer esp32-01 --peer phone          # vpn/ 에 설정 생성 (개인 키 포함, git 제외됨)
    sudo cp vpn/wg-qeaai.conf /etc/wireguard/ && sudo wg-quick up wg-qeaai
    ./run.sh --mqtt-bind 10.77.0.1

인터페이스 올리기(wg-quick up)는 관리자 권한이 필요해서 사용자가 직접 실행한다.
"""

from __future__ import annotations

import ipaddress
import os
import re
import shutil
import socket
import subprocess
from pathlib import Path

IFACE = "wg-qeaai"
PEER_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def _wg(*args: str, stdin: str | None = None) -> str:
    if not shutil.which("wg"):
        raise RuntimeError("WireGuard 도구(wg)가 없습니다: sudo apt install wireguard-tools")
    return subprocess.run(["wg", *args], input=stdin, capture_output=True, text=True, check=True).stdout.strip()


def keypair() -> tuple[str, str]:
    priv = _wg("genkey")
    return priv, _wg("pubkey", stdin=priv)


def lan_ip() -> str:
    """기본 경로의 로컬 IP (패킷은 보내지 않음)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # TEST-NET, 라우팅 조회만
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def generate(out_dir: str | Path, peers: list[str], endpoint: str | None = None, port: int = 51820,
             subnet: str = "10.77.0.0/24") -> dict:
    net = ipaddress.ip_network(subnet)
    hosts = list(net.hosts())
    if not peers or len(peers) > len(hosts) - 1:
        raise ValueError("peer 는 1 개 이상, 서브넷 크기 이하")
    for name in peers:
        if not PEER_NAME.match(name):
            raise ValueError(f"잘못된 peer 이름: {name!r}")
    endpoint = endpoint or lan_ip()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)

    gw_priv, gw_pub = keypair()
    gw_ip = hosts[0]
    server = [f"# QEAAI 게이트웨이 (이 컴퓨터) - {IFACE}", "[Interface]", f"Address = {gw_ip}/{net.prefixlen}",
              f"ListenPort = {port}", f"PrivateKey = {gw_priv}", ""]
    result = {"interface": IFACE, "gateway_ip": str(gw_ip), "endpoint": f"{endpoint}:{port}", "peers": []}
    for i, name in enumerate(peers, start=1):
        priv, pub = keypair()
        psk = _wg("genpsk")
        ip = hosts[i]
        server += [f"# {name}", "[Peer]", f"PublicKey = {pub}", f"PresharedKey = {psk}", f"AllowedIPs = {ip}/32", ""]
        peer = [f"# QEAAI 장치 {name}", "[Interface]", f"Address = {ip}/32", f"PrivateKey = {priv}", "",
                "[Peer]", f"PublicKey = {gw_pub}", f"PresharedKey = {psk}", f"Endpoint = {endpoint}:{port}",
                f"AllowedIPs = {gw_ip}/32", "PersistentKeepalive = 25", ""]
        path = out / f"peer-{name}.conf"
        path.write_text("\n".join(peer))
        os.chmod(path, 0o600)
        result["peers"].append({"name": name, "ip": str(ip), "config": str(path),
                                "mqtt": f"mqtt://{gw_ip}:1883"})
    server_path = out / f"{IFACE}.conf"
    server_path.write_text("\n".join(server))
    os.chmod(server_path, 0o600)
    result["server_config"] = str(server_path)
    return result


def status() -> dict:
    """VPN 인터페이스 상태 (게이트웨이 트윈에 들어간다)."""
    p = Path("/sys/class/net") / IFACE
    if not p.exists():
        return {"vpn": "down"}
    try:
        state = (p / "operstate").read_text().strip()
    except OSError:
        state = "unknown"
    return {"vpn": "up" if state in ("up", "unknown") else state}
