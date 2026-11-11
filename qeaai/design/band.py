"""Design & Chassis - "3D 프린팅이 아닌 3D 펜으로 만드는 개체", "손목시계 착용감".

손목 둘레를 넣으면 SNSS 손목밴드를 설계한다:
    * 셀 주머니 배치 (SNSS 셀 직렬 n 개) -> 셀 면적이 시뮬레이션 셀 모델로 들어간다
    * 셀 사이 직렬 배선: SNSS 배선 잉크를 3D 펜(주사기형)으로 그리거나 실로 꿰맨다 -> 저항이 팩 ESR 에 더해진다
    * 섀시: 저온 3D 펜(PCL, 60-80 C, 생분해성)으로 그리는 획 순서 가이드
    * 근전도 전자 타투: 전완근 4 채널 전극 위치와 serpentine(뱀 모양) 메쉬 패턴

출력은 1:1 축척 SVG 템플릿이다. 종이에 인쇄해서 그 위에 3D 펜으로 따라 그리면 된다
(3D 펜 공작의 표준 방법: 평면 부품을 템플릿 위에 그린 뒤 손목 곡률로 굽혀 마감).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import cached_property

from ..materials.snss_material import TRACE_INK, ETattooElectrode, SNSSInk, Trace

PCL_DENSITY_G_CM3 = 1.145
BUCKLE_MM = 20.0
CHANNEL_MM = 3.0  # 배선 채널 폭 (위/아래 가장자리)

Point = tuple[float, float]


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def area_cm2(self) -> float:
        return self.w * self.h / 100.0

    def loop(self) -> list[Point]:
        return [(self.x, self.y), (self.x + self.w, self.y), (self.x + self.w, self.y + self.h), (self.x, self.y + self.h), (self.x, self.y)]


def _plen(pts: list[Point]) -> float:
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


@dataclass(frozen=True)
class BandDesign:
    wrist_mm: float = 165.0
    width_mm: float = 32.0
    overlap_mm: float = 25.0
    cells: int = 5
    bead_mm: float = 1.0  # 3D 펜 비드 폭
    pen_speed_mm_s: float = 8.0
    trace_width_mm: float = 2.0
    trace_thickness_um: float = 100.0

    def __post_init__(self) -> None:
        if not 110 <= self.wrist_mm <= 260:
            raise ValueError("wrist_mm 은 110-260 mm 범위")
        if not 18 <= self.width_mm <= 50:
            raise ValueError("width_mm 은 18-50 mm 범위")
        if not 1 <= self.cells <= 10:
            raise ValueError("cells 는 1-10")

    @property
    def length_mm(self) -> float:
        return self.wrist_mm + self.overlap_mm

    # --- 배치 ---------------------------------------------------------------
    @cached_property
    def pockets(self) -> list[Rect]:
        usable_x0, usable_x1 = BUCKLE_MM, self.length_mm - self.overlap_mm
        pitch = (usable_x1 - usable_x0) / self.cells
        rib = 2 * self.bead_mm
        y0 = CHANNEL_MM + self.bead_mm
        h = self.width_mm - 2 * (CHANNEL_MM + self.bead_mm)
        return [Rect(usable_x0 + i * pitch + rib / 2, y0, pitch - rib, h) for i in range(self.cells)]

    @property
    def cell_footprint_cm2(self) -> float:
        return self.pockets[0].area_cm2

    @cached_property
    def traces(self) -> list[list[Point]]:
        """직렬 배선 (서로 겹치지 않음 = 단락 없음).
        위 채널: 모듈 -> 셀1(-), 셀i(+) -> 셀i+1(-)  /  아래 채널: 마지막 셀(+) -> 모듈 (귀선)."""
        top, bottom = CHANNEL_MM / 2, self.width_mm - CHANNEL_MM / 2
        ps = self.pockets
        paths = [[(BUCKLE_MM / 2, top), (ps[0].x + 3, top), (ps[0].x + 3, ps[0].y)]]
        for a, b in zip(ps, ps[1:]):
            paths.append([(a.x + a.w - 3, a.y), (a.x + a.w - 3, top), (b.x + 3, top), (b.x + 3, b.y)])
        last = ps[-1]
        paths.append([(last.x + last.w - 3, last.y + last.h), (last.x + last.w - 3, bottom), (BUCKLE_MM / 2, bottom)])
        return paths

    def traces_overlap(self) -> bool:
        """같은 채널에서 x 구간이 겹치는 배선이 있는지 (단락 검사)."""
        spans: dict[float, list[tuple[float, float]]] = {}
        for path in self.traces:
            for (x0, y0), (x1, y1) in zip(path, path[1:]):
                if y0 == y1:
                    spans.setdefault(round(y0, 3), []).append((min(x0, x1), max(x0, x1)))
        for segs in spans.values():
            segs.sort()
            if any(b0 < a1 - 1e-9 for (a0, a1), (b0, b1) in zip(segs, segs[1:])):
                return True
        return False

    def trace_resistance_ohm(self, ink: SNSSInk = TRACE_INK) -> float:
        order = ink.order_parameter(ink.extrusion_shear(0.7, self.pen_speed_mm_s))
        return sum(Trace(_plen(p), self.trace_width_mm, self.trace_thickness_um).resistance(ink, order) for p in self.traces)

    # --- 3D 펜 획 ------------------------------------------------------------
    @cached_property
    def strokes(self) -> list[tuple[str, list[Point]]]:
        """(이름, 경로) - 그리는 순서대로."""
        L, W, b = self.length_mm, self.width_mm, self.bead_mm
        out: list[tuple[str, list[Point]]] = [
            ("외곽", Rect(b / 2, b / 2, L - b, W - b).loop()),
            ("외곽 2겹", Rect(1.5 * b, 1.5 * b, L - 3 * b, W - 3 * b).loop()),
        ]
        for i, p in enumerate(self.pockets):
            out.append((f"셀 {i + 1} 테두리", Rect(p.x - b / 2, p.y - b / 2, p.w + b, p.h + b).loop()))
        # 버클/모듈 영역 채움 (지그재그)
        for name, x0, x1 in (("모듈부 채움", 2 * b, BUCKLE_MM - b), ("버클부 채움", L - self.overlap_mm + b, L - 2 * b)):
            zz, x, up = [], x0, True
            while x <= x1:
                zz += [(x, 2 * b), (x, W - 2 * b)] if up else [(x, W - 2 * b), (x, 2 * b)]
                x += b * 1.2
                up = not up
            out.append((name, zz))
        # 버클 구멍 테두리
        for k in range(3):
            cx = L - self.overlap_mm / 2 - 6 + k * 6
            out.append((f"버클 구멍 {k + 1}", [(cx + 1.5 * math.cos(t / 12 * 2 * math.pi), W / 2 + 1.5 * math.sin(t / 12 * 2 * math.pi)) for t in range(13)]))
        return out

    @cached_property
    def electrodes(self) -> list[dict]:
        """전자 타투 전극 (전완, 손목 주름에서 근위부로 mm). 근전도 채널 순서 = bio.emg.CHANNELS."""
        c = self.wrist_mm
        return [
            {"channel": "flexor", "x": 60.0, "y": 0.18 * c, "note": "손바닥 쪽 전완 (굴근)"},
            {"channel": "extensor", "x": 60.0, "y": 0.68 * c, "note": "손등 쪽 전완 (신근)"},
            {"channel": "radial", "x": 45.0, "y": 0.43 * c, "note": "엄지 쪽 (요측)"},
            {"channel": "ulnar", "x": 45.0, "y": 0.93 * c, "note": "새끼손가락 쪽 (척측)"},
        ]

    # --- 지표 ---------------------------------------------------------------
    def metrics(self) -> dict:
        stroke_len = sum(_plen(p) for _, p in self.strokes)
        bead_area_mm2 = math.pi * (self.bead_mm / 2) ** 2
        tattoo = ETattooElectrode()
        return {
            "length_mm": round(self.length_mm, 1),
            "width_mm": self.width_mm,
            "cells": self.cells,
            "cell_footprint_cm2": round(self.cell_footprint_cm2, 2),
            "pen_strokes": len(self.strokes),
            "pen_length_mm": round(stroke_len, 0),
            "pen_time_min": round(stroke_len / self.pen_speed_mm_s / 60, 1),
            "pcl_mass_g": round(stroke_len * bead_area_mm2 * 1e-3 * PCL_DENSITY_G_CM3, 2),
            "trace_length_mm": round(sum(_plen(p) for p in self.traces), 1),
            "trace_resistance_ohm": round(self.trace_resistance_ohm(), 2),
            "electrode_noise_uv": round(tattoo.noise_uv_rms(), 2),
            "electrode_impedance_kohm": round(tattoo.impedance_kohm(), 1),
        }

    # --- SVG ----------------------------------------------------------------
    def svg_template(self) -> str:
        L, W = self.length_mm, self.width_mm
        m = self.metrics()
        pad, title_h = 10.0, 46.0
        sw, sh = L + 2 * pad, W + 2 * pad + title_h
        o = []
        a = o.append
        a(f'<svg xmlns="http://www.w3.org/2000/svg" width="{sw}mm" height="{sh}mm" viewBox="0 0 {sw} {sh}" font-family="sans-serif">')
        a('<defs><marker id="arr" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="3" markerHeight="3" orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="#0b7d74"/></marker></defs>')
        a(f'<rect width="{sw}" height="{sh}" fill="#fff"/>')
        a(f'<g transform="translate({pad},{pad})">')
        a(f'<rect x="0" y="0" width="{L}" height="{W}" rx="4" fill="#f4f7f9" stroke="#9aa7b2" stroke-width="0.2"/>')
        a(f'<rect x="0" y="0" width="{BUCKLE_MM}" height="{W}" fill="#e3ecf2"/>')
        a(f'<text x="{BUCKLE_MM / 2}" y="{W / 2}" font-size="2.6" text-anchor="middle" fill="#456">모듈</text>')
        a(f'<text x="{L - self.overlap_mm / 2}" y="{W - 3}" font-size="2.4" text-anchor="middle" fill="#456">버클</text>')
        for i, p in enumerate(self.pockets):
            a(f'<rect x="{p.x:.2f}" y="{p.y:.2f}" width="{p.w:.2f}" height="{p.h:.2f}" fill="#d8f3ee" stroke="none"/>')
            a(f'<text x="{p.x + p.w / 2:.2f}" y="{p.y + p.h / 2:.2f}" font-size="3" text-anchor="middle" fill="#0b7d74">SNSS {i + 1}</text>')
            a(f'<text x="{p.x + p.w / 2:.2f}" y="{p.y + p.h / 2 + 3.6:.2f}" font-size="2" text-anchor="middle" fill="#0b7d74">{p.w:.1f}x{p.h:.1f} mm</text>')
        for name, pts in self.strokes:
            d = " ".join(f"{'M' if k == 0 else 'L'}{x:.2f},{y:.2f}" for k, (x, y) in enumerate(pts))
            a(f'<path d="{d}" fill="none" stroke="#1e2a36" stroke-width="{self.bead_mm * 0.35:.2f}" marker-end="url(#arr)"><title>{name}</title></path>')
        for k, (name, pts) in enumerate(self.strokes):
            x, y = pts[0]
            a(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="1.3" fill="#f6ad55"/><text x="{x:.2f}" y="{y + 0.9:.2f}" font-size="1.8" text-anchor="middle" fill="#000">{k + 1}</text>')
        for pts in self.traces:
            d = " ".join(f"{'M' if k == 0 else 'L'}{x:.2f},{y:.2f}" for k, (x, y) in enumerate(pts))
            a(f'<path d="{d}" fill="none" stroke="#d1495b" stroke-width="{self.trace_width_mm * 0.5:.2f}" stroke-dasharray="1.2 0.8" stroke-linecap="round"/>')
        a('</g>')
        ty = W + 2 * pad + 4
        a(f'<g transform="translate({pad},{ty})" font-size="3" fill="#1e2a36">')
        a(f'<text y="0" font-size="4" font-weight="bold">QEAAI SNSS 손목밴드 · 3D 펜 템플릿 (1:1, 손목 {self.wrist_mm:.0f} mm)</text>')
        a(f'<text y="6">검정 획: PCL 저온 3D 펜으로 번호 순서대로 (주황 원 = 시작점, 화살표 = 방향). 총 {m["pen_length_mm"]:.0f} mm, 약 {m["pen_time_min"]} 분, PCL {m["pcl_mass_g"]} g</text>')
        a(f'<text y="11">빨강 점선: SNSS 배선 잉크(GO 액정 20 mg/mL, PVA 0.5%)를 주사기 펜으로 그리거나 실로 꿰맨 뒤 비타민C 환원. 직렬 배선 합계 {m["trace_resistance_ohm"]} Ω</text>')
        a(f'<text y="16">청록: SNSS 셀 주머니 {self.cells}개 (각 {m["cell_footprint_cm2"]} cm²) - 그래핀 전극 + PVA·식염수 겔, 밀봉</text>')
        a('<text y="21">인쇄: 배율 100% (실제 크기). 완성 후 60 C 온수에 담가 손목 곡률로 굽힌다 (PCL 융점 60 C).</text>')
        a('<g transform="translate(0,27)"><rect width="10" height="1.2" fill="#1e2a36"/><text x="12" y="1.2" font-size="2.6">10 mm (인쇄 배율 확인)</text></g>')
        a('</g></svg>')
        return "\n".join(o)

    def svg_tattoo(self) -> str:
        """전자 타투 전극 시트: 지름 10 mm serpentine 메쉬 2개(차동) x 4 채널 + 기준 전극."""
        c = self.wrist_mm
        sw, sh = 110.0, c + 40
        o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{sw}mm" height="{sh}mm" viewBox="0 0 {sw} {sh}" font-family="sans-serif">',
             f'<rect width="{sw}" height="{sh}" fill="#fff"/>',
             '<text x="5" y="8" font-size="4" font-weight="bold">근전도 전자 타투 (그래핀, 1 µm 미만) · 전완 펼친 그림 1:1</text>',
             '<text x="5" y="13" font-size="2.6">가로 = 손목 주름에서 팔꿈치 쪽 거리, 세로 = 전완 둘레. 전극마다 serpentine 메쉬 2개(차동) + 기준 전극.</text>',
             f'<g transform="translate(5,20)"><rect width="100" height="{c}" fill="#fbf4ef" stroke="#c9b8aa" stroke-width="0.2"/>']
        for e in self.electrodes:
            for dx in (-7.0, 7.0):
                o.append(self._serpentine(e["x"] + dx, e["y"], 4.5))
            o.append(f'<text x="{e["x"]}" y="{e["y"] + 9}" font-size="2.6" text-anchor="middle">{e["channel"]} · {e["note"]}</text>')
        o.append(self._serpentine(15.0, c / 2, 4.5))
        o.append(f'<text x="15" y="{c / 2 + 9}" font-size="2.6" text-anchor="middle">기준 (뼈 위)</text>')
        o.append('</g></svg>')
        return "\n".join(o)

    @staticmethod
    def _serpentine(cx: float, cy: float, r: float) -> str:
        """원 안을 채우는 뱀 모양 경로 - 피부가 늘어나도 끊어지지 않는 전자 타투 패턴."""
        pts, n, step = [], 7, 2 * r / 7
        for i in range(n):
            x = cx - r + step * (i + 0.5)
            half = math.sqrt(max(r * r - (x - cx) ** 2, 0)) * 0.92
            ys = (cy - half, cy + half) if i % 2 == 0 else (cy + half, cy - half)
            pts += [(x, ys[0]), (x, ys[1])]
        d = "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in pts)
        return (f'<path d="{d}" fill="none" stroke="#2b2b2b" stroke-width="0.6" stroke-linejoin="round" stroke-linecap="round"/>'
                f'<circle cx="{cx}" cy="{cy}" r="{r + 0.6}" fill="none" stroke="#999" stroke-width="0.15" stroke-dasharray="0.6 0.6"/>')
