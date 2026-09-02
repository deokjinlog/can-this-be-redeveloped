"""
juso.py — 주소 한 줄 → 법정동코드·지번·좌표 (도로명주소 API)

이 한 단계가 프로젝트의 입력 마찰을 없앤다.
지금까지 손으로 넣던 `11620 10200 0010 0010` 이 주소에서 바로 나온다:

    admCd(10자리)  = 시군구코드5 + 법정동코드5   → gather.py 의 sigungu / bjdong
    lnbrMnnm/SlNo  = 지번 본번 / 부번            → gather.py 의 bun / ji
    entX / entY    = 좌표                        → geo.py 의 구역 판정

키 2개가 필요하다(둘 다 무료·즉시발급, business.juso.go.kr → API신청하기):
    JUSO_KEY        도로명주소 API   (주소검색)
    JUSO_COORD_KEY  좌표제공 API     (좌표)
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


def search(keyword: str, mock: bool = False, n: int = 5) -> list[Addr]:
    if mock:
        d = _MOCK
    else:
        key = os.environ.get("JUSO_KEY")
        if not key:
            raise SystemExit(
                "JUSO_KEY 없음. business.juso.go.kr → API신청하기 → '도로명주소 API' 승인키를\n"
                "  .env 에  JUSO_KEY=...  로 넣으세요 (무료·즉시발급). 흐름만 보려면 --mock.")
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


def coord(a: Addr, mock: bool = False) -> Addr:
    """좌표제공 API → entX/entY → 좌표계 판별 → a.lat/a.lon 채움."""
    if mock:
        # 판별기까지 함께 확인하려고, 실제 신림동 좌표를 5186 으로 만들어 흘린다.
        x, y = geo.wgs84_to_tm(37.484201, 126.929715, geo.EPSG_5186)
        ent = {"entX": f"{x:.4f}", "entY": f"{y:.4f}"}
    else:
        key = os.environ.get("JUSO_COORD_KEY")
        if not key:
            a.crs = "미확인(JUSO_COORD_KEY 없음)"
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
        L.append(f"  좌표: {a.lat:.6f}, {a.lon:.6f}   [{a.crs} 로 판별]")
    else:
        L.append(f"  좌표: {a.crs or '미확인'} — 구역 판정은 건너뜀")
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
