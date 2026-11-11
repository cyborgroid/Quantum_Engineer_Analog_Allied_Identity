"""실제 환경: 실제 시각의 태양 위치 + 실제 날씨 (Open-Meteo, API 키 불필요).

    - 태양 고도/방위: 천문 알고리즘으로 위치·시각에서 계산 (네트워크 불필요)
    - 날씨: https://open-meteo.com  (10 분마다 갱신, 실패하면 마지막 값 유지)

개인정보: 날씨를 받으려면 설정한 좌표가 Open-Meteo 로 전송된다. 기본값은 서울 시청이고,
--no-weather 로 끌 수 있다. 사용자의 실제 위치를 자동으로 알아내지 않는다.
"""

from __future__ import annotations

import json
import math
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
CURRENT_FIELDS = (
    "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,"
    "cloud_cover,pressure_msl,wind_speed_10m,wind_direction_10m,wind_gusts_10m"
)
WMO_KO = {
    0: "맑음", 1: "대체로 맑음", 2: "구름 조금", 3: "흐림", 45: "안개", 48: "서리 안개",
    51: "약한 이슬비", 53: "이슬비", 55: "강한 이슬비", 56: "어는 이슬비", 57: "어는 이슬비",
    61: "약한 비", 63: "비", 65: "강한 비", 66: "어는 비", 67: "어는 비",
    71: "약한 눈", 73: "눈", 75: "강한 눈", 77: "싸락눈",
    80: "약한 소나기", 81: "소나기", 82: "강한 소나기", 85: "눈 소나기", 86: "강한 눈 소나기",
    95: "뇌우", 96: "우박 뇌우", 99: "강한 우박 뇌우",
}


@dataclass(frozen=True)
class Location:
    name: str = "서울 시청"
    lat: float = 37.5665
    lon: float = 126.9780


def sun_position(lat: float, lon: float, unix_time: float | None = None) -> dict:
    """태양 고도/방위각 [deg]. 방위는 북=0, 동=90 (시계 방향). 정확도 약 0.5도."""
    t = time.time() if unix_time is None else unix_time
    d = t / 86400.0 + 2440587.5 - 2451545.0  # J2000 기준 일수
    g = math.radians((357.529 + 0.98560028 * d) % 360)
    q = (280.459 + 0.98564736 * d) % 360
    lam = math.radians((q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) % 360)
    eps = math.radians(23.439 - 0.00000036 * d)
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    dec = math.asin(math.sin(eps) * math.sin(lam))
    gmst_h = (18.697374558 + 24.06570982441908 * d) % 24
    ha = math.radians((gmst_h * 15 + lon) % 360) - ra
    phi = math.radians(lat)
    elev = math.asin(math.sin(phi) * math.sin(dec) + math.cos(phi) * math.cos(dec) * math.cos(ha))
    az = math.atan2(-math.sin(ha), math.tan(dec) * math.cos(phi) - math.sin(phi) * math.cos(ha))
    return {"elevation_deg": round(math.degrees(elev), 2), "azimuth_deg": round(math.degrees(az) % 360, 2)}


class WeatherService:
    def __init__(self, location: Location, interval_s: float = 600.0, enabled: bool = True):
        self.location = location
        self.interval_s = interval_s
        self.enabled = enabled
        self.current: dict | None = None
        self.utc_offset_s: int | None = None
        self.fetched_at: float | None = None
        self.error: str | None = None
        self._stop = threading.Event()

    def url(self) -> str:
        q = {"latitude": self.location.lat, "longitude": self.location.lon, "current": CURRENT_FIELDS,
             "wind_speed_unit": "ms", "timezone": "auto"}
        return f"{OPEN_METEO_URL}?{urllib.parse.urlencode(q)}"

    def fetch_once(self, timeout: float = 8.0) -> bool:
        req = urllib.request.Request(self.url(), headers={"User-Agent": "qeaai-digital-twin/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.load(r)
            cur = data["current"]
            cur["description"] = WMO_KO.get(int(cur.get("weather_code", -1)), "알 수 없음")
            self.current = cur
            self.utc_offset_s = int(data.get("utc_offset_seconds", 0))
            self.fetched_at = time.time()
            self.error = None
            return True
        except (OSError, ValueError, KeyError) as e:
            self.error = f"{type(e).__name__}: {e}"
            return False

    def start(self) -> None:
        if not self.enabled:
            return

        def loop() -> None:
            while not self._stop.is_set():
                ok = self.fetch_once()
                self._stop.wait(self.interval_s if ok else 60.0)

        threading.Thread(target=loop, name="weather", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()


class RealEnvironment:
    def __init__(self, location: Location | None = None, weather: bool = True):
        self.location = location or Location()
        self.weather = WeatherService(self.location, enabled=weather)

    def start(self) -> None:
        self.weather.start()

    def wind_vector(self) -> tuple[float, float]:
        """바람이 '불어가는' 방향의 (동, 북) 성분 [m/s]. 기상 풍향은 '불어오는' 방향."""
        w = self.weather.current
        if not w:
            return 0.0, 0.0
        speed, frm = float(w.get("wind_speed_10m", 0.0)), math.radians(float(w.get("wind_direction_10m", 0.0)))
        return -speed * math.sin(frm), -speed * math.cos(frm)

    def snapshot(self) -> dict:
        now = time.time()
        offset = self.weather.utc_offset_s
        if offset is None:
            offset = -time.timezone if not time.localtime().tm_isdst else -time.altzone
        local = time.gmtime(now + offset)
        w = self.weather
        return {
            "location": asdict(self.location),
            "local_time": time.strftime("%Y-%m-%d %H:%M", local),
            "utc_offset_h": offset / 3600,
            "sun": sun_position(self.location.lat, self.location.lon, now),
            "weather": w.current,
            "weather_age_s": round(now - w.fetched_at) if w.fetched_at else None,
            "weather_enabled": w.enabled,
            "weather_error": w.error,
        }
