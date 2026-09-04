"""
stage.py — 정비사업 진행단계 (정보몽땅 사업장 목록)

마지막 남았던 구멍. "이 구역 지금 어디까지 갔나" 를 답하고,
**C게이트의 첫 관문**(§39② 조합원 지위 양도 제한이 이미 발동했는지)을 자동 판정한다.

    도시정비법 §39②  — 투기과열지구에서
        재건축: 조합설립인가 **후** 양수 → 조합원이 될 수 없음(8예외 필요)
        재개발: 관리처분계획인가 **후** 양수 → 조합원이 될 수 없음(8예외 필요)
    → 그 시점 **전**이면 제한 자체가 없다. 지금까지 이 게이트가 없어서
      투기과열지구이기만 하면 무조건 8예외를 따졌다(과잉 판정).

원천: 정비사업 정보몽땅(cleanup.seoul.go.kr) 사업장검색.
      `python stage.py --fetch` 가 목록을 직접 읽는다(한 번 요청, 표준 라이브러리만).
      오프라인이면 엑셀다운로드 `사업장목록.xls` 를 data/raw/mongttang/ 에 두고 --setup.
      목록에는 엑셀에 없는 것 두 개가 더 있다:
        · cafe   조합 카페 id  → elapse.py 가 인가 '일자' 를 읽는 입구
        · agz    구역 id(11620AGZ…) → 고시도형 WTNNC_SN 과 같은 포맷 = 정확 조인키

⚠ 근거등급 S1(기관 게시) — 대장 전수(P1)보다 한 단계 낮다.
   목록 화면 표시값이라 **인가 '일자'가 없다.** 재건축 3년 트리(예외5~7)는 날짜가 필요해
   여기서 자동으로 못 채운다 → 사업장 상세 페이지나 고시문이 따로 필요하다.
⚠ 대표지번은 **구역이 아니라 대표 1필지**다. "이 주소가 이 구역 안인가" 는 여전히
   고시도형 폴리곤(geo.py)으로 판정한다. 이 파일은 폴리곤에 이름·단계를 붙이는 라벨이다.

    python stage.py --setup
    python stage.py --gu 관악구
    python stage.py --find 신림7
표준 라이브러리만 사용(변환 1회만 xlrd 필요 — 아래 --setup 안내 참조).
"""

import argparse
import html
import json
import os
import re
import urllib.request
from dataclasses import dataclass
from typing import Optional

ROOT = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(ROOT, "data", "raw", "mongttang")
OUT = os.path.join(ROOT, "data", "stages-seoul.json")
BJD = os.path.join(ROOT, "data", "bjd-seoul.json")
SRC_DOC = "정비사업 정보몽땅 사업장목록(기관 게시)"

# ── 단계 순서 — 도시정비법 절차 흐름 ──
# 같은 순위 = 실무상 같은 지점(표기만 다름). 목록에 실제로 나온 24종 전부 매핑.
ORDER = {
    "안전진단": 10, "안전진단(1차)": 10,
    "정비계획 수립": 20,
    "지구단위계획수립/건축심의/교통심의": 25, "도시계획심의": 25,
    "정비구역지정": 30,
    "추진위구성": 40, "추진위원회승인": 45,
    "조합규약작성": 50, "조합창립총회": 55,
    "조합설립인가": 60,          # ← 재건축 §39② 발동
    "사업계획승인": 65, "사업계획승인(리모델링 허가)": 65,
    "사업시행인가": 70,
    "관리처분인가": 80,          # ← 재개발 §39② 발동
    "철거": 85, "철거 및 착공": 88, "착공": 90,
    "분양": 95,
    "준공인가": 100, "사용검수 및 입주": 105,
    "이전고시": 110,
    "조합해산": 120, "청산 및 조합해산": 125, "조합청산": 130,
    # 지역주택조합 전용 — 도시정비법 절차가 아니다
    "조합원 모집신고": 0,
    # '사업시행자지정'(공공시행·신탁방식)은 일부러 넣지 않는다.
    # 조합이 없는 방식이라 §39② 의 '조합설립인가' 에 대응하는지가 불확실하고,
    # 순서에 끼워넣으면 그게 곧 추측이 된다 → rank -1 로 두어 '확인필요' 로 흐르게 한다.
}
GATE_재건축 = ORDER["조합설립인가"]
GATE_재개발 = ORDER["관리처분인가"]

# 사업구분 → 도시정비법 적용 여부
# 가로주택·소규모재건축·소규모재개발은 「빈집 및 소규모주택 정비 특례법」,
# 지역주택은 주택법, 리모델링은 주택법 — §39② 그대로 적용되지 않는다(흔한 오답).
LAW = {
    "재건축": "재건축", "소규모재건축": "소규모",
    "재개발(주택정비형)": "재개발", "재개발(도시정비형)": "재개발",
    "소규모재개발": "소규모", "가로주택정비": "소규모",
    "지역주택": "기타", "리모델링": "기타",
}

_JIBUN = re.compile(r"^([가-힣0-9]+(?:동|가))\s*(산)?\s*(\d+)(?:\s*-\s*(\d+))?")

LIST_URL = "https://cleanup.seoul.go.kr/cleanup/bsnssttus/lsubBsnsSttus.do?pageSize={}"
_UA = {"User-Agent": "Mozilla/5.0 (can-this-be-redeveloped; personal non-commercial)"}
_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_AGZ = re.compile(r"mapOpenPopup\('([^']+)'\)")
_CAFE = re.compile(r"cafeOpenPopup\('([^']+)'\)")


def _celltext(x: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", x))).strip()


def fetch_list(size: int = 1500) -> list[dict]:
    """사업장 목록을 한 번에 읽는다. (페이지 파라미터는 pageSize 만 먹는다)"""
    req = urllib.request.Request(LIST_URL.format(size), headers=_UA)
    with urllib.request.urlopen(req, timeout=90) as r:
        page = r.read().decode("utf-8", "replace")
    out = []
    for m in _ROW.findall(page):
        tds = _TD.findall(m)
        if len(tds) < 6:
            continue
        c = [_celltext(t) for t in tds]
        if not c[0].isdigit():
            continue
        a, cf = _AGZ.search(m), _CAFE.search(m)
        out.append({"자치구": c[1], "사업구분": c[2], "사업장명": c[3], "대표지번": c[4],
                    "진행단계": c[5], "운영구분": "", "운영단계": "",
                    "cafe": cf.group(1) if cf else "", "agz": a.group(1) if a else ""})
    return out


@dataclass
class Site:
    gu: str
    kind: str            # 사업구분 원문
    law: str             # 재건축 / 재개발 / 소규모 / 기타
    name: str
    jibun: str           # 대표지번 원문
    stage: str           # 진행단계 원문
    rank: int            # ORDER 순위 (모르면 -1)
    op: str              # 운영구분 (운영 / 일시중단)
    op_stage: str        # 운영단계 (조합 / 추진위원회 / …)
    bjd: str = ""        # 법정동코드 10 (못 찾으면 "")
    pnu: str = ""        # 19자리 (못 만들면 "")
    cafe: str = ""       # 조합 카페 id — elapse.py 로 인가 일자를 읽는 입구
    agz: str = ""        # 구역 id (고시도형 WTNNC_SN 과 같은 포맷)

    @property
    def 승계제한(self) -> tuple[str, str]:
        """(상태, 근거) — 투기과열지구라는 전제 하에 §39② 가 발동했는지.

        발동 / 미발동 / 해당없음(별도법) / 확인필요(단계 미상)
        """
        if self.law == "소규모":
            return ("해당없음", "「빈집 및 소규모주택 정비 특례법」 사업 — 도시정비법 §39② 아님(별도 검토)")
        if self.law == "기타":
            return ("해당없음", f"{self.kind} — 도시정비법 정비사업이 아님(주택법)")
        if self.rank < 0:
            return ("확인필요", f"진행단계 '{self.stage or '미표시'}' 를 절차 순서에 매핑 못 함")
        if self.rank == 0:
            return ("확인필요", "지역주택조합 절차 표기 — 도시정비법 단계 아님")
        gate = GATE_재건축 if self.law == "재건축" else GATE_재개발
        gname = "조합설립인가" if self.law == "재건축" else "관리처분계획인가"
        if self.rank >= gate:
            return ("발동", f"{self.law} · 현재 '{self.stage}' 는 {gname} 이후 → 양수해도 조합원 지위 승계 제한")
        return ("미발동", f"{self.law} · 현재 '{self.stage}' 는 {gname} 전 → §39② 제한 없음(자유 양도)")

    @property
    def 진행률(self) -> Optional[float]:
        if self.rank <= 0:
            return None
        return min(1.0, self.rank / ORDER["이전고시"])


# ── 변환 ──

def _load_bjd() -> dict:
    if not os.path.exists(BJD):
        return {}
    m = json.load(open(BJD, encoding="utf-8"))["map"]
    idx = {}
    for code, (gu, dong) in m.items():
        idx[(gu, dong)] = code
    return idx


def _read_xls(src: str = None) -> list[dict]:
    try:
        import xlrd
    except ImportError:
        raise SystemExit(
            "xlrd 가 필요합니다(엑셀 경로만).  uv run --with xlrd==2.0.1 python stage.py --setup\n"
            "  또는 키·의존성 없이:  python stage.py --fetch")
    src = src or (next((os.path.join(RAW, f) for f in sorted(os.listdir(RAW))
                        if f.endswith(".xls")), None) if os.path.isdir(RAW) else None)
    if not src:
        raise SystemExit(
            f"{RAW} 에 사업장목록.xls 가 없음. 정보몽땅 엑셀다운로드로 받거나 --fetch 를 쓰세요.")
    sh = xlrd.open_workbook(src).sheet_by_index(0)
    hdr = [str(sh.cell_value(1, c)).strip() for c in range(sh.ncols)]
    out = []
    for r in range(2, sh.nrows):
        d = dict(zip(hdr, [str(sh.cell_value(r, c)).strip() for c in range(sh.ncols)]))
        if d.get("사업장명"):
            d.setdefault("cafe", "")
            d.setdefault("agz", "")
            out.append(d)
    return out


def build(src: str = None, out: str = OUT, rows_in: list = None, 원천: str = "") -> str:
    """목록(fetch) 또는 엑셀 → data/stages-seoul.json (대표지번 → PNU 조립)."""
    src_rows = rows_in if rows_in is not None else _read_xls(src)
    원천 = 원천 or (os.path.basename(src) if src else "사업장목록.xls")
    bjd_idx = _load_bjd()
    rows, nopnu = [], 0
    for d in src_rows:
        gu, jibun = d.get("자치구", ""), d.get("대표지번", "")
        stage, kind = d.get("진행단계", ""), d.get("사업구분", "")
        m = _JIBUN.match(jibun)
        bjd = pnu = ""
        if m:
            dong, san, bun, ji = m.groups()
            bjd = bjd_idx.get((gu, dong), "")
            if bjd:
                pnu = f"{bjd}{'2' if san else '1'}{bun.zfill(4)[-4:]}{(ji or '0').zfill(4)[-4:]}"
        if not pnu:
            nopnu += 1
        rows.append([gu, kind, LAW.get(kind, "기타"), d.get("사업장명", ""), jibun,
                     stage, ORDER.get(stage, -1), d.get("운영구분", ""),
                     d.get("운영단계", ""), bjd, pnu,
                     d.get("cafe", ""), d.get("agz", "")])
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"출처": SRC_DOC, "원천": 원천, "사업장": len(rows), "PNU미조립": nopnu,
                   "카페": sum(1 for r in rows if r[11]),
                   "구역ID": sum(1 for r in rows if r[12]), "rows": rows},
                  fh, ensure_ascii=False, separators=(",", ":"))
    return (f"{out}  ({len(rows):,}건 · PNU 미조립 {nopnu} · "
            f"카페 {sum(1 for r in rows if r[11]):,} · 구역ID {sum(1 for r in rows if r[12]):,})")


_CACHE = None
_BY_SGG: dict = {}


def by_sigungu(code: str, sites=None) -> list[Site]:
    ss = sites if sites is not None else load()
    key = (id(ss), code)
    if key not in _BY_SGG:
        _BY_SGG[key] = [s for s in ss if s.bjd[:5] == code]
    return _BY_SGG[key]


def load(path: str = OUT) -> list[Site]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    if not os.path.exists(path):
        raise SystemExit(
            "진행단계 데이터 없음.\n"
            "  정보몽땅 사업장목록.xls 를 data/raw/mongttang/ 에 두고\n"
            "  uv run --with xlrd==2.0.1 python stage.py --setup")
    d = json.load(open(path, encoding="utf-8"))
    _CACHE = [Site(*r) for r in d["rows"]]
    return _CACHE


# ── 질의 ──

def by_gu(gu: str, sites=None) -> list[Site]:
    return [s for s in (sites or load()) if s.gu == gu]


def by_pnu(pnu: str, sites=None) -> list[Site]:
    return [s for s in (sites or load()) if s.pnu == pnu]


def _norm(t: str) -> str:
    return re.sub(r"[\s\-_()（）·,]", "", t or "")


# '신림7' '봉천4-1-3' '가재울8' 처럼 [지역명+번호] 가 사실상 구역의 고유 이름이다.
_TOKEN = re.compile(r"([가-힣]{2,4}?)\s*제?\s*(\d+(?:-\d+)*)\s*(?:구역|지구)?")
_STOP = ("주택", "재개발", "재건축", "정비", "사업", "구역", "지구", "조합", "촉진", "공공")


def _tokens(name: str) -> set:
    """구역 식별 토큰 집합. '신림7구역 재개발정비사업' → {'신림7'}"""
    out = set()
    for base, num in _TOKEN.findall(name or ""):
        if base and base not in _STOP and not any(w in base for w in _STOP):
            out.add(base + num)
    return out


def _rep_parcels(site: Site, ps: dict) -> tuple[list, bool]:
    """대표지번에 대응하는 필지들 + 정확 매칭 여부 (단건 조회용)."""
    if not site.pnu:
        return [], False
    p = ps.get(site.pnu)
    if p is not None:
        return [p], True
    import parcel as PARCEL
    return PARCEL.by_bon(ps).get(site.pnu[:15], []), False


def match_zone(zone, sites=None, parcels=None) -> Optional[Site]:
    """고시도형 구역 ↔ 정보몽땅 사업장 매칭.

    위치가 이름보다 강한 신호다:
      ⓪ 구역 ID(AGZ) 가 양쪽에 있으면 그것으로 확정
      ① 구역 안 필지 집합을 만들고, 대표지번이 거기 드는 사업장을 모은다
         (대표지번에 부번이 없으면 본번 단위로 넓히되 '느슨함'을 기억)
      ② 그중 이름 토큰('신림7' 등)이 맞는 것을 고른다
      ③ 양쪽에 번호가 있는데 하나도 안 맞으면 **붙이지 않는다**
         — 인접 구역이 같은 본번에 걸려 '봉천3 → 봉천4-1-2' 같은 오매칭이 난다
      ④ 위치를 못 쓰면 이름 토큰이 유일하게 맞는 경우, 마지막으로 구역 ID(AGZ)
    """
    ss = sites if sites is not None else load()
    ztok = _tokens(zone.name)
    zn = _norm(zone.name)

    inside, exact = [], []
    located = False          # 이 구역에 대해 '위치로 판정할 수 있었는가'
    try:
        import parcel as PARCEL
        ps = parcels if parcels is not None else PARCEL.load(
            zone.sigungu if zone.sigungu != "11000" else None)
        hits = PARCEL.in_zone(zone, ps)
        located = len(hits) >= 3
        pnus = {p.pnu for p in hits}
        bons = {p.pnu[:15] for p in hits}
        pool_sites = (by_sigungu(zone.sigungu, ss)
                      if zone.sigungu not in ("11000", "") else ss)
        for s in pool_sites:
            if not s.pnu:
                continue
            if s.pnu in pnus:
                inside.append(s)
                exact.append(s)
            elif s.pnu[:15] in bons:
                inside.append(s)
    except SystemExit:
        pass

    if inside:
        named = [s for s in inside if ztok & _tokens(s.name)]
        if named:
            return named[0] if len(named) == 1 else max(named, key=lambda s: s.rank)
        if ztok and any(_tokens(s.name) for s in inside):
            return None          # 양쪽에 번호가 있는데 하나도 안 맞음 = 다른 구역
        if not exact:
            return None          # 정확 지번도 아니고 이름 확인도 못 함 → 미상
        return exact[0] if len(exact) == 1 else max(exact, key=lambda s: s.rank)

    # 위치로 판정할 수 있었는데(구역 안 필지를 찾았는데) 대표지번이 하나도 안 들어왔다면,
    # 그건 '이 구역에 대응하는 현행 사업장이 없다' 는 뜻이다. 이름으로 억지로 붙이면
    # 다른 땅의 같은 번호 구역에 매달린다(신림1구역 2007 준공 → 신림1재정비촉진구역 조합).
    if located:
        return None

    if ztok:
        cands = [s for s in ss if ztok & _tokens(s.name)]
        if len({(c.name, c.stage) for c in cands}) == 1:
            return cands[0]
    cands = [s for s in ss if _norm(s.name) and (_norm(s.name) in zn or zn in _norm(s.name))]
    if len(cands) == 1:
        return cands[0]

    # ④ 마지막으로 구역 ID(AGZ). 정확 조인처럼 보이지만 구역이 분할되면
    #    고시도형은 옛 경계('봉천6구역'), 사업장은 분할 후('봉천6-1구역')를 가리킨다.
    #    그래서 이름·위치가 먼저고, 이건 최후 수단 — 어긋남은 match_note 로 드러낸다.
    if getattr(zone, "agz", ""):
        hit = [s for s in ss if s.agz == zone.agz]
        if hit:
            return hit[0] if len(hit) == 1 else max(hit, key=lambda s: s.rank)
    return None


def match_note(zone, site) -> Optional[str]:
    """붙인 매칭이 미덥지 않은 이유. 없으면 None.

    AGZ 가 같아도 구역이 쪼개졌으면 이름이 어긋난다(봉천6구역 ↔ 봉천6-1구역).
    서울 실측: AGZ 조인 144건 중 130건은 이름도 일치, 14건은 분할·개명으로 어긋남.
    """
    if site is None:
        return None
    zt, mt = _tokens(zone.name), _tokens(site.name)
    if zt and mt and not (zt & mt):
        via = "구역ID(AGZ)" if getattr(zone, "agz", "") and zone.agz == site.agz else "위치"
        return (f"{via} 로는 이어지지만 이름 번호가 다름 "
                f"({'/'.join(sorted(zt))} vs {'/'.join(sorted(mt))}) "
                "— 구역이 분할·개명됐을 수 있으니 사업장명을 확인하세요")
    if getattr(zone, "family", "") and site.law not in ("기타",):
        fam = {"재개발": "재개발", "재건축": "재건축"}.get(zone.family)
        if fam and site.law in ("재개발", "재건축") and fam != site.law:
            return f"구역은 {zone.family}인데 사업장은 {site.law} — 사업이 바뀌었는지 확인 필요"
    return None


# ── 출력 ──

_ICON = {"발동": "🔒", "미발동": "🔓", "해당없음": "⚪", "확인필요": "🟡"}


def render(s: Site, detail: bool = True) -> str:
    st, why = s.승계제한
    L = [f"■ {s.name}",
         f"  {s.gu} · {s.kind} · 대표지번 {s.jibun}",
         f"  진행단계: {s.stage or '(미표시)'}"
         + (f"   ({s.진행률:.0%} 지점)" if s.진행률 else "")
         + (f"   ⚠ {s.op}" if s.op and s.op != "운영" else "")]
    if detail:
        L += [f"  {_ICON[st]} 조합원 지위 승계 제한: {st}",
              f"      {why}",
              "      ※ 투기과열지구 전제. 인가 '일자'는 목록에 없어 3년 트리(예외5~7)는 별도 확인 필요.",
              f"  출처: {SRC_DOC} (S1)"]
    return "\n".join(L)


def main(argv=None):
    p = argparse.ArgumentParser(description="정비사업 진행단계")
    p.add_argument("--fetch", action="store_true",
                   help="정보몽땅 목록을 직접 읽어 저장 (cafe·구역ID 포함, 의존성 없음)")
    p.add_argument("--setup", action="store_true", help="사업장목록.xls → data/stages-seoul.json")
    p.add_argument("--gu", help="자치구 (예: 관악구)")
    p.add_argument("--find", help="사업장명 검색")
    p.add_argument("--src", help="xls 경로 직접 지정")
    a = p.parse_args(argv)

    if a.fetch:
        rows = fetch_list()
        print(f"목록 {len(rows):,}건 수신")
        print("저장:", build(rows_in=rows, 원천="사업장검색 목록(직접 조회)"))
        return
    if a.setup:
        print("저장:", build(a.src))
        return

    ss = load()
    if a.find:
        hits = [s for s in ss if _norm(a.find) in _norm(s.name)]
        if not hits:
            raise SystemExit(f"'{a.find}' 없음")
        for s in hits[:5]:
            print(render(s))
            print()
        return
    if a.gu:
        hits = sorted(by_gu(a.gu, ss), key=lambda s: (-s.rank, s.name))
        print(f"■ {a.gu} 정비사업 {len(hits)}개소\n")
        print(f"  {'단계':<16}{'구분':<14}{'제한':<6}사업장")
        for s in hits:
            st, _ = s.승계제한
            print(f"  {s.stage[:15] or '(미표시)':<16}{s.kind[:13]:<14}"
                  f"{_ICON[st]}{st[:4]:<5}{s.name[:34]}")
        return
    print(f"사업장 {len(ss):,}건. --gu / --find 로 조회하세요.")


if __name__ == "__main__":
    main()
