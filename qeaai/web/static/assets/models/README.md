# 기체 3D 모델 (glTF 2.0)

이 폴더에 `flyer.glb` 를 넣으면 VR 화면이 코드로 만든 기체 대신 이 모델을 사용합니다.
(서버 재시작 후 브라우저 새로고침, 엔진 배지에 `glTF: flyer.glb` 로 표시)

## Blender 에서 내보내기

1. 단위: 미터 (Scene Properties → Units → Metric, Unit Scale 1.0). 기체 길이 약 10 m, 날개폭 약 14 m.
2. 방향: 기수가 **-Y** (Blender Front 뷰에서 보는 쪽), 위쪽이 **+Z**. 원점은 무게중심.
   - glTF 내보내기의 `+Y Up` 변환을 거치면 Three.js 에서 기수 +Z, 위 +Y 가 되어 엔진 규약과 맞습니다.
3. 재질: Principled BSDF (Base Color / Metallic / Roughness / Normal / Emission) → glTF PBR 로 그대로 변환됩니다.
   항법등·엔진처럼 빛나야 하는 부분은 Emission Strength 를 5 이상으로 두면 블룸이 걸립니다.
4. File → Export → glTF 2.0 → Format: **glTF Binary (.glb)**, Include: Selected Objects, Transform: **+Y Up** 체크.

Substance Painter, Maya, 3ds Max, Unity / Unreal (glTF exporter) 에서 만든 .glb 도 같은 규약이면 동작합니다.
