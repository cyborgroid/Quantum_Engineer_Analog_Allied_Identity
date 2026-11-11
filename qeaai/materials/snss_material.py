"""SNSS 소재 = 제품: 그래핀 (산화물) 폴리머 액정 + PVA + 멸균 생리 식염수.

명세: "그래핀 폴리머 액정, 멸균 생리 식염수, PVA 로 구현", "소재를 소재로만 보지 않고 제품으로 쓸 수 있음",
      "SNSS_Battery 소재로 모든 걸 빌드업", "생물 자체에 한 땀 한 땀 새겨 넣는다", "3D 펜으로 만드는 개체".

하나의 잉크가 공정에 따라 다른 부품이 된다:
    액정 잉크 --(3D 펜 압출: 전단 배향)--> 배향 필름/섬유
        --(비타민C 환원: 친환경 rGO)--> 전도성 배선 · 바느질 실 · 셀 전극
        --(초박막 전사)-------------> 피부 전자 타투 근전도 전극
        --(PVA+식염수 겔)------------> 셀 전해질 · 변형 센서 · 피부와 비슷한 탄성의 섀시 내층

물리 모델 (문헌 범위의 근사):
    * 액정 상전이: 얇은 판상 입자의 Onsager / Bates-Frenkel 이론  phi_I ~ 3.3 t/D,  phi_N ~ 4.4 t/D
    * 배향: 노즐 벽 전단율 8v/d 로 질서도 S 증가 (전단 정렬)
    * 전도: rGO 2D 충전재 퍼콜레이션  sigma = sigma_rGO ((f - f_c)/(1 - f_c))^2, 배향 방향 이방성
    * 전자 타투: 피부-전극 임피던스 + 열잡음 + 움직임 잡음(두께 의존) -> 근전도 잡음 바닥
"""

from __future__ import annotations

import math
from dataclasses import dataclass

BOLTZMANN = 1.380649e-23
GO_DENSITY_MG_PER_ML = 1800.0  # 1.8 g/cm^3
PVA_DENSITY_MG_PER_ML = 1300.0


@dataclass(frozen=True)
class SNSSInk:
    """3D 펜 / 바느질 / 전사 공정에 쓰는 SNSS 잉크 조성."""

    go_mg_per_ml: float = 6.0  # 그래핀 산화물 농도
    flake_um: float = 10.0  # 조각 지름 D
    flake_nm: float = 1.0  # 조각 두께 t (단층)
    pva_wt_percent: float = 4.0
    nacl_wt_percent: float = 0.9  # 멸균 생리 식염수

    # --- 액정 ---------------------------------------------------------------
    @property
    def aspect(self) -> float:
        return (self.flake_nm * 1e-3) / self.flake_um  # t / D

    @property
    def volume_fraction(self) -> float:
        return self.go_mg_per_ml / GO_DENSITY_MG_PER_ML

    @property
    def phi_isotropic(self) -> float:
        return 3.3 * self.aspect

    @property
    def phi_nematic(self) -> float:
        return 4.4 * self.aspect

    @property
    def phase(self) -> str:
        phi = self.volume_fraction
        if phi < self.phi_isotropic:
            return "isotropic"
        if phi < self.phi_nematic:
            return "biphasic"
        return "nematic"

    @property
    def nematic_onset_mg_per_ml(self) -> float:
        return self.phi_nematic * GO_DENSITY_MG_PER_ML

    def order_parameter(self, shear_rate_s: float = 0.0) -> float:
        """질서도 S (0 무질서 ~ 1 완전 정렬). 평형값 + 압출 전단 정렬."""
        phi, pn, pi = self.volume_fraction, self.phi_nematic, self.phi_isotropic
        if phi < pi:
            s_eq = 0.0
        elif phi < pn:
            s_eq = 0.4 * (phi - pi) / (pn - pi)  # 공존 영역
        else:
            s_eq = min(0.85, 0.4 + 0.45 * (1 - pn / phi))
        # 액정일 때만 전단으로 쉽게 정렬된다 (등방성 분산액은 약하게)
        susceptibility = 1.0 if self.phase == "nematic" else 0.3 if self.phase == "biphasic" else 0.05
        s_shear = (1 - s_eq) * (1 - math.exp(-shear_rate_s / 60.0)) * susceptibility
        return min(0.95, s_eq + s_shear)

    # --- 전도 (환원 후) -------------------------------------------------------
    def solid_rgo_fraction(self) -> float:
        """건조 후 고체 중 rGO 부피 분율 (환원 시 산소기 제거로 질량 약 60% 남음)."""
        rgo = self.go_mg_per_ml * 0.6 / GO_DENSITY_MG_PER_ML
        pva = self.pva_wt_percent * 10 / PVA_DENSITY_MG_PER_ML  # wt% -> mg/mL ~ x10
        return rgo / (rgo + pva) if rgo + pva > 0 else 0.0

    def conductivity(self, order: float, rgo_s_per_m: float = 2.0e4, f_c: float = 0.01) -> tuple[float, float]:
        """(배향 방향, 직각 방향) 전도도 [S/m]."""
        f = self.solid_rgo_fraction()
        if f <= f_c:
            return 0.0, 0.0
        base = rgo_s_per_m * ((f - f_c) / (1 - f_c)) ** 2
        return base * (1 + 2 * order), base * (1 - 0.6 * order)  # 배향 rGO 필름 이방성 ~2-10 배

    # --- 공정 ---------------------------------------------------------------
    @staticmethod
    def extrusion_shear(nozzle_mm: float = 0.7, speed_mm_s: float = 10.0) -> float:
        """3D 펜 노즐 벽 전단율 [1/s] ~ 8 v / d."""
        return 8 * speed_mm_s / nozzle_mm


@dataclass(frozen=True)
class Trace:
    """3D 펜으로 그리거나 실로 꿰맨 SNSS 배선."""

    length_mm: float
    width_mm: float = 1.0
    thickness_um: float = 20.0

    def resistance(self, ink: SNSSInk, order: float, along: bool = True) -> float:
        sp, sx = ink.conductivity(order)
        sigma = sp if along else sx
        if sigma <= 0:
            return math.inf
        return (self.length_mm * 1e-3) / (sigma * self.width_mm * 1e-3 * self.thickness_um * 1e-6)


@dataclass(frozen=True)
class ETattooElectrode:
    """피부에 '새겨 넣는' 그래핀 전자 타투 근전도 전극."""

    area_cm2: float = 1.0
    thickness_um: float = 0.5  # 1 um 미만이면 피부 주름에 밀착
    hydrogel_interface: bool = True  # PVA-식염수 겔 계면

    def impedance_kohm(self, freq_hz: float = 100.0) -> float:
        """피부-전극 임피던스 크기 [kOhm] (R_skin || C_interface 근사)."""
        conform = 1.0 / (1.0 + self.thickness_um / 2.0)  # 두꺼울수록 밀착 불량
        r_spec = (30.0 if self.hydrogel_interface else 80.0) / conform  # kOhm*cm^2
        c_spec = 0.05e-6 * conform  # F/cm^2 : 각질층(stratum corneum) 정전용량이 지배
        r = r_spec / self.area_cm2 * 1e3
        xc = 1 / (2 * math.pi * freq_hz * c_spec * self.area_cm2)
        z = 1 / math.sqrt((1 / r) ** 2 + (1 / xc) ** 2)
        return z / 1e3

    def noise_uv_rms(self, bandwidth_hz: float = 480.0, temp_k: float = 306.0, amp_uv: float = 1.5) -> float:
        """근전도 잡음 바닥 [uV rms] = 열잡음 + 증폭기 + 움직임 잡음(밀착도 의존)."""
        thermal = math.sqrt(4 * BOLTZMANN * temp_k * self.impedance_kohm() * 1e3 * bandwidth_hz) * 1e6
        motion = 4.0 * math.sqrt(max(self.thickness_um, 0.1))
        return math.sqrt(thermal**2 + amp_uv**2 + motion**2)


@dataclass(frozen=True)
class StrainGauge:
    """PVA-식염수-rGO 하이드로겔 변형 센서 (손목 굽힘)."""

    gauge_factor: float = 8.0
    r0_ohm: float = 2000.0

    def resistance(self, strain: float) -> float:
        return self.r0_ohm * (1 + self.gauge_factor * strain)


# 같은 소재 계열, 기능별 조성: 겔 잉크(전해질·전극·센서·섀시 내층) / 배선 잉크(GO 많고 PVA 적음)
GEL_INK = SNSSInk()
TRACE_INK = SNSSInk(go_mg_per_ml=20.0, pva_wt_percent=0.5)
POWER_TRACE = Trace(length_mm=40.0, width_mm=2.0, thickness_um=100.0)


def material_report(ink: SNSSInk | None = None, nozzle_mm: float = 0.7, speed_mm_s: float = 10.0) -> dict:
    """한 잉크가 각 부품으로 쓰일 때의 성능 요약 (기본: 배선 잉크, 비교로 겔 잉크)."""
    ink = ink or TRACE_INK
    shear = ink.extrusion_shear(nozzle_mm, speed_mm_s)
    s0, s1 = ink.order_parameter(0.0), ink.order_parameter(shear)
    sp, sx = ink.conductivity(s1)
    trace = POWER_TRACE
    gel_r = trace.resistance(GEL_INK, GEL_INK.order_parameter(shear))
    tattoo = ETattooElectrode()
    thick = ETattooElectrode(thickness_um=50.0, hydrogel_interface=False)
    return {
        "ink": {
            "go_mg_per_ml": ink.go_mg_per_ml, "phase": ink.phase,
            "nematic_onset_mg_per_ml": round(ink.nematic_onset_mg_per_ml, 2),
            "order_rest": round(s0, 3), "order_extruded": round(s1, 3), "shear_rate_s": round(shear, 1),
        },
        "trace": {
            "sigma_along_s_m": round(sp, 1), "sigma_across_s_m": round(sx, 1),
            "anisotropy": round(sp / sx, 2) if sx else None,
            "r_40mm_along_ohm": round(trace.resistance(ink, s1, True), 1),
            "r_40mm_across_ohm": round(trace.resistance(ink, s1, False), 1),
            "r_40mm_if_gel_ink_ohm": round(gel_r, 1),
            "geometry_mm": [trace.length_mm, trace.width_mm, trace.thickness_um / 1000],
        },
        "etattoo": {
            "impedance_kohm_100hz": round(tattoo.impedance_kohm(), 1),
            "noise_uv_rms": round(tattoo.noise_uv_rms(), 2),
            "thick_electrode_noise_uv_rms": round(thick.noise_uv_rms(), 2),
        },
        "strain_gauge": {"gauge_factor": StrainGauge().gauge_factor, "r_at_10pct_ohm": StrainGauge().resistance(0.10)},
    }


def default_emg_noise_floor_mv() -> float:
    """밴드의 전자 타투 전극이 만드는 근전도 잡음 바닥 [mV] -> EMG 시뮬레이터에 연결."""
    return ETattooElectrode().noise_uv_rms() / 1000.0
