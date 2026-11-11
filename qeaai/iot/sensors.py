"""실측 장치: 이 컴퓨터를 '엣지 게이트웨이' IoT 장치로 - Linux sysfs / procfs.

손목밴드(Allied Identity)가 붙는 실제 게이트웨이의 센서를 실시간으로 읽는다.
값을 읽을 수 없는 항목(데스크톱의 배터리 등)은 None 으로 남기고 건너뛴다.
"""

from __future__ import annotations

import os
import socket
import time
from pathlib import Path

SYS = Path("/sys/class")


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _num(path: Path, scale: float = 1.0) -> float | None:
    v = _read(path)
    try:
        return float(v) * scale if v is not None else None
    except ValueError:
        return None


def _hwmon(name: str) -> Path | None:
    for h in (SYS / "hwmon").glob("hwmon*"):
        if _read(h / "name") == name:
            return h
    return None


class HostSensors:
    """이 컴퓨터의 실측 센서. read() 를 주기적으로 호출한다 (변화율 계산 때문에)."""

    def __init__(self) -> None:
        self.hostname = socket.gethostname()
        self._prev_cpu: tuple[int, int] | None = None
        self._prev_net: tuple[float, int, int] | None = None
        self._coretemp = _hwmon("coretemp")
        self._nvme = _hwmon("nvme")
        self._bat = next(iter(sorted((SYS / "power_supply").glob("BAT*"))), None)
        self._ac = next((p for p in (SYS / "power_supply").glob("*") if _read(p / "type") == "Mains"), None)

    @property
    def available(self) -> bool:
        return Path("/proc/stat").exists()

    def _cpu_temp(self) -> float | None:
        if self._coretemp:
            for label in self._coretemp.glob("temp*_label"):
                if (_read(label) or "").startswith("Package"):
                    return _num(Path(str(label).replace("_label", "_input")), 1e-3)
            return _num(self._coretemp / "temp1_input", 1e-3)
        for z in (SYS / "thermal").glob("thermal_zone*"):
            if _read(z / "type") in ("x86_pkg_temp", "cpu-thermal", "TCPU"):
                return _num(z / "temp", 1e-3)
        return None

    def _cpu_load(self) -> float | None:
        line = _read(Path("/proc/stat"))
        if not line:
            return None
        fields = [int(x) for x in line.splitlines()[0].split()[1:]]
        idle, total = fields[3] + (fields[4] if len(fields) > 4 else 0), sum(fields)
        prev, self._prev_cpu = self._prev_cpu, (idle, total)
        if not prev or total == prev[1]:
            return None
        return 100.0 * (1 - (idle - prev[0]) / (total - prev[1]))

    def _mem_used(self) -> float | None:
        info = {}
        for line in (_read(Path("/proc/meminfo")) or "").splitlines():
            k, _, v = line.partition(":")
            info[k] = float(v.split()[0]) if v.split() else 0.0
        if "MemTotal" in info and "MemAvailable" in info:
            return 100.0 * (1 - info["MemAvailable"] / info["MemTotal"])
        return None

    def _default_iface(self) -> str | None:
        for line in (_read(Path("/proc/net/route")) or "").splitlines()[1:]:
            parts = line.split()
            if len(parts) > 2 and parts[1] == "00000000":
                return parts[0]
        return None

    def _net(self) -> tuple[str | None, float | None, float | None]:
        iface = self._default_iface()
        if not iface:
            return None, None, None
        rx = _num(SYS / "net" / iface / "statistics" / "rx_bytes")
        tx = _num(SYS / "net" / iface / "statistics" / "tx_bytes")
        if rx is None or tx is None:
            return iface, None, None
        now = time.monotonic()
        prev, self._prev_net = self._prev_net, (now, int(rx), int(tx))
        if not prev or now <= prev[0]:
            return iface, None, None
        dt = now - prev[0]
        return iface, (rx - prev[1]) * 8 / 1000 / dt, (tx - prev[2]) * 8 / 1000 / dt

    def _battery(self) -> dict:
        b = self._bat
        if not b:
            return {}
        power = _num(b / "power_now", 1e-6)
        if power is None:
            cur, volt = _num(b / "current_now", 1e-6), _num(b / "voltage_now", 1e-6)
            power = cur * volt if cur is not None and volt is not None else None
        return {
            "battery_pct": _num(b / "capacity"),
            "battery_status": _read(b / "status"),
            "battery_power_w": power,
            "battery_voltage_v": _num(b / "voltage_now", 1e-6),
        }

    def read(self) -> dict:
        iface, rx, tx = self._net()
        uptime = _read(Path("/proc/uptime"))
        state = {
            "kind": "edge_gateway",
            "host": self.hostname,
            "cpu_temp_c": self._cpu_temp(),
            "cpu_load_pct": self._cpu_load(),
            "mem_used_pct": self._mem_used(),
            "nvme_temp_c": _num(self._nvme / "temp1_input", 1e-3) if self._nvme else None,
            "ac_online": bool(_num(self._ac / "online")) if self._ac else None,
            **self._battery(),
            "net_iface": iface,
            "net_rx_kbps": rx,
            "net_tx_kbps": tx,
            "uptime_h": float(uptime.split()[0]) / 3600 if uptime else None,
            "load_avg_1m": os.getloadavg()[0] if hasattr(os, "getloadavg") else None,
        }
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in state.items() if v is not None}
