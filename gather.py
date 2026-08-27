"""
gather.py — 주소/법정동코드 → 공공데이터 자동 수집 → 엔진 투입 (Level 1)

이 프로젝트에서 API 키로 채울 수 있는 건 '건축물대장(준공·구조·세대수·용도)'.
지역 노후도·구역면적·호수밀도는 정보몽땅(스크래핑, 별도), 소유·거주기간은 등기부·초본(업로드).

키 없이 흐름 확인: python gather.py --mock
라이브:            DATA_GO_KR_KEY=... python gather.py --code 11290 10100 0123 0004
  (11290=성북구 시군구코드5, 10100=법정동코드5, 0123=본번, 0004=부번)

키는 .env(gitignore)에 두거나 환경변수. 절대 커밋 금지(repo Public).
표준 라이브러리만 사용(urllib+xml) — 별도 설치 불필요.
"""

import os
import sys
import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date

from criteria_engine import Building, Area, Fact, Grade, evaluate, render

BLD_TITLE_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"


def _load_env():
    """.env(gitignore) 에서 키 로드. 커밋 금지."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()


# ── 응답 필드 매핑 (라이브에서 어긋나면 여기만 수정) ──
FIELD = {
    "useAprDay": "useAprDay",       # 사용승인일 YYYYMMDD
    "struct":    "strctCdNm",       # 구조명 (철근콘크리트구조 등)
    "purpose":   "mainPurpsCdNm",   # 주용도명 (공동주택/단독주택 등)
    "households":"hhldCnt",         # 세대수
    "totArea":   "totArea",         # 연면적
    "grndFlr":   "grndFlrCnt",      # 지상층수
    "bldNm":     "bldNm",           # 건물명
}


def _get(item, key):
    el = item.find(FIELD[key])
    return el.text.strip() if el is not None and el.text else None


def fetch_title(sigungu, bjdong, bun, ji, mock=False) -> dict:
    """건축물대장 표제부 조회(JSON) → 원시 dict. 여러 동이면 첫 동."""
    if mock:
        return {k: v for k, v in zip(FIELD, [
            "19900501", "철근콘크리트구조", "공동주택(아파트)", "480", "52000.5", "15", "○○아파트"])}
    key = os.environ.get("DATA_GO_KR_KEY")
    if not key:
        raise SystemExit("DATA_GO_KR_KEY 없음. .env 에 넣거나 --mock 로 실행.")
    # 인코딩키(%2B 등)는 이미 URL-safe → 그대로 붙임(이중 인코딩 방지)
    q = (f"serviceKey={key}&sigunguCd={sigungu}&bjdongCd={bjdong}"
         f"&bun={str(bun).zfill(4)}&ji={str(ji).zfill(4)}"
         f"&numOfRows=100&pageNo=1&_type=json")
    with urllib.request.urlopen(BLD_TITLE_URL + "?" + q, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8", "ignore"))
    rb = data.get("response", {}) or {}
    hdr = rb.get("header", {}) or {}
    if str(hdr.get("resultCode")) not in ("00", "0", "None"):
        raise SystemExit(f"API 오류: {hdr.get('resultCode')} {hdr.get('resultMsg')}")
    items = (rb.get("body", {}) or {}).get("items", {}) or {}
    item = items.get("item") if isinstance(items, dict) else items
    if isinstance(item, dict):
        item = [item]
    if not item:
        raise SystemExit("표제부 결과 없음 (법정동코드/지번 확인).")
    # 한 지번에 여러 동 → 주거동(공동주택/아파트) 우선, 없으면 연면적 최대
    def is_resi(it): return any(k in (it.get(FIELD["purpose"]) or "") for k in ("공동주택", "아파트", "주택"))
    resi = [it for it in item if is_resi(it)]
    pool = resi if resi else item
    chosen = max(pool, key=lambda it: float(it.get(FIELD["totArea"]) or 0))
    out = {k: chosen.get(FIELD[k]) for k in FIELD}
    out["_동수"] = len(item)
    return out


def to_building(raw: dict, asof: str) -> Building:
    """원시 dict → Building. 구조 버킷 매핑."""
    src = f"건축물대장 표제부({raw.get('bldNm') or ''})"
    준공 = None
    if raw.get("useAprDay") and len(raw["useAprDay"]) == 8:
        y, m, d = raw["useAprDay"][:4], raw["useAprDay"][4:6], raw["useAprDay"][6:8]
        준공 = Fact(date(int(y), int(m), int(d)), Grade.P1, src,
                   f"사용승인 {y}-{m}-{d}")
    st = raw.get("struct") or ""
    공동 = "공동주택" in (raw.get("purpose") or "") or "아파트" in (raw.get("purpose") or "")
    rc = any(k in st for k in ("철근콘크리트", "철골", "강구조"))
    구조 = "RC공동주택" if (rc and 공동) else "기타"
    return Building(준공일=준공, 구조=구조)


def collect(sigungu, bjdong, bun, ji, mock=False):
    raw = fetch_title(sigungu, bjdong, bun, ji, mock=mock)
    b = to_building(raw, asof="")
    # 구역 요건(노후도·면적·선택)은 정보몽땅/블록스캔 → 여기선 미확인(확인필요로 나옴)
    a = Area(사업유형="재개발", 지역="서울")
    return raw, b, a


def main():
    args = sys.argv[1:]
    mock = "--mock" in args
    if mock:
        sig, bj, bun, ji = "11290", "10100", "0123", "0004"
    elif "--code" in args:
        i = args.index("--code")
        sig, bj, bun, ji = args[i + 1:i + 5]
    else:
        raise SystemExit("사용: python gather.py --mock  |  --code 시군구5 법정동5 본번 부번")

    raw, b, a = collect(sig, bj, bun, ji, mock=mock)
    print("── 건축물대장 자동수집 결과 ──")
    print(json.dumps(raw, ensure_ascii=False, indent=2))
    print(f"\n→ Building: 준공={b.준공일.value if b.준공일 else None}, 구조={b.구조}")
    if raw.get("households"):
        print(f"   (세대수 {raw['households']} · 연면적 {raw.get('totArea')}㎡ — 재건축 판정용)")
    print("\n" + "=" * 56)
    print("엔진 투입 결과 (구역 노후도·면적은 미수집 → 확인필요로 표시)")
    print("=" * 56)
    print(render(evaluate(b, a)))
    print("\n※ 자동으로 채운 건 '내 건물(준공·구조)'뿐. 노후도·면적=정보몽땅(다음), 소유·거주=등기부·초본(업로드).")


if __name__ == "__main__":
    main()
