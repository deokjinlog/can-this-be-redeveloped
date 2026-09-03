"""
addrdb.py — 로컬 주소 해석 (juso API 키 없이)

juso 오픈API 가 하던 일을 내려받은 **주소정보 건물DB** 로 대신한다.
좌표까지 로컬(연속지적도 필지 중심)에서 나오므로 키가 아예 필요 없다.

    juso 도로명주소 API  →  build_<시도>.txt   (도로명 ↔ 지번 ↔ 법정동코드)
    juso 좌표제공 API    →  parcel.py          (PNU → 필지 중심점)

원천: 주소기반산업지원서비스(business.juso.go.kr) → 주소정보 다운로드 → 건물DB 전체분.
      압축을 풀어 build_seoul.txt 를 data/raw/juso/ 에 두면 된다. (CP949, `|` 구분)

    python addrdb.py --setup 11620          # 그 자치구 인덱스 생성
    python addrdb.py "관악구 신림동 10-10"
    python addrdb.py "신림로58길 62-5"
표준 라이브러리만 사용.
"""

import argparse
import glob
import json
import os
import re
from dataclasses import dataclass
from typing import Optional

ROOT = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(ROOT, "data", "raw", "juso")
SRC_DOC = "주소정보 건물DB(주소기반산업지원서비스)"

# build_*.txt 필드 (1-based → 0-based)
F_BJD, F_SIDO, F_SGG, F_DONG, F_RI = 0, 1, 2, 3, 4
F_SAN, F_BUN, F_JI = 5, 6, 7
F_ROADCD, F_ROAD, F_UG, F_BBUN, F_BJI = 8, 9, 10, 11, 12


@dataclass
class Hit:
    bjd: str          # 법정동코드 10
    sgg: str          # 시군구명
    dong: str         # 법정동명
    san: str          # "0" 지번 / "1" 산
    bun: str          # 지번 본번 (문자열)
    ji: str           # 지번 부번
    road: str         # 도로명
    roadcd: str       # 도로명코드 12
    ug: str           # 지하 여부
    bbun: str         # 건물 본번
    bji: str          # 건물 부번
    bldnm: str = ""

    @property
    def jibun_addr(self) -> str:
        j = f"{self.bun}-{self.ji}" if self.ji != "0" else self.bun
        return f"서울특별시 {self.sgg} {self.dong} {'산 ' if self.san == '1' else ''}{j}"

    @property
    def road_addr(self) -> str:
        b = f"{self.bbun}-{self.bji}" if self.bji != "0" else self.bbun
        return (f"서울특별시 {self.sgg} {self.road} "
                f"{'지하 ' if self.ug == '1' else ''}{b}"
                + (f" ({self.dong}{', ' + self.bldnm if self.bldnm else ''})" if self.dong else ""))

    @property
    def pnu(self) -> str:
        return (f"{self.bjd}{'2' if self.san == '1' else '1'}"
                f"{self.bun.zfill(4)[-4:]}{self.ji.zfill(4)[-4:]}")


def _src_files() -> list[str]:
    return sorted(glob.glob(os.path.join(RAW, "build_*.txt")))


def build(sigungu: str, out_dir: str = None) -> str:
    """build_<시도>.txt → data/addr-<시군구코드>.json."""
    srcs = _src_files()
    if not srcs:
        raise SystemExit(
            f"{RAW} 에 build_*.txt 가 없음.\n"
            "  business.juso.go.kr → 주소정보 다운로드 → 건물DB(전체분) 을 받아\n"
            f"  압축을 풀고 build_seoul.txt 를 {RAW}/ 에 두세요.")
    bjd, roads, rows = {}, {}, []
    ri, di = {}, {}
    for src in srcs:
        with open(src, "rb") as fh:
            for line in fh:
                f = line.decode("cp949", "replace").rstrip("\n").split("|")
                if len(f) < 13 or f[F_BJD][:5] != sigungu:
                    continue
                if f[F_BJD] not in di:
                    di[f[F_BJD]] = len(di)
                    bjd[f[F_BJD]] = [f[F_SGG], f[F_DONG]]
                if f[F_ROADCD] not in ri:
                    ri[f[F_ROADCD]] = len(ri)
                    roads[f[F_ROADCD]] = f[F_ROAD]
                rows.append([di[f[F_BJD]], f[F_SAN], f[F_BUN], f[F_JI],
                             ri[f[F_ROADCD]], f[F_UG], f[F_BBUN], f[F_BJI]])
    if not rows:
        raise SystemExit(f"시군구 {sigungu} 행이 없음 (다른 시도 파일이 필요할 수 있음)")
    out_dir = out_dir or os.path.join(ROOT, "data")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"addr-{sigungu}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"출처": SRC_DOC, "시군구": sigungu,
                   "bjd_keys": list(di), "bjd": [bjd[k] for k in di],
                   "road_keys": list(ri), "road": [roads[k] for k in ri],
                   "rows": rows}, fh, ensure_ascii=False, separators=(",", ":"))
    return f"{path}  ({len(rows):,}건, 법정동 {len(di)} · 도로 {len(ri)})"


class DB:
    def __init__(self, path):
        d = json.load(open(path, encoding="utf-8"))
        self.sigungu = d["시군구"]
        self.bjd_keys, self.bjd = d["bjd_keys"], d["bjd"]
        self.road_keys, self.road = d["road_keys"], d["road"]
        self.rows = d["rows"]
        self.by_jibun, self.by_road, self.dong_names = {}, {}, {}
        for r in self.rows:
            self.by_jibun.setdefault((r[0], r[1], r[2].lstrip("0") or "0",
                                      r[3].lstrip("0") or "0"), r)
            self.by_road.setdefault((r[4], r[5], r[6].lstrip("0") or "0",
                                     r[7].lstrip("0") or "0"), r)
        for i, (sgg, dong) in enumerate(self.bjd):
            self.dong_names.setdefault(dong, i)
        self.road_names = {}
        for i, nm in enumerate(self.road):
            self.road_names.setdefault(nm, i)

    def _hit(self, r) -> Hit:
        sgg, dong = self.bjd[r[0]]
        return Hit(self.bjd_keys[r[0]], sgg, dong, r[1],
                   r[2].lstrip("0") or "0", r[3].lstrip("0") or "0",
                   self.road[r[4]], self.road_keys[r[4]], r[5],
                   r[6].lstrip("0") or "0", r[7].lstrip("0") or "0")


_DBS: dict = {}


def load(sigungu: str = None) -> list[DB]:
    pats = sorted(glob.glob(os.path.join(ROOT, "data", f"addr-{sigungu or '*'}.json")))
    if not pats:
        raise SystemExit(
            "로컬 주소 인덱스 없음.\n"
            "  건물DB 를 data/raw/juso/ 에 두고:  python addrdb.py --setup 11620")
    out = []
    for p in pats:
        if p not in _DBS:
            _DBS[p] = DB(p)
        out.append(_DBS[p])
    return out


def have(sigungu: str) -> bool:
    return bool(glob.glob(os.path.join(ROOT, "data", f"addr-{sigungu}.json")))


# ── 주소 문자열 파싱 ──

_NUM = re.compile(r"(?:산\s*)?(\d+)(?:\s*-\s*(\d+))?\s*(?:번지)?\s*$")
_DONG = re.compile(r"([가-힣]+(?:\d+가)?동)\b")
_ROAD = re.compile(r"([가-힣A-Za-z0-9]+(?:대로|로|길))\b")


def parse(q: str):
    """'관악구 신림동 10-10' / '신림로58길 62-5' → (종류, 이름, 본번, 부번, 산)."""
    q = " ".join(q.replace(",", " ").split())
    m = _NUM.search(q)
    bun = m.group(1) if m else None
    ji = (m.group(2) or "0") if m else "0"
    san = "1" if m and "산" in q[max(0, m.start() - 2):m.start() + 1] else "0"
    head = q[:m.start()] if m else q
    r = _ROAD.search(head)
    d = _DONG.search(head)
    # 도로명과 동이 함께 있으면(도로명주소의 괄호 표기) 도로명이 우선
    if r and (not d or r.start() > d.start()):
        return ("road", r.group(1), bun, ji, san)
    if d:
        return ("jibun", d.group(1), bun, ji, san)
    if r:
        return ("road", r.group(1), bun, ji, san)
    return (None, head.strip(), bun, ji, san)


def search(q: str, sigungu: str = None, n: int = 5) -> list[Hit]:
    kind, name, bun, ji, san = parse(q)
    if bun is None:
        return []
    out = []
    for db in load(sigungu):
        if kind == "jibun":
            i = db.dong_names.get(name)
            if i is None:
                continue
            r = db.by_jibun.get((i, san, bun, ji))
            if r:
                out.append(db._hit(r))
        elif kind == "road":
            i = db.road_names.get(name)
            if i is None:
                continue
            for ug in ("0", "1"):
                r = db.by_road.get((i, ug, bun, ji))
                if r:
                    out.append(db._hit(r))
        if len(out) >= n:
            break
    return out[:n]


def coord(h: Hit):
    """지번 → 연속지적도 필지 중심 → WGS84. 필지 데이터가 없으면 (None, None, 사유)."""
    import parcel
    sgg = h.bjd[:5]
    if not parcel.have(sgg):
        return None, None, f"미확인(연속지적도 {sgg} 없음 — parcel.py --setup)"
    p = parcel.load(sgg).get(h.pnu)
    if p is None:
        return None, None, f"미확인(PNU {h.pnu} 가 지적도에 없음)"
    la, lo = p.wgs84()
    return la, lo, "연속지적도 필지 중심(EPSG:2097→WGS84)"


def render(h: Hit) -> str:
    la, lo, how = coord(h)
    L = [f"■ {h.road_addr}",
         f"  지번: {h.jibun_addr}",
         f"  법정동코드: {h.bjd}  →  시군구 {h.bjd[:5]} / 법정동 {h.bjd[5:]}  ({h.sgg} {h.dong})",
         f"  지번코드: 본번 {h.bun.zfill(4)} / 부번 {h.ji.zfill(4)}   PNU {h.pnu}"]
    L.append(f"  좌표: {la:.6f}, {lo:.6f}   [{how}]" if la is not None else f"  좌표: {how}")
    L.append(f"  출처: {SRC_DOC} (키 없이 로컬 조회)")
    return "\n".join(L)


def main(argv=None):
    p = argparse.ArgumentParser(description="로컬 주소 해석 (juso 키 불필요)")
    p.add_argument("q", nargs="*")
    p.add_argument("--setup", metavar="시군구코드", help="예: 11620 (관악구)")
    a = p.parse_args(argv)
    if a.setup:
        print("저장:", build(a.setup))
        return
    q = " ".join(a.q).strip()
    if not q:
        p.error('주소를 입력하세요. 예: python addrdb.py "관악구 신림동 10-10"')
    hits = search(q)
    if not hits:
        k, nm, bun, ji, san = parse(q)
        raise SystemExit(f"못 찾음 (해석: 종류={k} 이름={nm} 번={bun} 지={ji}). "
                         f"인덱스가 있는 자치구인지 확인하세요.")
    print(render(hits[0]))
    for h in hits[1:]:
        print(f"    · {h.road_addr}")


if __name__ == "__main__":
    main()
