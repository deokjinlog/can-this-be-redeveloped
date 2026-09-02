"""
check.py — 주소 한 줄 → "이 재개발 물건, 사도 되나" A게이트 전체 판정

    python check.py "서울 관악구 신림동 10-10"
    python check.py --mock "서울 관악구 신림동 10-10"     # 키 없이 흐름 확인

흐름 (각 단계는 실패해도 나머지를 계속 진행한다 — 판단불가를 자료요청으로 수렴):

    주소 ─juso─→ 법정동코드·지번·좌표
                    ├─geo──→ 이미 정비구역 안인가 (고시도형)  ← 있으면 A게이트 끝
                    ├─gather→ 내 건물 준공·구조·세대수 (건축물대장 API)
                    └─aging─→ 그 길 일대 노후도 (표제부 CSV 전수)  ← 미지정일 때 쓰는 요건 근거
                                                    └─criteria_engine─→ A 판정

C게이트(입주권 자격, engine.py)는 등기부·초본이 있어야 해서 여기선 요청 목록만 낸다.
"""

import argparse
import sys
from datetime import date

import criteria_engine as CE

_ERR = []          # 단계별 실패를 모아 끝에 한 번에 알린다


def _try(label, fn, *a, **k):
    try:
        return fn(*a, **k)
    except SystemExit as e:
        _ERR.append(f"{label}: {e}")
    except Exception as e:
        _ERR.append(f"{label}: {type(e).__name__} {e}")
    return None


def _building_from_raw(raw: dict):
    """gather 원시응답 → criteria_engine.Building + 표시용 dict."""
    y = raw.get("useAprDay")
    year = int(str(y)[:4]) if y and len(str(y)) >= 4 else None
    st, pu = raw.get("struct") or "", raw.get("purpose") or ""
    rc = (any(k in st for k in ("철근콘크리트", "철골", "강구조"))
          and any(k in pu for k in ("공동주택", "아파트", "주택")))
    b = CE.Building(구조="RC공동주택" if rc else "기타")
    if year:
        b.준공일 = CE.Fact(date(year, 1, 1), CE.Grade.P1, "건축물대장(표제부 API)",
                        f"사용승인 {y}")
    info = {"year": year, "struct": st, "purpose": pu,
            "households": raw.get("households"), "bldNm": raw.get("bldNm"),
            "dong": raw.get("_동수")}
    return b, info


def run(keyword: str, mock: bool = False, unit: str = "road"):
    import juso

    out = {"keyword": keyword}
    print(f"■ 조회: {keyword}\n")

    # ── 1. 주소 ──
    addr = _try("주소검색(juso)", juso.resolve, keyword, mock)
    if addr is None:
        print("주소를 못 찾았습니다.")
        for e in _ERR:
            print("  ⚠", e)
        print("\n  키가 없으면:  python check.py --mock \"서울 관악구 신림동 10-10\"")
        return out
    out["addr"] = addr
    print("[1] 주소")
    print("   ", juso.render(addr).replace("\n", "\n    "))
    print()

    # ── 2. 이미 정비구역인가 ──
    zones = []
    if addr.lat is not None:
        import geo
        zones = _try("구역판정(geo)", geo.at, addr.lat, addr.lon) or []
    out["zones"] = zones
    print("[2] 정비구역 지정 여부")
    if addr.lat is None:
        print("    ⚪ 좌표 미확인 → 구역 판정 건너뜀 (JUSO_COORD_KEY 필요)")
    elif not zones:
        print("    ⚪ 지정된 정비구역·촉진지구 안이 아님 → 아래 요건으로 '될 수 있나'를 본다")
    else:
        for z in zones:
            print(f"    🟢 {z.kind} · {z.name}  {z.area:,.0f}㎡"
                  + (f"  (조각 {z.parts}개 합산)" if z.parts > 1 else ""))
            if z.notice:
                print(f"        고시 {z.notice} · 도형 {z.created}")
    print()

    # ── 3. 내 건물 (건축물대장) ──
    import gather
    raw = _try("건축물대장(gather)", gather.fetch_title,
               addr.sigungu, addr.bjdong, addr.bun, addr.ji, mock)
    b, binfo = (_building_from_raw(raw) if raw else (CE.Building(), None))
    out["building"] = binfo
    print("[3] 내 건물 (건축물대장 표제부)")
    if binfo is None:
        print("    🟡 조회 실패 또는 키 없음 → 준공연도·구조 미확인")
    else:
        print(f"    {binfo['bldNm'] or '(건물명 없음)'} · 준공 {binfo['year'] or '?'}"
              f" · {binfo['struct'] or '?'} · {binfo['purpose'] or '?'}"
              f" · {binfo['households'] or 0}세대"
              + (f" · {binfo['dong']}개동" if binfo.get("dong") else ""))
    print()

    # ── 4. 그 일대 노후도 (표제부 CSV 전수) ──
    ag = None
    if not zones:            # 이미 지정됐으면 노후도를 다시 셀 이유가 없다
        import aging
        bl = _try("노후도 집계(aging)", aging.load)
        if bl:
            buckets = aging.aggregate(bl, unit)
            key = addr.rnMgtSn if unit == "road" else addr.bun.lstrip("0") or "0"
            ag = buckets.get(key)
            print("[4] 그 일대 노후도 (건축물대장 표제부 전수)")
            if ag is None:
                print(f"    ⚪ 이 주소의 {('도로' if unit=='road' else '지번블록')} 는 "
                      f"가진 CSV 안에 없음 (CSV 는 특정 법정동만 담고 있음)")
            else:
                rng = f"{ag.lo:.1%}" if ag.unknown == 0 else f"{ag.lo:.1%} ~ {ag.hi:.1%}"
                v = ag.verdict(CE.Cfg.REDEV_RATIO)
                icon = {"MET": "🟢", "NOT_MET": "🔴", "확인필요": "🟡"}[v]
                print(f"    {icon} {ag.label} — 노후·불량 {rng} (기준 {CE.Cfg.REDEV_RATIO:.0%})"
                      f" · {ag.total:,}동")
                print("       ⚠ 집계 단위는 정비구역 경계가 아니라 " + ag.unit + " (대리지표)")
            print()
    out["aging"] = ag

    # ── 5. A 판정 ──
    if zones:
        import geo
        area = geo.to_criteria(zones)
    else:
        import aging
        area = aging.to_area(ag) if ag is not None else CE.Area()
    rep = CE.evaluate(b, area)
    out["report"] = rep
    print("=" * 62)
    print(CE.render(rep))

    # ── 6. C게이트 안내 ──
    print("=" * 62)
    print("■ 다음 관문 — C 입주권 자격 (engine.py)")
    print("  투기과열지구에서 사면 조합원 지위를 승계받는지(입주권) vs 현금청산인지.")
    print("  이건 공공데이터에 없는 개인 서류라 업로드가 필요합니다:")
    print("    📄 등기부등본 (매도인 취득일)")
    print("    📄 주민등록초본 (매도인 거주기간, 배우자·직계존비속 합산)")
    print("    📄 (해당 시) 상속·해외이주·경매 증빙")

    if _ERR:
        print("\n── 진행 중 건너뛴 단계 ──")
        for e in _ERR:
            print("  ⚠", e)
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description="주소 한 줄 → 재개발 A게이트 판정")
    p.add_argument("keyword", nargs="*")
    p.add_argument("--mock", action="store_true", help="키 없이 흐름 확인")
    p.add_argument("--by", choices=["road", "bun"], default="road", help="노후도 집계 단위")
    a = p.parse_args(argv)
    kw = " ".join(a.keyword).strip()
    if not kw:
        p.error('주소를 입력하세요. 예: python check.py "서울 관악구 신림동 10-10"')
    run(kw, a.mock, a.by)


if __name__ == "__main__":
    main()
