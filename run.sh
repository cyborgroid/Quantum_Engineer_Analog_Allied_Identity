#!/usr/bin/env bash
# QEAAI OS 시각화 실행기
#   ./run.sh               기본 포트 8765 (사용 중이면 다음 포트 자동 선택)
#   PORT=9000 ./run.sh     포트 지정
#   ./run.sh --seed 3      다른 사용자(근육 특성)로 페어링
#
# 실제 세계 연결 (디지털 트윈):
#   ./run.sh --place "부산" --lat 35.1796 --lon 129.0756   실제 위치 (날씨·태양 위치)
#   ./run.sh --mqtt-bind 0.0.0.0                           같은 네트워크의 실제 장치(ESP32 등) 받기
#   QEAAI_MQTT_USER=u QEAAI_MQTT_PASSWORD=p ./run.sh ...   MQTT 인증
#   ./run.sh --no-weather / --no-sensors / --no-mqtt / --offline
#
# 실행 후 VS Code 에서: Ctrl+Shift+P -> "Open Integrated Browser" -> 출력된 주소 입력
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "python3 를 찾을 수 없습니다. Python 3.10 이상을 설치하세요." >&2
  exit 1
fi
if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  echo "Python 3.10 이상이 필요합니다: $("$PYTHON" --version)" >&2
  exit 1
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8765}"

echo "======================================================"
echo " QEAAI - Quantum Engineer Analog Allied Identity"
echo "======================================================"
exec "$PYTHON" -m qeaai serve --host "$HOST" --port "$PORT" "$@"
