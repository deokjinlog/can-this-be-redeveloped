"""
juso.py — 주소 한 줄 → 법정동코드·지번·좌표 (도로명주소 API)

이 한 단계가 프로젝트의 입력 마찰을 없앤다.
지금까지 손으로 넣던 `11620 10200 0010 0010` 이 주소에서 바로 나온다:

    admCd(10자리)  = 시군구코드5 + 법정동코드5   → gather.py 의 sigungu / bjdong
    lnbrMnnm/SlNo  = 지번 본번 / 부번            → gather.py 의 bun / ji
    entX / entY    = 좌표                        → geo.py 의 구역 판정

**키는 선택이다.** 건물DB + 연속지적도를 내려받아 두면 addrdb.py 가 같은 일을 로컬에서 한다.
우선순위: 키가 있으면 API, 없으면 로컬 폴백 → 둘 다 없으면 그때만 실패.
    JUSO_KEY        도로명주소 API   (주소검색)   ↔ addrdb.search
    JUSO_COORD_KEY  좌표제공 API     (좌표)      ↔ 연속지적도 필지 중심
.env(gitignore) 에 넣는다. 커밋 금지.

좌표제공 API 가 어느 좌표계로 주는지는 안내가 엇갈린다 → **추측하지 않는다.**
받은 좌표를 geo.sniff_crs 로 후보(5186/5181/5174/5179)에 대보고,
주소검색이 알려준 자치구 안에 떨어지는 것을 고른다. 못 고르면 좌표를 버린다(미확인).

    python juso.py --mock "서울 관악구 신림동 10-10"
    python juso.py "서울 관악구 신림동 10-10"
표준 라이브러리만 사용.
"""

import json
import os
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

import geo

SEARCH_URL = "https://business.juso.go.kr/addrlink/addrLinkApi.do"
COORD_URL = "https://business.juso.go.kr/addrlink/addrCoordApi.do"


def _load_env():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()


@dataclass
class Addr:
    roadAddr: str
    jibunAddr: str
    admCd: str            # 법정동코드 10자리
    rnMgtSn: str          # 도로명코드 12자리
    udrtYn: str           # 지하 여부 0/1
    buldMnnm: str         # 건물본번
    buldSlno: str         # 건물부번
    lnbrMnnm: str         # 지번 본번
    lnbrSlno: str         # 지번 부번
    bdNm: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    crs: str = ""         # 좌표를 어느 좌표계로 해석했는지 (판별 결과)
    via: str = "juso API" # 어디서 나온 값인지 (juso API / 로컬 건물DB)

    @property
    def sigungu(self) -> str:
        return self.admCd[:5]

    @property
    def bjdong(self) -> str:
        return self.admCd[5:]

    @property
    def bun(self) -> str:
        return (self.lnbrMnnm or "0").zfill(4)

    @property
    def ji(self) -> str:
        return (self.lnbrSlno or "0").zfill(4)

    @property
    def 자치구(self) -> str:
        return geo.SIGUNGU.get(self.sigungu, self.sigungu)


_MOCK = {
    "results": {"common": {"errorCode": "0", "errorMessage": "정상"}, "juso": [{
        "roadAddr": "서울특별시 관악구 신림로58길 62-5 (신림동)",
        "jibunAddr": "서울특별시 관악구 신림동 10-10",
        "admCd": "1162010200", "rnMgtSn": "116204160540", "udrtYn": "0",
        "buldMnnm": "62", "buldSlno": "5", "lnbrMnnm": "10", "lnbrSlno": "10",
        "bdNm": ""}]}}


def _get(url, params) -> dict:
    q = urllib.parse.urlencode(params, encoding="utf-8")
    req = urllib.request.Request(url + "?" + q, headers={"User-Agent": "can-this-be-redeveloped"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def _from_local(q: str, n: int) -> list[Addr]:
    """키 없이 — 내려받은 건물DB 로 해석."""
    import addrdb
    out = []
    for h in addrdb.search(q, n=n):
        out.append(Addr(roadAddr=h.road_addr, jibunAddr=h.jibun_addr, admCd=h.bjd,
                        rnMgtSn=h.roadcd, udrtYn=h.ug, buldMnnm=h.bbun, buldSlno=h.bji,
                        lnbrMnnm=h.bun, lnbrSlno=h.ji, bdNm=h.bldnm, via="로컬 건물DB"))
    return out


def search(keyword: str, mock: bool = False, n: int = 5) -> list[Addr]:
    if mock:
        d = _MOCK
    else:
        key = os.environ.get("JUSO_KEY")
        if not key:
            try:
                hits = _from_local(keyword, n)
            except SystemExit as e:
                raise SystemExit(
                    "주소를 해석할 방법이 없습니다. 둘 중 하나:\n"
                    "  (a) 키 없이 — 건물DB 를 data/raw/juso/ 에 두고  python addrdb.py --setup 11620\n"
                    "  (b) 키로   — business.juso.go.kr 승인키를 .env 에 JUSO_KEY=...\n"
                    f"  (로컬 사유: {e})")
            return hits
        d = _get(SEARCH_URL, {"confmKey": key, "currentPage": 1, "countPerPage": n,
                              "keyword": keyword, "resultType": "json"})
    common = d.get("results", {}).get("common", {})
    if common.get("errorCode") not in ("0", None):
        raise SystemExit(f"주소검색 실패 [{common.get('errorCode')}] {common.get('errorMessage')}")
    out = []
    for j in d.get("results", {}).get("juso") or []:
        out.append(Addr(
            roadAddr=j.get("roadAddr", ""), jibunAddr=j.get("jibunAddr", ""),
            admCd=j.get("admCd", ""), rnMgtSn=j.get("rnMgtSn", ""), udrtYn=j.get("udrtYn", "0"),
            buldMnnm=j.get("buldMnnm", "0"), buldSlno=j.get("buldSlno", "0"),
            lnbrMnnm=j.get("lnbrMnnm", "0"), lnbrSlno=j.get("lnbrSlno", "0"),
            bdNm=j.get("bdNm", "")))
    return out


def _gu_box(sigungu: str):
    """그 자치구 안에 있는 구역 도형들로 대략적인 bbox(WGS84) 를 만든다 — 좌표계 판별용 잣대."""
    try:
        zs = [z for z in geo.load() if z.sigungu in (sigungu, "11000")]
    except SystemExit:
        return None
    zs = [z for z in zs if z.sigungu == sigungu]
    if not zs:
        return None
    pts = [geo.tm_to_wgs84((z.bbox[0] + z.bbox[2]) / 2, (z.bbox[1] + z.bbox[3]) / 2) for z in zs]
    la = [p[0] for p in pts]
    lo = [p[1] for p in pts]
    return (sum(la) / len(la), sum(lo) / len(lo))


def _coord_local(a: Addr) -> bool:
    """연속지적도 필지 중심으로 좌표를 채운다. 성공 여부 반환."""
    try:
        import parcel
    except ImportError:
        return False
    sgg = a.sigungu
    if not parcel.have(sgg):
        a.crs = f"미확인(좌표API 키 없음 · 연속지적도 {sgg} 도 없음)"
        return False
    pnu = parcel.pnu_of(sgg, a.bjdong, a.bun, a.ji)
    p = parcel.load(sgg).get(pnu)
    if p is None:
        a.crs = f"미확인(PNU {pnu} 가 연속지적도에 없음)"
        return False
    a.lat, a.lon = p.wgs84()
    a.crs = "연속지적도 필지 중심"
    return True


def coord(a: Addr, mock: bool = False) -> Addr:
    """좌표제공 API → entX/entY → 좌표계 판별 → a.lat/a.lon 채움.
    키가 없으면 연속지적도 필지 중심으로 대체한다."""
    if mock:
        # 판별기까지 함께 확인하려고, 실제 신림동 좌표를 5186 으로 만들어 흘린다.
        x, y = geo.wgs84_to_tm(37.484201, 126.929715, geo.EPSG_5186)
        ent = {"entX": f"{x:.4f}", "entY": f"{y:.4f}"}
    else:
        key = os.environ.get("JUSO_COORD_KEY")
        if not key:
            _coord_local(a)
            return a
        d = _get(COORD_URL, {"confmKey": key, "admCd": a.admCd, "rnMgtSn": a.rnMgtSn,
                             "udrtYn": a.udrtYn, "buldMnnm": a.buldMnnm,
                             "buldSlno": a.buldSlno, "resultType": "json"})
        common = d.get("results", {}).get("common", {})
        if common.get("errorCode") not in ("0", None):
            a.crs = f"미확인(좌표API [{common.get('errorCode')}] {common.get('errorMessage')})"
            return a
        js = d.get("results", {}).get("juso") or []
        if not js:
            a.crs = "미확인(좌표 응답 없음)"
            return a
        ent = js[0]
    try:
        x, y = float(ent.get("entX")), float(ent.get("entY"))
    except (TypeError, ValueError):
        a.crs = "미확인(entX/entY 파싱 실패)"
        return a

    near = _gu_box(a.sigungu)
    cands = geo.sniff_crs(x, y, near=near)
    if not cands:
        a.crs = "미확인(어느 후보 좌표계로도 한국 밖)"
        return a
    c, la, lo, d = cands[0]
    # 자치구 중심에서 15km 넘게 벗어나면 판별 실패로 본다(추측으로 밀어넣지 않는다)
    if near and d is not None and d > 15000:
        a.crs = f"미확인(최선 후보 {c.name} 도 자치구 중심에서 {d/1000:.0f}km)"
        return a
    a.lat, a.lon, a.crs = la, lo, c.name
    return a


def resolve(keyword: str, mock: bool = False) -> Optional[Addr]:
    hits = search(keyword, mock)
    return coord(hits[0], mock) if hits else None


def render(a: Addr) -> str:
    L = [f"■ {a.roadAddr}",
         f"  지번: {a.jibunAddr}" + (f"  · {a.bdNm}" if a.bdNm else ""),
         f"  법정동코드: {a.admCd}  →  시군구 {a.sigungu} / 법정동 {a.bjdong}  ({a.자치구})",
         f"  지번코드: 본번 {a.bun} / 부번 {a.ji}   → 건축물대장 조회에 그대로 투입"]
    if a.lat is not None:
        L.append(f"  좌표: {a.lat:.6f}, {a.lon:.6f}   [{a.crs}]")
    else:
        L.append(f"  좌표: {a.crs or '미확인'} — 구역 판정은 건너뜀")
    L.append(f"  경로: {a.via}")
    return "\n".join(L)


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="주소 → 법정동코드·지번·좌표")
    p.add_argument("keyword", nargs="*", help="주소 (예: 서울 관악구 신림동 10-10)")
    p.add_argument("--mock", action="store_true", help="키 없이 흐름 확인")
    p.add_argument("-n", type=int, default=5, help="검색 결과 개수")
    a = p.parse_args(argv)
    kw = " ".join(a.keyword).strip()
    if not kw:
        p.error("주소를 입력하세요")
    hits = search(kw, a.mock, a.n)
    if not hits:
        print("검색 결과 없음. 더 구체적으로 (예: '관악구 신림동 10-10')")
        return
    print(render(coord(hits[0], a.mock)))
    if len(hits) > 1:
        print(f"\n  다른 후보 {len(hits)-1}건:")
        for h in hits[1:]:
            print(f"    · {h.roadAddr}  ({h.jibunAddr})")


if __name__ == "__main__":
    main()
