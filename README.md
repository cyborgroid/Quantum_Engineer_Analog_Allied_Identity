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

### 그래픽 스택

| 구분 | 사용 기술 | 역할 |
|---|---|---|
| 3D 엔진 | **Three.js r160** (WebGL2) | 실시간 렌더링, 씬 그래프 |
| 조명 | Preetham 대기 산란 **Sky** + **PMREM IBL** + 방향광 PCF 소프트 그림자 | 시간대에 따른 하늘색, 환경 반사, 그림자 |
| 재질 | **PBR** (MeshStandard / MeshPhysical: metalness·roughness·clearcoat) | 기체 외피, 캐노피, 활주로, 지형 |
| 색 처리 | HDR → **ACES Filmic** 톤매핑 → sRGB | 영화·게임 엔진 표준 색 공간 |
| 후처리 | EffectComposer → **UnrealBloom** → OutputPass | 항법등, 배기, 활주로 등화, 비콘의 발광 |
| 에셋 | **glTF 2.0** (`assets/models/flyer.glb`) | Blender, Maya, Substance 등에서 만든 모델을 그대로 사용 ([안내](qeaai/web/static/assets/models/README.md)) |
| 차트 | **Chart.js 4.4** | 전원 기록, 양자 커패시턴스 곡선 |
| 서체 | **Pretendard** (한글 UI) + **JetBrains Mono** (수치) | |

- **카메라:** 추적 / 조종석 / 자유 궤도(마우스 드래그로 회전, 휠로 확대). **트윈 레이어** 버튼으로 그리드, 비콘 범위, 데이터 링크, 비행 궤적을 켜고 끕니다.
- **오프라인 동작:** 라이브러리는 jsDelivr CDN에서 버전을 고정해 불러옵니다. 인터넷이 없거나 WebGL을 쓸 수 없으면 내장 Canvas 2D 렌더러와 Canvas 차트로 자동 전환합니다.

조작: **W** 주먹(스로틀↑) · **S** 펴기(스로틀↓) · **A / D** 손목 좌/우(선회). 방향키나 화면 버튼도 같습니다.
직접 조작하면 자동 데모가 멈춥니다. 상단의 **타인 착용**을 누르면 Allied Identity가 제어를 잠그고 비행체가 fail-safe로 감속합니다.

## 실제 세계 연결: IoT와 디지털 트윈

VR 화면은 시뮬레이션만 보여주지 않습니다. **실제로 측정한 장치와 환경**이 같은 트윈에 들어오고, 화면과 카드에는 **REAL / SIM** 배지로 구분해서 표시합니다.

| 트윈 장치 | 출처 | 실제 / 가상 | VR 표현 |
|---|---|---|---|
| `edge-gateway` | 이 컴퓨터의 sysfs/procfs (CPU·SSD 온도, CPU 사용률, 메모리, 배터리, 전력, 네트워크) | **REAL** | 관제 건물. 상태 띠 색은 CPU 온도, 밤에는 창문 조명 |
| `weather` | [Open-Meteo](https://open-meteo.com) 실시간 관측 (기온, 습도, 풍향·풍속, 운량, 기압) | **REAL** | 기상 마스트와 풍향 자루 |
| MQTT 장치 | `qeaai/things/<id>/...` 로 접속한 실제 장치 | **REAL** | 기둥과 실시간 값 이름표 |
| `band`, `flyer`, `beacon-*` | 시뮬레이션 (근전도 밴드, 비행체, 가상 비콘) | SIM | |

실제 환경은 시뮬레이션에 다음과 같이 영향을 줍니다.
- **태양:** 위치와 현재 시각으로 계산한 실제 태양 고도·방위로 하늘을 그립니다. 낮과 밤, 노을, 별, 달빛이 실제 시간을 따라갑니다.
- **구름:** 실제 운량만큼 구름이 생기고, 흐리면 햇빛이 약해집니다.
- **바람:** 실제 바람이 비행체를 밀어냅니다(대지속도 = 대기속도 + 풍속). HUD에 `WIND · REAL`로 표시합니다.
- **기온:** 실제 기온이 손목밴드 SNSS 전해질 온도에 반영됩니다(피부 33°C와 외기의 혼합). 그래서 이온전도도, ESR, 양자 커패시턴스가 달라집니다.

### MQTT (IoT 표준 프로토콜)

`./run.sh`를 실행하면 MQTT 3.1.1 브로커가 함께 뜹니다(`mqtt://127.0.0.1:1883`). ESP32, 스마트폰 MQTT 앱, Node-RED, Home Assistant, mosquitto 같은 도구가 그대로 접속할 수 있습니다.

| 방향 | 토픽 | 내용 |
|---|---|---|
| 장치 → 트윈 | `qeaai/things/<id>/state` | JSON 객체 `{"kind":"env_sensor","temp_c":23.4}` (`x`, `y`를 넣으면 VR 안 위치 지정, 단위 m) |
| 장치 → 트윈 | `qeaai/things/<id>/<속성>` | 단일 값 `23.4`, `true` |
| 트윈 → 밖 | `qeaai/twin/<id>/state` (retained) | 트윈에 있는 모든 장치의 최신 상태 |
| 트윈 → 밖 | `qeaai/neural/intent` (retained) | Allied Identity가 해석한 근육 의도. 실제 액추에이터를 근육으로 제어할 때 씀 |

```bash
python3 examples/mqtt_device.py --id room --kind env_sensor --prop temp_c=23.5   # 파이썬 예제 장치
mosquitto_pub -t qeaai/things/room/state -m '{"temp_c":23.5}'                     # mosquitto
mosquitto_sub -t 'qeaai/twin/#' -v                                                # 트윈 전체 구독
```

실제 하드웨어용 예제는 [examples/esp32_thing/esp32_thing.ino](examples/esp32_thing/esp32_thing.ino)에 있습니다. 센서값을 보내고, 근육 의도 `fist`/`open`을 받아 LED를 켜고 끕니다. 같은 Wi-Fi의 장치를 받으려면 `./run.sh --mqtt-bind 0.0.0.0`으로 실행하고, `QEAAI_MQTT_USER` / `QEAAI_MQTT_PASSWORD`로 인증을 거세요.

### 디지털 트윈 표준: W3C WoT Thing Description

`http://127.0.0.1:8765/api/things`는 트윈의 모든 장치를 [W3C WoT TD 1.1](https://www.w3.org/TR/wot-thing-description11/)(JSON-LD) 형식으로 내보냅니다. 각 속성에 HTTP(`/api/things/<id>/properties/<p>`)와 MQTT 접근 경로를 함께 적어 두기 때문에, node-wot이나 Eclipse Ditto 같은 트윈 도구가 장치를 발견해서 읽을 수 있습니다.

### 위치, 개인정보, 보안
- **위치:** 기본값은 **서울 시청**이며, 사용자의 실제 위치를 자동으로 알아내지 않습니다. `./run.sh --place "집" --lat .. --lon ..`으로 지정하세요.
- **날씨:** 날씨를 받으려면 그 좌표가 Open-Meteo로 전송됩니다. `--no-weather`로 끌 수 있습니다.
- **MQTT:** 기본 설정은 이 컴퓨터에서만 접속할 수 있습니다(`127.0.0.1`). 네트워크에 열 때는 인증을 설정하세요. 암호화(TLS)가 필요하면 앞단에 TLS 프록시를 두세요.
- **완전 오프라인:** `--offline`이면 실제 세계 연결을 모두 끄고 순수 시뮬레이션으로 동작합니다.

## 명세 재검토 반영: 소재 · 설계 · OS=VR · Allied Identity · VPN

### SNSS 소재 = 제품 (그래핀 폴리머 **액정**)
"그래핀 폴리머 액정"은 **그래핀 산화물(GO) 액정**으로 해석했습니다. GO 조각은 일정 농도를 넘으면 스스로 정렬하는 액정이 됩니다(Onsager 이론, 기본 조각 기준 약 0.8 mg/mL). 3D 펜 노즐을 지날 때 생기는 전단으로 결이 더 정렬되고, 비타민C로 환원하면 결 방향으로 전기가 잘 통하는 rGO가 됩니다([snss_material.py](qeaai/materials/snss_material.py)).

같은 소재 계열을 조성만 바꿔 부품마다 씁니다.

| 부품 | 조성 / 공정 | 계산 결과 (기본값) |
|---|---|---|
| 직렬 배선 | 배선 잉크 GO 20 mg/mL · PVA 0.5%, 3D 펜 압출 후 환원 | 40 mm 배선 **8.7 Ω**, 이방성 6.7배 (같은 배선을 겔 잉크로 그리면 1.3 kΩ이라 쓸 수 없음) |
| 피부 전자 타투 근전도 전극 | 1 µm 미만 그래핀 + PVA·식염수 겔 계면 | 27 kΩ, 잡음 **3.2 µV** (50 µm 두께 전극은 28 µV) |
| 셀 / 변형 센서 / 섀시 내층 | 겔 잉크 GO 6 mg/mL · PVA 4% · 0.9% 식염수 | 셀 모델, 게이지 팩터 8 |

이 값들은 시뮬레이션에 그대로 들어갑니다. 전자 타투 잡음은 근전도 잡음 바닥으로, 밴드 배선 저항은 팩 ESR로, 설계된 셀 면적은 셀 모델로 연결됩니다.

### 3D 펜 손목밴드 설계
```bash
python3 -m qeaai design --wrist 165        # design_out/ 에 템플릿 SVG, 전자 타투 시트 SVG, 지표 JSON 생성
```
- `band_template.svg`를 **배율 100%** 로 인쇄하고, 번호 순서대로 PCL 저온 3D 펜으로 따라 그립니다(12획, 약 5분, PCL 2.2 g). 60 °C 물에 담가 손목 곡률로 굽힙니다.
- 빨간 점선은 SNSS 배선 잉크 경로입니다. 위 채널은 모듈 → 셀 직렬, 아래 채널은 귀선이라 서로 겹치지 않습니다(단락 검사 포함).
- `etattoo_electrodes.svg`는 전완 4채널 근전도 전극의 위치와 serpentine 메쉬입니다. 피부가 늘어나도 끊어지지 않는 패턴입니다.
- 대시보드의 **DESIGN · CHASSIS** 패널에서 손목 둘레를 바꾸면 바로 다시 설계됩니다.

### OS가 VR이 된다 (WebXR)
- VR 화면 오른쪽의 **ENTER VR** 버튼으로 실제 헤드셋에 들어갑니다(헤드셋이 없으면 `VR NOT SUPPORTED`로 표시). 조종석에 앉으면 OS 패널(FLIGHT / ALLIED IDENTITY / POWER·SNSS / REAL WORLD·TWIN)이 계기판으로 떠 있습니다.
- **VR 컨트롤러 입력:** 트리거 = 주먹, 그립 = 펴기, 스틱 좌우 = 손목 좌우.
- **Quest 단독 브라우저:** `adb reverse tcp:8765 tcp:8765`로 연결하고 헤드셋에서 `http://localhost:8765`를 엽니다(WebXR은 localhost나 HTTPS에서만 동작). PC VR(Link)은 PC 브라우저에서 바로 됩니다.
- **헤드셋 없이:** **몰입 OS** 버튼을 누르면 조종석 시점과 전체 화면으로 같은 경험을 할 수 있습니다.

### Allied Identity 에이전트: "그것이 너이고, 네가 컨트롤 한다"
음성 비서가 아니라, 근육 신호와 실제 환경을 보고 **스스로 제어하는 AI**입니다([allied_agent.py](qeaai/agent/allied_agent.py)).
- **근육 → 실제 장치:** 주먹을 **1초 유지**하면 MQTT 액추에이터를 켜고, 손을 펴서 1초 유지하면 끕니다. 명령은 `qeaai/things/<id>/set`으로 갑니다.
- **자율 판단:**
  - 타인이 착용하면 모든 액추에이터를 차단합니다.
  - SOC가 30% 아래면 절전합니다(무선 송신 5%, 화면 0.3 mW).
  - 실제로 밤이면 화면을 어둡게 합니다.
  - 실측 돌풍이 10 m/s를 넘으면 경고합니다.
- **페르소나:** 주로 쓰는 동작, 동작별 힘, 활동 시간대, 비행 성향을 계속 학습하고 `data/allied_persona.json`에 누적합니다("AI가 나 자신을 나타내게").
- **Claude가 직접 제어(MCP):** 프로젝트 루트의 [.mcp.json](.mcp.json)에 MCP 서버가 등록되어 있습니다. 이 폴더에서 Claude Code를 열면 `qeaai` 도구(`get_status`, `list_things`, `get_thing`, `set_device`, `control`, `get_persona`, `design_band`)가 생기고, Claude가 트윈을 읽고 실제 장치를 제어할 수 있습니다. 먼저 `./run.sh`를 실행해 두세요.

### VPN (WireGuard)
```bash
python3 -m qeaai vpn --peer esp32-01 --peer phone      # vpn/ 에 설정 생성 (개인 키 포함, git 제외)
sudo cp vpn/wg-qeaai.conf /etc/wireguard/ && sudo wg-quick up wg-qeaai
./run.sh --mqtt-bind 10.77.0.1                          # MQTT 를 VPN 안에서만 열기
```
WireGuard 키를 가진 장치만 암호화된 터널로 트윈에 들어옵니다. 게이트웨이 트윈에 `vpn: up/down` 상태가 표시됩니다.

## 터미널 실행

```bash
python3 -m qeaai demo              # 페어링된 본인이 착용 → 근육 신호로 비행체 이륙·선회
python3 -m qeaai demo --impostor   # 다른 사람이 착용 → Allied Identity 가 제어 거부, 비행 불가
python3 -m qeaai demo --live       # 실시간 OS 대시보드
python3 -m qeaai battery           # SNSS_Battery 셀/팩 특성 + 그래핀 양자 커패시턴스 표
python3 -m qeaai identity          # 본인 인식 정확도 / 타인 거부 평가

pip install -e ".[dev]" && pytest  # 설치 후 `qeaai` 명령 사용 가능, 테스트 64개
```

외부 의존성 없이 Python 3.10 이상 표준 라이브러리만 사용합니다.

## 명세 → 코드 대응

| 명세 [구성] | 모듈 | 구현 내용 |
|---|---|---|
| **SNSS_Battery** (그래핀 폴리머 **액정** / 멸균 생리 식염수 / PVA) | [snss_battery.py](qeaai/materials/snss_battery.py), [snss_material.py](qeaai/materials/snss_material.py) | 셀/팩 모델 + **그래핀 산화물 액정** 소재 모델(Onsager 상전이, 3D 펜 압출 배향). 한 소재가 셀·배선·전자 타투 전극·변형 센서 역할을 함 |
| **QE** (양자화의 첫걸음) | [qeaai/materials/quantum.py](qeaai/materials/quantum.py) | 그래핀 **양자 커패시턴스** C_q(V). 디랙점 근처에서 전체 용량의 최대 ~92%를 제한하는 실제 양자 효과 |
| **근육 구동** ("물리 엔진 대신 근육") | [qeaai/bio/emg.py](qeaai/bio/emg.py), [qeaai/bio/harvester.py](qeaai/bio/harvester.py) | 전완 4채널 근전도(EMG) 시뮬레이터 + 근육 움직임 에너지 하베스터 |
| **Allied Identity** ("사람과 호환해야 완성") | [qeaai/identity/allied_identity.py](qeaai/identity/allied_identity.py) | 페어링 전에는 동작하지 않음. 그 사람의 근육 패턴으로 의도를 해석하고, 다른 사람이 착용하면 trust 가 떨어져 제어 거부. 사용하면서 계속 적응 |
| **Allied Identity** ("그것이 너이고, 네가 컨트롤") | [qeaai/agent/](qeaai/agent/) | 말이 아니라 근육과 실제 환경을 보고 **스스로 장치를 제어하는 에이전트** + 사용자 페르소나 학습 + Claude 용 **MCP 서버** |
| **Circuit** ("모든 디바이스 호환") | [qeaai/circuit/power.py](qeaai/circuit/power.py) | 팩 전압(4.0→1.0 V)을 DC-DC로 3.3 V로 변환, 부하 소비, 하베스터 충전, 전원 부족(brownout) 처리 |
| **Network** ("없으면 벙어리") | [qeaai/network/bus.py](qeaai/network/bus.py) | 토픽 기반 메시지 버스 + HMAC-SHA256 서명(위변조·사칭 차단) |
| **VPN / WIFI** | [qeaai/iot/vpn.py](qeaai/iot/vpn.py) | **WireGuard** 설정 생성: 게이트웨이 + 장치(ESP32·스마트폰). MQTT 를 VPN 안에서만 열기 |
| **VR** = IoT + 디지털 트윈 | [qeaai/twin/digital_twin.py](qeaai/twin/digital_twin.py) | `device/*/state` 토픽을 구독해서 모든 디바이스 상태를 실시간 미러링, VR 뷰 렌더링 |
| **Game** ("조종하면 이미 날 수 있다") | [qeaai/game/flight.py](qeaai/game/flight.py) | 주먹=스로틀↑, 펴기=스로틀↓, 손목 좌/우=뱅크. 제어권이 없으면 fail-safe |
| **OS** ("보고 경험하는 것 모두") | [qeaai/system/kernel.py](qeaai/system/kernel.py) | 위 전부를 200 ms 틱 루프로 묶고 화면(대시보드)으로 보여주는 커널 |
| **OS 가 VR 이 된다** | [vr3d.js](qeaai/web/static/vr3d.js) | **WebXR**: 실제 VR 헤드셋으로 진입. OS 패널이 3D 조종석 계기판이 되고 VR 컨트롤러가 근육 입력 대용 |
| **Design & Chassis** ("3D 펜으로 만드는 개체") | [qeaai/design/band.py](qeaai/design/band.py) | 손목 둘레로 SNSS 밴드 설계 → **1:1 인쇄 템플릿**(3D 펜 획 순서), 단락 없는 직렬 배선, 전자 타투 전극 시트 |
| **IoT / 디지털 트윈** (VR = IoT + 디지털 트윈) | [qeaai/iot/](qeaai/iot/) | MQTT 3.1.1 브로커·클라이언트, 이 컴퓨터의 실측 센서, 실제 날씨·태양 위치, W3C WoT Thing Description |
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
5. **"생물 자체에 한 땀 한 땀 새겨 넣는다"**: 피부에 붙이는 **그래핀 전자 타투**(두께 1 µm 미만, 피부 주름을 따라 밀착하는 근전도 전극)와, 밴드 직물에 전도성 실로 **꿰매 넣는 배선**으로 구현합니다([snss_material.py](qeaai/materials/snss_material.py), [band.py](qeaai/design/band.py)). 몸 안에 넣는 것은 아닙니다. 피부 접촉 부위에는 5 V 이하의 직류만 쓰고, 상처 난 피부에는 붙이지 않으며, 전해질(식염수 겔)은 밀봉합니다.

## 로드맵

| 단계 | 내용 | 코드에서 교체할 부분 |
|---|---|---|
| 0 ✅ | 소프트웨어 디지털 트윈 (현재) | — |
| 1 | SNSS 셀 벤치 제작 → CV·GCD·EIS 측정 | `Electrode` / `Electrolyte` / `CellGeometry` 파라미터를 측정값에 맞춤 |
| 2 | 시판 sEMG 센서 손목밴드 | `EMGSimulator` 대신 `read(duration_s)` 를 가진 센서 드라이버 (`SignalSource` 프로토콜) |
| 3 | 3D 펜 섀시 + 셀 직렬 배선 + DC-DC 보드 | `DCDCConverter` / `Load` 를 실측 소비전력으로 교체 |
| 4 ✅ | MQTT 연결, 실물 IoT 트윈 (이 컴퓨터, 실제 날씨, MQTT 장치) | 실제 장치는 `qeaai/things/<id>/state` 로 발행 |
| 5 | VR 헤드셋/3D 렌더러로 트윈 시각화 | `DigitalTwin.scene()` 을 렌더러로 전달 |
