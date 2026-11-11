#!/usr/bin/env bash
# QEAAI OS 시각화 실행기
#   ./run.sh               기본 포트 8765 (사용 중이면 다음 포트 자동 선택)
#   PORT=9000 ./run.sh     포트 지정
#   ./run.sh --seed 3      다른 사용자(근육 특성)로 페어링
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
