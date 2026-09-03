"""
parcel.py — 연속지적도(필지) → PNU·중심점·면적

이 모듈이 푸는 것 세 가지:
  ① 정비구역 경계 '안'의 건물만 골라내기 → aging 의 '대리지표' 딱지 제거
  ② 건축물대장 대지면적 결측(신림동 37%) 메우기 → 과소필지 요건 판정
  ③ 지번 → 좌표 (juso 좌표API 없이도 구역 판정 가능)

원천: 브이월드 연속지적도 LSMD_CONT_LDREG_<시군구코드>_<YYYYMM> (SHP, EPSG:5186).
      data/raw/jijeok/ 에 풀어두면 자동으로 찾는다.

⚠ 연속지적도는 **참고도형**이지 공부(公簿)면적이 아니다 → 면적은 단일값이 아니라 밴드로 다룬다.

   밴드는 눈대중이 아니라 실측이다. 관악구 신림동 10,639필지에서 대장 대지면적과 비교하되,
   **'외필지수' 로 두 집단을 갈랐다** — 이걸 안 가르면 밴드가 두 배로 부풀려진다:
     · 외필지 0 (단일 필지, 9,664건)  도형/대장 중앙 1.003 · 90%구간 0.960~1.115  ← 진짜 도형 오차
     · 외필지 1+ (여러 필지, 975건)   중앙 0.509                                  ← 오차가 아니라 개념 차이
       (대장 '대지면적' = 건축 대지 = 필지 여러 개 합. 과소필지 요건의 '필지' 와 다른 단위)

   과소필지 요건은 **지적공부상 필지** 단위이므로 이 판정의 정답 소스는 지적도 쪽이고,
   대장 대지면적은 외필지 1+ 이면 애초에 쓰면 안 된다.

⚠ 구역 포함 판정은 **필지 중심점**이 구역 폴리곤 안인지로 본다(경계에 걸친 필지는 중심 기준).
   신림동 실측: 이렇게 모은 필지면적 합 / 고시면적 = 98~100%.

  python parcel.py --setup            # data/raw/jijeok/ → data/parcels-<코드>.json
  python parcel.py --pnu 1162010200100100010
  python parcel.py --zone 신림7        # 그 구역 안 필지 요약
표준 라이브러리만 사용.
"""

import argparse
import glob
import json
import math
import os
from dataclasses import dataclass
from typing import Optional

import geo

ROOT = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(ROOT, "data", "raw", "jijeok")
SRC_DOC = "연속지적도(브이월드 LSMD_CONT_LDREG, 참고도형)"

# 실제 필지면적 추정 구간 = 도형면적 × [LO, HI].
# 실측 90% 구간(도형/실제 = 0.960~1.115)의 역수 → 0.897 ~ 1.042.
BAND_LO, BAND_HI = 1 / 1.115, 1 / 0.960          # ≈ 0.897 ~ 1.042
BAND_SRC = "관악구 신림동 단일필지 9,664건 실측 90% 구간 (외필지 1+ 제외)"
GWASO = 90.0          # 과소필지 기준 (서울 조례: 토지 90㎡ 미만)

# 주택접도율 — 서울 조례: 폭 4m 이상 도로에 4m 이상 접한 건축물의 비율 (기준 40% 이하)
ROAD_MIN_W = 4.0      # 도로 최소 폭 (m)
TOUCH_MIN = 4.0       # 최소 접한 길이 (m)
SNAP = 1.0            # 격자 해상도 (m) — 접한 길이를 이 단위로 센다
SUB = 0.2             # 경계 샘플링 간격 (m). 격자보다 촘촘해야 맞닿은 선분을 안 놓친다
JIMOK_ROAD = "도"     # 지적 지목 '도로'
ROAD_NOTE = ("지목 '도' 기준 근사 — 사도·통행로 같은 **현황도로는 미반영**이라 "
             "실제 접도율보다 낮게(=요건에 유리하게) 나올 수 있다. "
             "도로 폭은 폴리곤에서 2×면적/둘레로 추정한 값이다.")


@dataclass
class Parcel:
    pnu: str          # 19자리 = 법정동코드10 + 대장구분1 + 본번4 + 부번4
    jibun: str        # "856-1대"
    area: float       # 도형 면적 ㎡ (참고도형)
    x: float          # 중심점 — BASE(EPSG:2097) 로 변환해 저장, 구역과 같은 좌표계
    y: float
    jimok: str = ""   # 지목 한 글자 (대/도/천/임 …) — JIBUN 끝에서 파싱
    touch: float = -1.0   # 폭 ROAD_MIN_W 이상 도로에 접한 길이(m). -1=미계산

    @property
    def 도로(self) -> bool:
        return self.jimok == "도"

    @property
    def 접도(self) -> Optional[bool]:
        """조례 접도 조건(폭 4m 도로에 4m 이상 접함) 충족 여부. None=미계산."""
        if self.touch < 0:
            return None
        return self.touch >= TOUCH_MIN

    @property
    def bjd(self) -> str:
        return self.pnu[:10]

    @property
    def band(self) -> tuple[float, float]:
        """실제 필지면적이 있을 구간 (도형 정밀도 실측 근거)."""
        return self.area * BAND_LO, self.area * BAND_HI

    @property
    def 과소(self) -> str:
        """MET / NOT_MET / 확인필요 — 밴드가 90㎡ 를 걸치면 확정하지 않는다."""
        lo, hi = self.band
        if hi < GWASO:
            return "MET"
        if lo >= GWASO:
            return "NOT_MET"
        return "확인필요"

    def wgs84(self):
        return geo.tm_to_wgs84(self.x, self.y)


def pnu_of(sigungu: str, bjdong: str, bun, ji, san: bool = False) -> str:
    return (f"{sigungu}{bjdong}{'2' if san else '1'}"
            f"{str(bun).zfill(4)[-4:]}{str(ji).zfill(4)[-4:]}")


# ── 도형 계산 ──

def _shoelace(r) -> float:
    s = 0.0
    n = len(r) // 2
    for i in range(n):
        j = (i + 1) % n
        s += r[2 * i] * r[2 * j + 1] - r[2 * j] * r[2 * i + 1]
    return s / 2.0


def _centroid(rings):
    ax = ay = A = 0.0
    for r in rings:
        n = len(r) // 2
        for i in range(n):
            j = (i + 1) % n
            cr = r[2 * i] * r[2 * j + 1] - r[2 * j] * r[2 * i + 1]
            A += cr
            ax += (r[2 * i] + r[2 * j]) * cr
            ay += (r[2 * i + 1] + r[2 * j + 1]) * cr
    if abs(A) < 1e-9:                      # 면적 0(선형) → 꼭짓점 평균
        r = rings[0]
        n = len(r) // 2
        return sum(r[0::2]) / n, sum(r[1::2]) / n
    return ax / (3 * A), ay / (3 * A)


def _jimok(jibun: str) -> str:
    j = (jibun or "").strip()
    return j[-1] if j and "가" <= j[-1] <= "힣" else ""


def _perimeter(rings) -> float:
    t = 0.0
    for r in rings:
        n = len(r) // 2
        for i in range(n):
            j = (i + 1) % n
            t += math.hypot(r[2 * j] - r[2 * i], r[2 * j + 1] - r[2 * i + 1])
    return t


def _sample(rings, step=SUB):
    """폴리곤 경계를 step 간격으로 샘플링한 격자 좌표 집합.

    지적도는 인접 필지가 꼭짓점을 공유하지 않는 경우가 있어, 꼭짓점만 비교하면
    맞닿은 걸 놓친다. 경계를 따라 촘촘히 찍어 격자에 스냅하면 선분 겹침도 잡힌다.
    """
    pts = set()
    for r in rings:
        n = len(r) // 2
        for i in range(n):
            j = (i + 1) % n
            x0, y0, x1, y1 = r[2 * i], r[2 * i + 1], r[2 * j], r[2 * j + 1]
            d = math.hypot(x1 - x0, y1 - y0)
            k = max(1, int(d / step))
            for t in range(k + 1):
                f = t / k
                pts.add((round(x0 + (x1 - x0) * f), round(y0 + (y1 - y0) * f)))
    return pts


def _road_widths(items):
    """도로 필지 → 추정 폭. 길쭉한 도형에서 2×면적/둘레 ≈ 폭."""
    out = {}
    for pnu, rings, area in items:
        per = _perimeter(rings)
        out[pnu] = (2 * area / per) if per > 0 else 0.0
    return out


def _crs_of(prj_path: str):
    """.prj 를 읽어 좌표계를 고른다 — 추측하지 않는다."""
    if not os.path.exists(prj_path):
        raise SystemExit(f"{prj_path} 없음 — 좌표계를 알 수 없어 중단")
    t = open(prj_path, encoding="utf-8", errors="replace").read()
    if "Central_Belt_2010" in t or "Central Belt 2010" in t:
        return geo.EPSG_5186
    if "Korean 1985" in t or "Korean_1985" in t:
        return geo.EPSG_5174
    if "Korea 2000" in t or "Korea_2000" in t:
        return geo.EPSG_5181
    if "UTM-K" in t or "1000000" in t:
        return geo.EPSG_5179
    raise SystemExit(f"알 수 없는 좌표계:\n{t[:200]}")


def build(src: str = RAW, out_dir: str = None) -> list[str]:
    """data/raw/jijeok/*.shp → data/parcels-<시군구코드>.json (여러 구 동시 처리)."""
    out_dir = out_dir or os.path.join(ROOT, "data")
    shps = sorted(glob.glob(os.path.join(src, "*LDREG*.shp")))
    if not shps:
        raise SystemExit(
            f"{src} 에 연속지적도 SHP 가 없음.\n"
            "  브이월드(vworld.kr) → 연속지적도 → 시군구 선택 → 받은 zip 을\n"
            f"  {src}/ 에 풀어두세요.")
    made = []
    for shp in shps:
        stem = shp[:-4]
        crs = _crs_of(stem + ".prj")
        recs = geo.read_dbf(open(stem + ".dbf", "rb").read())
        geoms = geo.read_shp_polygons(open(shp, "rb").read())
        if len(recs) != len(geoms):
            raise SystemExit(f"{os.path.basename(shp)}: DBF {len(recs)} vs SHP {len(geoms)}")
        # ① 기본 속성
        keep, sgg = [], ""
        for r, g in zip(recs, geoms):
            if not g:
                continue
            pnu = (r.get("PNU") or "").strip()
            if len(pnu) != 19:
                continue
            sgg = sgg or pnu[:5]
            jibun = (r.get("JIBUN") or "").strip()
            keep.append((pnu, jibun, _jimok(jibun), g,
                         abs(sum(_shoelace(x_) for x_ in g))))

        # ② 접도 — 폭 4m 이상 도로 필지의 경계를 격자에 찍고, 각 필지가 몇 m 접하는지
        roads = [(pnu, g, a) for pnu, _, jm, g, a in keep if jm == JIMOK_ROAD]
        widths = _road_widths(roads)
        wide = set()
        for pnu, g, _a in roads:
            if widths.get(pnu, 0) >= ROAD_MIN_W:
                wide |= _sample(g)
        touch = {}
        for pnu, _jb, jm, g, _a in keep:
            if jm == JIMOK_ROAD:
                touch[pnu] = -1.0        # 도로 자신은 분모에서 뺀다
                continue
            hit = len(_sample(g) & wide)
            touch[pnu] = round(max(0.0, (hit - 1) * SNAP), 1) if hit else 0.0

        rows = []
        for pnu, jibun, jm, g, area in keep:
            cx, cy = _centroid(g)
            x, y = geo.wgs84_to_tm(*geo.tm_to_wgs84(cx, cy, crs))   # → BASE(2097)
            rows.append([pnu, jibun, round(area, 1), round(x, 1), round(y, 1),
                         jm, touch.get(pnu, -1.0)])
        path = os.path.join(out_dir, f"parcels-{sgg}.json")
        os.makedirs(out_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"출처": SRC_DOC, "원천": os.path.basename(shp),
                       "좌표계": "EPSG:2097(변환 저장)", "원본좌표계": crs.name,
                       "필지": len(rows), "도로필지": len(roads),
                       "접도기준": {"도로폭": ROAD_MIN_W, "접한길이": TOUCH_MIN,
                                 "샘플간격": SNAP, "주의": ROAD_NOTE},
                       "rows": rows},
                      fh, ensure_ascii=False, separators=(",", ":"))
            n_ok = sum(1 for r in rows if r[6] >= TOUCH_MIN)
        n_calc = sum(1 for r in rows if r[6] >= 0)
        made.append(f"{path}  ({len(rows):,}필지 · 도로 {len(roads):,} · "
                    f"접도 판정 {n_calc:,} 중 충족 {n_ok:,})")
    return made


_CACHE: dict = {}


def load(sigungu: str = None) -> dict[str, Parcel]:
    """시군구코드별 필지 사전. 코드를 안 주면 가진 것 전부."""
    key = sigungu or "*"
    if key in _CACHE:
        return _CACHE[key]
    paths = sorted(glob.glob(os.path.join(ROOT, "data", f"parcels-{sigungu or '*'}.json")))
    if not paths:
        raise SystemExit(
            "필지 데이터 없음. 브이월드 연속지적도를 data/raw/jijeok/ 에 풀고\n"
            "  python parcel.py --setup")
    out = {}
    for p in paths:
        for row in json.load(open(p, encoding="utf-8"))["rows"]:
            pnu, jibun, area, x, y = row[:5]
            jm = row[5] if len(row) > 5 else _jimok(jibun)
            tc = row[6] if len(row) > 6 else -1.0
            out[pnu] = Parcel(pnu, jibun, area, x, y, jm, tc)
    _CACHE[key] = out
    return out


_BON: dict = {}


def by_bon(parcels: dict) -> dict:
    """본번(PNU 앞 15자리) → 필지 목록. 대표지번에 부번이 없을 때 쓰는 인덱스."""
    key = id(parcels)
    if key not in _BON:
        idx = {}
        for pnu, p in parcels.items():
            idx.setdefault(pnu[:15], []).append(p)
        _BON[key] = idx
    return _BON[key]


def have(sigungu: str) -> bool:
    return bool(glob.glob(os.path.join(ROOT, "data", f"parcels-{sigungu}.json")))


# ── 질의 ──

def in_zone(zone, parcels=None) -> list[Parcel]:
    """구역 폴리곤 안(중심점 기준)의 필지."""
    ps = parcels if parcels is not None else load()
    x0, y0, x1, y1 = zone.bbox
    return [p for p in (ps.values() if isinstance(ps, dict) else ps)
            if x0 <= p.x <= x1 and y0 <= p.y <= y1 and zone.contains_tm(p.x, p.y)]


def coverage(zone, hits: list[Parcel]) -> float:
    """필지면적 합 / 고시면적 — 경계 추출이 제대로 됐는지 보는 자기점검 수치."""
    return sum(p.area for p in hits) / zone.area if zone.area else 0.0


def gwaso_ratio(hits: list[Parcel]) -> tuple[float, float, int]:
    """과소필지 비율 [하한, 상한] + 걸친 필지 수. 밴드가 90㎡ 를 걸치면 확정하지 않는다."""
    if not hits:
        return 0.0, 0.0, 0
    met = sum(1 for p in hits if p.과소 == "MET")
    edge = sum(1 for p in hits if p.과소 == "확인필요")
    n = len(hits)
    return met / n, (met + edge) / n, edge


def main(argv=None):
    p = argparse.ArgumentParser(description="연속지적도 필지")
    p.add_argument("--setup", action="store_true", help="data/raw/jijeok/*.shp → data/parcels-*.json")
    p.add_argument("--pnu", help="PNU 로 한 필지 조회")
    p.add_argument("--zone", help="구역명으로 그 안 필지 요약")
    a = p.parse_args(argv)

    if a.setup:
        for m in build():
            print("저장:", m)
        return

    if a.pnu:
        ps = load(a.pnu[:5])
        q = ps.get(a.pnu)
        if not q:
            raise SystemExit(f"PNU {a.pnu} 없음")
        la, lo = q.wgs84()
        b = q.band
        print(f"■ {q.pnu}  {q.jibun}")
        print(f"  도형면적 {q.area:,.1f}㎡  → 실제 추정 {b[0]:,.0f}~{b[1]:,.0f}㎡"
              f"  ({BAND_LO:.3f}~{BAND_HI:.3f}배)")
        print(f"  과소필지(90㎡ 미만): {q.과소}")
        print(f"  좌표 {la:.6f}, {lo:.6f}")
        print(f"  출처: {SRC_DOC} — 공부면적 아님(참고도형)")
        return

    if a.zone:
        zs = geo.search(a.zone)
        if not zs:
            raise SystemExit(f"'{a.zone}' 구역 없음")
        z = zs[0]
        ps = load(z.sigungu if z.sigungu != "11000" else None)
        hits = in_zone(z, ps)
        lo, hi, edge = gwaso_ratio(hits)
        cov = coverage(z, hits)
        print(f"■ {z.name}  ({z.kind})")
        print(f"  고시면적 {z.area:,.0f}㎡  ·  필지 {len(hits):,}개 합계 {sum(p.area for p in hits):,.0f}㎡"
              f"  ·  포착률 {cov:.1%}")
        rng = f"{lo:.1%}" if edge == 0 else f"{lo:.1%} ~ {hi:.1%}"
        print(f"  과소필지(90㎡ 미만) {rng}   (기준 40%)"
              + (f"  · 밴드가 90㎡ 를 걸친 필지 {edge}개" if edge else ""))
        print(f"  출처: {SRC_DOC}")
        return

    ps = load()
    print(f"필지 {len(ps):,}개 적재됨. --pnu / --zone 으로 조회하세요.")


if __name__ == "__main__":
    main()
