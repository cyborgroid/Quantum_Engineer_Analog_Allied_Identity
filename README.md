# QEAAI — Quantum Engineer Analog Allied Identity

> 작성인: 정구영 · 원 명세: [Quantum Engineer Analog Allied Identity.md](Quantum%20Engineer%20Analog%20Allied%20Identity.md)

명세서의 비전을 **실행 가능한 소프트웨어 디지털 트윈**으로 옮긴 프로젝트입니다.
하드웨어(SNSS_Battery 셀, 손목밴드, 3D 펜 섀시)를 만들기 전에 소재·회로·근육 신호·AI·OS·VR·게임·네트워크가
하나의 시스템으로 어떻게 맞물리는지 시뮬레이션하고, 나중에 실측값과 실제 센서로 한 부분씩 교체해 나가는 것이 목표입니다.

## 화면으로 보기 (OS / VR / Game)

```bash
./run.sh                 # http://127.0.0.1:8765/ (사용 중이면 다음 포트 자동 선택)
PORT=9000 ./run.sh       # 포트 지정
```

VS Code에서 `Ctrl+Shift+P` → **Open Integrated Browser** (없으면 **Simple Browser: Show**) → 터미널에 출력된 주소를 입력합니다.

| 패널 | 내용 |
|---|---|
| **VR · GAME** | 3인칭 3D 비행 뷰 + HUD(방위, 속도, 고도, 스로틀) + 트윈 맵. IoT 비콘이 범위 안에 들어오면 초록색으로 연결 표시 |
| **MUSCLE** | 4채널 근전도 오실로스코프, 근육 활성도, 동작 버튼 |
| **ALLIED IDENTITY** | 해석된 의도, 동작별 확률, 신뢰도(trust), CONTROL / LOCKED |
| **POWER** | SNSS 팩 충전 상태, 전압, 부하, 근육 발전량, 예상 구동 시간 |
| **QE** | 그래핀 양자 커패시턴스 곡선과 현재 셀 상태 |
| **IoT · DIGITAL TWIN / NETWORK** | 버스로 미러링된 디바이스 카드, 서명된 메시지 로그 |

조작: **W** 주먹(스로틀↑) · **S** 펴기(스로틀↓) · **A / D** 손목 좌/우(선회). 방향키나 화면 버튼도 같습니다.
직접 조작하면 자동 데모가 멈춥니다. 상단의 **타인 착용**을 누르면 Allied Identity가 제어를 잠그고 비행체가 fail-safe로 감속합니다.

## 터미널 실행

```bash
python3 -m qeaai demo              # 페어링된 본인이 착용 → 근육 신호로 비행체 이륙·선회
python3 -m qeaai demo --impostor   # 다른 사람이 착용 → Allied Identity 가 제어 거부, 비행 불가
python3 -m qeaai demo --live       # 실시간 OS 대시보드
python3 -m qeaai battery           # SNSS_Battery 셀/팩 특성 + 그래핀 양자 커패시턴스 표
python3 -m qeaai identity          # 본인 인식 정확도 / 타인 거부 평가

pip install -e ".[dev]" && pytest  # 설치 후 `qeaai` 명령 사용 가능, 테스트 31개
```

외부 의존성 없이 Python 3.10 이상 표준 라이브러리만 사용합니다.

## 명세 → 코드 대응

| 명세 [구성] | 모듈 | 구현 내용 |
|---|---|---|
| **SNSS_Battery** (그래핀 폴리머 / 멸균 생리 식염수 / PVA) | [qeaai/materials/snss_battery.py](qeaai/materials/snss_battery.py) | 전극 면적·전해질 전도도·분리막으로 용량, ESR, 에너지, 자가방전을 계산하는 셀/팩 모델 |
| **QE** (양자화의 첫걸음) | [qeaai/materials/quantum.py](qeaai/materials/quantum.py) | 그래핀 **양자 커패시턴스** C_q(V). 디랙점 근처에서 전체 용량의 최대 ~92%를 제한하는 실제 양자 효과 |
| **근육 구동** ("물리 엔진 대신 근육") | [qeaai/bio/emg.py](qeaai/bio/emg.py), [qeaai/bio/harvester.py](qeaai/bio/harvester.py) | 전완 4채널 근전도(EMG) 시뮬레이터 + 근육 움직임 에너지 하베스터 |
| **Allied Identity** ("사람과 호환해야 완성") | [qeaai/identity/allied_identity.py](qeaai/identity/allied_identity.py) | 페어링 전에는 동작하지 않음. 그 사람의 근육 패턴으로 의도를 해석하고, 다른 사람이 착용하면 trust 가 떨어져 제어 거부. 사용하면서 계속 적응 |
| **Circuit** ("모든 디바이스 호환") | [qeaai/circuit/power.py](qeaai/circuit/power.py) | 팩 전압(4.0→1.0 V)을 DC-DC로 3.3 V로 변환, 부하 소비, 하베스터 충전, 전원 부족(brownout) 처리 |
| **Network** ("없으면 벙어리") / VPN | [qeaai/network/bus.py](qeaai/network/bus.py) | 토픽 기반 메시지 버스 + HMAC-SHA256 서명(위변조·사칭 차단) |
| **VR** = IoT + 디지털 트윈 | [qeaai/twin/digital_twin.py](qeaai/twin/digital_twin.py) | `device/*/state` 토픽을 구독해서 모든 디바이스 상태를 실시간 미러링, VR 뷰 렌더링 |
| **Game** ("조종하면 이미 날 수 있다") | [qeaai/game/flight.py](qeaai/game/flight.py) | 주먹=스로틀↑, 펴기=스로틀↓, 손목 좌/우=뱅크. 제어권이 없으면 fail-safe |
| **OS** ("보고 경험하는 것 모두") | [qeaai/system/kernel.py](qeaai/system/kernel.py) | 위 전부를 200 ms 틱 루프로 묶고 화면(대시보드)으로 보여주는 커널 |
| **OS 화면 / VR 렌더링** | [qeaai/web/](qeaai/web/) | 커널 상태를 SSE로 스트리밍하는 서버(표준 라이브러리) + Canvas 3D 화면(외부 라이브러리 없음, 오프라인 동작) |

### 한 틱의 데이터 흐름

```
근육(EMG) ──┬─▶ 하베스터 ──▶ SNSS 팩 ◀── 디바이스 부하 (Circuit)
            │                   │
            └─▶ Allied Identity ─┤ (전원 부족이면 정지)
                 의도 + trust    │
                     │           ▼
                     ▼     Network (서명된 메시지)
                 비행 게임 ──────┤
                                 ▼
                         디지털 트윈 = VR ──▶ OS 화면
```

## 시뮬레이션 결과 요약 (기본값)

- **SNSS 셀** (전극 50 mg×2, 10 cm²): 0.8 V, 0.1–0.6 F (전압에 따라 다름), ESR 0.9 Ω, 149 mJ
- **5S 팩**: 4.0 V, 0.75 J → 손목밴드 평균 1.25 mW 부하로 **약 8분**
- **본인 의도 해석 정확도**: 100% · **타인 착용**: 5/5 명 모두 제어 거부, 이륙 불가

## 현실성 검토

명세의 방향은 유지하되, 물리적으로 확인된 범위 안에서 모델링했습니다. 하드웨어 단계에서 기대치를 맞추려면 아래 내용이 중요합니다.

1. **SNSS_Battery는 화학적으로 '배터리'가 아니라 슈퍼커패시터입니다.** 그래핀과 중성 식염수 사이에서는 산화환원 반응이 일어나지 않고, 전하는 전기이중층에 저장됩니다. 그래서 전압창이 약 0.8 V로 제한되고(물 분해), 에너지 밀도가 리튬 배터리보다 수십~수백 배 낮습니다. 대신 충방전이 빠르고 수명이 길며, 재료가 무해하고, 유연하게 만들 수 있습니다.
2. **"양자"는 소재 수준에서 실재합니다.** 그래핀 양자 커패시턴스는 측정할 수 있는 양자 효과이고, 이 프로젝트의 QE는 여기서 출발합니다. 반면 "양자 아날로그 OS"는 아직 개념 단계입니다. 현재 OS는 일반 디지털 소프트웨어입니다.
3. **근육 구동 에너지는 µW 단위입니다.** 손목 하베스터는 보통 수십~수백 µW를 내므로, 기기를 구동하기보다는 팩을 보조 충전하는 역할입니다(데모에서 직접 확인할 수 있습니다).
4. **"신경회로의 깊은 속내"**: 표면 근전도(sEMG)로 알 수 있는 것은 손·손목 근육의 활동 의도입니다. 뇌·신경 내부 상태는 읽을 수 없습니다.
5. **안전**: 명세의 "생물 자체에 새겨 넣는다"를 신체 이식으로 해석해서는 안 됩니다. 자작 전기화학 셀을 몸에 넣거나 피부 아래 두는 것은 감염, 전해질 누출, 화상 위험이 큽니다. 이 프로젝트는 **착용형(손목시계형) 외부 기기**를 전제로 합니다.

## 로드맵

| 단계 | 내용 | 코드에서 교체할 부분 |
|---|---|---|
| 0 ✅ | 소프트웨어 디지털 트윈 (현재) | — |
| 1 | SNSS 셀 벤치 제작 → CV·GCD·EIS 측정 | `Electrode` / `Electrolyte` / `CellGeometry` 파라미터를 측정값에 맞춤 |
| 2 | 시판 sEMG 센서 손목밴드 | `EMGSimulator` 대신 `read(duration_s)` 를 가진 센서 드라이버 (`SignalSource` 프로토콜) |
| 3 | 3D 펜 섀시 + 셀 직렬 배선 + DC-DC 보드 | `DCDCConverter` / `Load` 를 실측 소비전력으로 교체 |
| 4 | WiFi/MQTT 연결, 실물 IoT 트윈 | `Message.to_json()` / `NetworkBus.receive()` 에 전송 계층 연결 (TLS) |
| 5 | VR 헤드셋/3D 렌더러로 트윈 시각화 | `DigitalTwin.scene()` 을 렌더러로 전달 |
