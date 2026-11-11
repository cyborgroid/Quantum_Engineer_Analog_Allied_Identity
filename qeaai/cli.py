"""qeaai 명령행.

    qeaai demo                 # 페어링된 본인이 착용 -> 근육으로 비행
    qeaai demo --impostor      # 다른 사람이 착용 -> Allied Identity 가 제어 거부
    qeaai demo --live          # 실시간 대시보드 애니메이션
    qeaai battery              # SNSS_Battery 소재/셀/팩 특성과 양자 커패시턴스
    qeaai identity             # 페어링 정확도와 타인 거부 평가
    qeaai serve                # 브라우저 OS 화면 (VR / Game / OS 시각화)
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from .bio.emg import GESTURES, EMGSimulator, UserPhysiology, extract_features
from .circuit.power import Load, PowerBus
from .identity.allied_identity import AlliedIdentity, collect_calibration
from .materials.quantum import capacitance_breakdown
from .materials.snss_battery import SNSSPack
from .system.kernel import DEMO_SCRIPT, QEAAIKernel


def cmd_demo(args: argparse.Namespace) -> int:
    kernel = QEAAIKernel.build_default(seed=args.seed, wearer_seed=args.seed + 999 if args.impostor else None)
    if args.save_profile:
        kernel.identity.save(args.save_profile)
        print(f"Allied Identity 프로필 저장: {args.save_profile}")

    every = max(1, round(1.0 / kernel.dt))
    frames = []
    for gesture, seconds in DEMO_SCRIPT:
        for _ in range(max(1, round(seconds / kernel.dt))):
            frame = kernel.tick(gesture)
            frames.append(frame)
            if args.live:
                sys.stdout.write("\x1b[2J\x1b[H" + kernel.render(frame) + "\n")
                sys.stdout.flush()
                time.sleep(kernel.dt)
            elif len(frames) % every == 0:
                d = frame.decision
                ai = "brownout" if d is None else f"{d.intent:<11} {'CTRL' if d.trusted else 'LOCK'} trust={d.trust:4.2f}"
                print(f"t={frame.t:5.1f}s  {gesture:<11} -> {ai}  alt={frame.flight.altitude_m:6.1f}m"
                      f"  SOC={frame.power.state_of_charge*100:5.1f}%")

    last = frames[-1]
    decided = [f for f in frames if f.decision]
    correct = sum(f.decision.intent == f.truth for f in decided)
    print()
    print(kernel.render(last))
    print()
    print(f"의도 해석 정확도 : {correct}/{len(decided)} ({100*correct/max(len(decided),1):.1f}%)")
    print(f"최고 고도        : {max(f.flight.altitude_m for f in frames):.1f} m")
    print(f"제어권 유지 비율 : {100*sum(f.decision.trusted for f in decided)/max(len(decided),1):.1f}%")
    print(f"네트워크 메시지  : {kernel.bus.delivered} delivered / {kernel.bus.rejected} rejected")
    return 0


def cmd_battery(args: argparse.Namespace) -> int:
    pack = SNSSPack.charged(cells_in_series=args.series, strings_in_parallel=args.parallel)
    cell = pack.cell
    print("== SNSS_Battery 셀 (그래핀-폴리머 / 0.9% NaCl / PVA, EDLC 모델) ==")
    print(f"전극 유효 면적      : {cell.electrode.area_m2:.1f} m^2 (전극당 {cell.electrode.mass_mg:.0f} mg)")
    print(f"전해질 전도도       : {cell.electrolyte.conductivity:.2f} S/m")
    print(f"ESR                 : {cell.esr:.2f} ohm")
    print(f"용량 C(0V) / C(0.8V): {cell.capacitance(0):.3f} F / {cell.capacitance(0.8):.3f} F")
    print(f"저장 에너지 (0.8V)  : {cell.stored_energy(0.8)*1000:.1f} mJ")
    print()
    print("== QE: 그래핀 양자 커패시턴스 (전극 전위별) ==")
    print("  V_e[V]   C_q[uF/cm2]  C_dl[uF/cm2]  C_tot[uF/cm2]  양자 제한(C_tot/C_dl)")
    for v in (0.0, 0.05, 0.1, 0.2, 0.3, 0.4):
        b = capacitance_breakdown(v, cell.electrode.edl_capacitance_f_per_m2, cell.electrolyte.temperature_k)
        print(f"  {v:5.2f}   {b['c_quantum']*100:10.2f}  {b['c_edl']*100:11.2f}  {b['c_total']*100:12.2f}  {b['quantum_limit']:10.2f}")
    print()
    print(f"== 팩 {args.series}S{args.parallel}P ==")
    print(f"전압 {pack.voltage:.2f} V, ESR {pack.esr:.2f} ohm, 에너지 {pack.full_energy:.3f} J")
    bus = PowerBus(pack)
    for name, w in (("저전력 대기", 200e-6), ("손목밴드 평균", 1.25e-3), ("디스플레이+무선 상시", 5e-3)):
        rt = bus.runtime_estimate_s(w)
        print(f"  {name:<18} {w*1e3:5.2f} mW -> 약 {rt/60:6.1f} 분")
    return 0


def cmd_identity(args: argparse.Namespace) -> int:
    owner = UserPhysiology.random("owner", args.seed)
    ai = AlliedIdentity()
    try:
        ai.decide([0.0] * 12)
    except RuntimeError as e:
        print(f"페어링 전: {e}")
    ai.pair("owner", collect_calibration(EMGSimulator(owner, seed=args.seed + 1)))
    print(f"페어링 완료: owner, 인증 임계 거리 {ai.auth_threshold:.2f}")

    def stream(phys: UserPhysiology, seed: int, n: int = 30):
        sim = EMGSimulator(phys, seed=seed)
        for g in GESTURES:
            sim.set_gesture(g)
            for _ in range(n):
                yield g, extract_features(sim.read())

    test = list(stream(owner, args.seed + 2))
    print(f"본인 의도 해석 정확도: {100*ai.evaluate(test):.1f}%")

    for k in range(args.impostors):
        other = UserPhysiology.random(f"other{k}", args.seed + 1000 + k)
        ai.trust = 1.0
        windows = [f for g, f in stream(other, args.seed + 3000 + k, 10) if g != "rest"]
        auth = sum(ai.decide(f).authenticated for f in windows)
        print(f"타인 #{k}: 창 인증률 {100*auth/len(windows):5.1f}%, 최종 trust {ai.trust:.2f} -> {'제어 허용(!)' if ai.trust >= ai.TRUST_THRESHOLD else '제어 거부'}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .web.server import serve

    from .iot.environment import Location
    from .iot.realworld import RealWorld, RealWorldConfig

    realworld = None
    if not args.offline:
        cfg = RealWorldConfig(
            location=Location(args.place, args.lat, args.lon),
            weather=not args.no_weather,
            sensors=not args.no_sensors,
            mqtt=not args.no_mqtt,
            mqtt_host=args.mqtt_bind,
            mqtt_port=args.mqtt_port,
            mqtt_user=os.environ.get("QEAAI_MQTT_USER"),
            mqtt_password=os.environ.get("QEAAI_MQTT_PASSWORD"),
        )
        if cfg.mqtt_host not in ("127.0.0.1", "localhost", "::1") and cfg.mqtt_user is None:
            print("경고: MQTT 를 네트워크에 열었지만 인증이 없습니다. QEAAI_MQTT_USER / QEAAI_MQTT_PASSWORD 를 설정하세요.")
        realworld = RealWorld(cfg)
    serve(host=args.host, port=args.port, seed=args.seed, realworld=realworld)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qeaai", description="Quantum Engineer Analog Allied Identity")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("demo", help="근육 -> Allied Identity -> 비행 게임 전체 시스템 시뮬레이션")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--impostor", action="store_true", help="페어링되지 않은 사람이 착용")
    p.add_argument("--live", action="store_true", help="실시간 대시보드")
    p.add_argument("--save-profile", metavar="PATH", help="페어링된 Allied Identity 프로필을 JSON 으로 저장")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("battery", help="SNSS_Battery 특성")
    p.add_argument("--series", type=int, default=5)
    p.add_argument("--parallel", type=int, default=1)
    p.set_defaults(func=cmd_battery)

    p = sub.add_parser("identity", help="Allied Identity 페어링/신원 평가")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--impostors", type=int, default=5)
    p.set_defaults(func=cmd_identity)

    p = sub.add_parser("serve", help="브라우저 시각화 서버 (OS / VR / Game)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--seed", type=int, default=0)
    g = p.add_argument_group("실제 세계 연결 (디지털 트윈)")
    g.add_argument("--place", default=os.environ.get("QEAAI_PLACE", "서울 시청"), help="위치 이름")
    g.add_argument("--lat", type=float, default=float(os.environ.get("QEAAI_LAT", 37.5665)), help="위도")
    g.add_argument("--lon", type=float, default=float(os.environ.get("QEAAI_LON", 126.9780)), help="경도")
    g.add_argument("--no-weather", action="store_true", help="Open-Meteo 날씨 끄기 (좌표를 외부로 보내지 않음)")
    g.add_argument("--no-sensors", action="store_true", help="이 컴퓨터의 실측 센서 끄기")
    g.add_argument("--no-mqtt", action="store_true", help="MQTT 브로커 끄기")
    g.add_argument("--mqtt-bind", default=os.environ.get("QEAAI_MQTT_BIND", "127.0.0.1"),
                   help="MQTT 수신 주소 (같은 네트워크의 실제 장치를 받으려면 0.0.0.0)")
    g.add_argument("--mqtt-port", type=int, default=int(os.environ.get("QEAAI_MQTT_PORT", 1883)))
    g.add_argument("--offline", action="store_true", help="실제 세계 연결 전부 끄기 (순수 시뮬레이션)")
    p.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
