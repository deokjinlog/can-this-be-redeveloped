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
    y = str(raw.get("useAprDay") or "")
    year = int(y[:4]) if len(y) >= 4 and y[:4].isdigit() else None
    st, pu = raw.get("struct") or "", raw.get("purpose") or ""
    rc = (any(k in st for k in ("철근콘크리트", "철골", "강구조"))
          and any(k in pu for k in ("공동주택", "아파트", "주택")))
    b = CE.Building(구조="RC공동주택" if rc else "기타")
    if year:
        try:            # YYYYMMDD 면 그날로, 아니면 그 해 1월 1일로(보수적)
            d = date(year, int(y[4:6]), int(y[6:8])) if len(y) >= 8 else date(year, 1, 1)
        except ValueError:
            d = date(year, 1, 1)
        b.준공일 = CE.Fact(d, CE.Grade.P1, "건축물대장(표제부 API)", f"사용승인 {y}")
    info = {"year": year, "struct": st, "purpose": pu,
            "households": raw.get("households"), "bldNm": raw.get("bldNm"),
            "dong": raw.get("_동수")}
    return b, info


def _parse_date(s: str):
    from datetime import datetime
    for f in ("%Y-%m-%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(s.strip(), f).date()
        except ValueError:
            pass
    raise SystemExit(f"날짜 형식을 못 읽음: {s} (예: 2026-05-01)")


def run(keyword: str, mock: bool = False, unit: str = "road",
        계약일=None, 등기일=None):
    import juso

    out = {"keyword": keyword}
    print(f"■ 조회: {keyword}\n")

    # ── 1. 주소 ──
    addr = _try("주소검색(juso)", juso.resolve, keyword, mock)
    if addr is None:
        print("주소를 못 찾았습니다.")
        for e in _ERR:
            print("  ⚠", e)
        print("  · 건물이 없는 필지(도로·공원 등)는 건물DB 에 없습니다 — 건물이 선 지번으로 시도하세요.")
        print("  · 인덱스가 있는 자치구인지 확인:  ls data/addr-*.json")
        print("  · 흐름만 볼 때:  python check.py --mock \"서울 관악구 신림동 10-10\"")
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
            mark = "" if z.현행 else "  ⚠ 과거 세대"
            print(f"    {'🟢' if z.현행 else '⚪'} {z.kind} · {z.name}  {z.area:,.0f}㎡"
                  + (f"  (조각 {z.parts}개 합산)" if z.parts > 1 else "") + mark)
            if z.notice:
                print(f"        고시 {z.notice}"
                      + (f" ({z.notice_date})" if z.notice_date else "")
                      + f" · 도형 {z.created}")
            if not z.현행:
                print(f"        → 같은 땅을 더 최근 고시가 덮음: {z.superseded_by}")
    print()

    # ── 2b. 진행단계 (정보몽땅) ──
    site = None
    zone_note = None
    if zones:
        import geo as _geo
        import stage
        _body, _ = _geo.pick(zones)
        if _body is not None:
            site = _try("진행단계(정보몽땅)", stage.match_zone, _body)
        print("[2b] 진행단계 (정보몽땅 게시)")
        if site is None:
            print("    🟡 이 구역에 대응하는 사업장을 특정하지 못함 "
                  "(이름·대표지번으로 확정 안 되면 붙이지 않습니다)")
        else:
            print(f"    {site.stage or '(미표시)'}"
                  + (f"  ({site.진행률:.0%} 지점)" if site.진행률 else "")
                  + f"   — {site.name}")
            if site.op and site.op != "운영":
                print(f"    ⚠ 운영구분: {site.op}")
            zone_note = stage.match_note(_body, site)
            if zone_note:
                print(f"    ⚠ {zone_note}")
        print()
    out["zone_note"] = zone_note
    out["site"] = site

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

    # ── 4. 노후도 — 구역이 있으면 그 경계 안으로, 없으면 길 단위 대리지표 ──
    import aging
    ag = None
    bl = _try("노후도 집계(aging)", aging.load)
    body = None
    if zones:
        import geo
        body, _promo = geo.pick(zones)
    if bl:
        icon = {"MET": "🟢", "NOT_MET": "🔴", "확인필요": "🟡"}
        if body is not None:
            ag = _try("구역 내 집계", aging.aggregate_zone, bl, body)
            print("[4] 구역 경계 안 실측 (연속지적 필지 → 대장 건물)")
            if ag is None:
                print("    ⚪ 연속지적도가 없어 구역 안 집계 불가 (python parcel.py --setup)")
            elif ag.범위밖:
                j = ag.jijeok
                print(f"    ⚪ 구역 안 필지 {j.필지 if j else 0:,}개 — 다만 이 구역은 가진 "
                      f"표제부 CSV 의 법정동 밖이라 건물 집계 불가(자료 없음)")
            elif ag.total == 0:
                j = ag.jijeok
                print(f"    ⚪ 구역 안 필지 {j.필지 if j else 0:,}개인데 대장 건물이 0동")
            else:
                rng = f"{ag.lo:.1%}" if ag.unknown == 0 else f"{ag.lo:.1%} ~ {ag.hi:.1%}"
                print(f"    {icon[ag.verdict(CE.Cfg.REDEV_RATIO)]} 노후·불량 {rng} "
                      f"(기준 {CE.Cfg.REDEV_RATIO:.0%}) · 건물 {ag.total:,}동")
                j = ag.jijeok
                if j and j.필지:
                    g = f"{j.과소_lo:.1%}" if j.경계필지 == 0 else f"{j.과소_lo:.1%}~{j.과소_hi:.1%}"
                    print(f"       과소필지 {g} (기준 40%) · 필지 {j.필지:,}개 "
                          f"· 포착률 {j.포착률:.1%}")
                if ag.접도율 is not None:
                    print(f"       주택접도율 {ag.접도율:.1%} (기준 40% 이하) "
                          f"— 지목 '도' 기준, 현황도로 미반영")
                if ag.호수밀도 is not None:
                    print(f"       호수밀도 {ag.호수밀도:,.0f}호/ha (기준 60호) — 정의 미검증 참고치")
            ph = aging.phase_signal(ag) if ag else None
            if ph:
                print(f"    {ph[0]} 사업 단계 신호: {ph[1]}")
                print(f"       {ph[2]}  ※ 대장 실측에서 읽은 추정")
                cc = aging.cross_check(ag, site)
                if cc:
                    print(f"       {'✓' if cc[0] else '⚠'} 교차검증: {cc[1]}")
        else:
            buckets = aging.aggregate(bl, unit)
            key = addr.rnMgtSn if unit == "road" else addr.bun.lstrip("0") or "0"
            ag = buckets.get(key)
            print("[4] 그 일대 노후도 (건축물대장 표제부 전수)")
            if ag is None:
                print(f"    ⚪ 이 주소의 {('도로' if unit=='road' else '지번블록')} 는 "
                      f"가진 CSV 안에 없음 (CSV 는 특정 법정동만 담고 있음)")
            else:
                rng = f"{ag.lo:.1%}" if ag.unknown == 0 else f"{ag.lo:.1%} ~ {ag.hi:.1%}"
                print(f"    {icon[ag.verdict(CE.Cfg.REDEV_RATIO)]} {ag.label} — "
                      f"노후·불량 {rng} (기준 {CE.Cfg.REDEV_RATIO:.0%}) · {ag.total:,}동")
                print("       ⚠ 집계 단위는 정비구역 경계가 아니라 " + ag.unit + " (대리지표)")
        print()
    out["aging"] = ag

    # ── 5. A 판정 ──
    if zones:
        import geo
        area = geo.to_criteria(zones)
    else:
        area = aging.to_area(ag) if ag is not None else CE.Area()
    rep = CE.evaluate(b, area)
    out["report"] = rep
    print("=" * 62)
    print(CE.render(rep))

    # ── 5b. 인가 일자 (추진경과) ──
    el = None
    if site is not None and site.cafe:
        import elapse
        el = _try("추진경과(정보몽땅)", lambda: elapse.load().get(site.cafe))
        print("=" * 62)
        print("[5b] 인가 일자 (조합 추진경과)")
        if el is None:
            print(f"    ⚪ 미수집/미공개 — 받으려면:  python elapse.py --cafe {site.cafe}")
        else:
            for fld, d in el.anchors.items():
                print(f"    · {fld:<16} {d}")
            if not el.anchors:
                print("    ⚪ 이벤트는 있으나 인가 기산점이 없음")
        print()
    out["elapse"] = el

    # ── 6. C게이트 — §39② 시점 요건은 서류 없이도 판정된다 ──
    print("=" * 62)
    print("■ C 게이트 — 입주권 자격 (조합원 지위 승계)")
    if site is None:
        print("  진행단계를 특정하지 못해 §39② 발동 여부를 판정할 수 없습니다.")
        print("  투기과열지구라면 13예외 중 하나가 필요하고, 그건 개인 서류가 있어야 합니다.")
    else:
        st, why = site.승계제한
        icon = {"발동": "🔒", "미발동": "🔓", "해당없음": "⚪", "확인필요": "🟡"}[st]
        print(f"  {icon} §39② 지위 양도 제한: {st}")
        print(f"     {why}")
        if st == "미발동":
            print()
            print("  → 지금 시점에서는 **서류 없이도 승계 가능**합니다(13예외 불필요).")
            print("     ⚠ 다만 계약~잔금 사이에 인가가 나면 결론이 뒤집힙니다. "
                  "잔금일 기준으로 단계를 다시 확인하세요.")
        elif st == "해당없음":
            print()
            print("  → 도시정비법 §39② 사업이 아니라 이 엔진의 스코프 밖입니다. "
                  "해당 특례법의 양도 제한을 따로 확인하세요.")
    # 양수 시점이 갈리는지 — 계약일·등기일을 알면 둘 다 돌린다
    if site is not None and (계약일 or 등기일) and el is not None:
        import engine as EN
        import elapse as EL
        kw = dict(사업유형="재건축" if site.law == "재건축" else "재개발",
                  기준일=계약일 or 등기일, 기준일_기준="미정", 투기과열지구=True)
        kw.update(EL.to_case_facts(el, site))
        if 계약일 and 등기일:
            dual = EN.evaluate_dual(EN.Case(**kw), 계약일, 등기일)
            out["dual"] = dual
            print()
            print(EN.render_dual(dual))
            print()
        else:
            g = EN.gate_at(EN.Case(**kw), 계약일 or 등기일)
            if g is not None:
                print(f"\n  · 양수 {계약일 or 등기일} 시점: {g.source_span}\n")

    if site is not None and site.승계제한[0] == "발동":
        # 인가 일자가 있으면 재건축 3년 트리(예외5~7)는 서류 없이도 돌아간다.
        import engine as EN
        import elapse as EL
        kw = dict(사업유형="재건축" if site.law == "재건축" else "재개발",
                  기준일=date.today(), 기준일_기준="미정", 투기과열지구=True,
                  제한발동=EN.Fact(True, EN.Grade.S1, "정보몽땅 사업장목록",
                                f"현재 '{site.stage}'"))
        if el is not None:
            kw.update(EL.to_case_facts(el, site))
        rep = EN.evaluate(EN.Case(**kw))
        out["succ"] = rep
        print()
        print(f"  가진 자료만으로 13예외를 돌린 결과: {rep.overall}")
        icon = {"MET": "🟢", "NOT_MET": "🔴", "INSUFFICIENT": "🟡",
                "CONFLICT": "🟠", "NA": "⚪"}
        for ex in rep.exceptions:
            v = ex.verdict.name
            if v == "NA":
                continue
            # 재건축 3년 트리는 인가 일자로 자동 판정되므로 결과를 펼쳐 보인다
            auto = ex.id.startswith(("ex5", "ex6", "ex7"))
            if v == "MET" or auto:
                why = ""
                if auto:
                    r0 = ex.reqs[0]
                    why = f" — {r0.value or r0.missing_input or r0.name}"
                print(f"    {icon[v]} {ex.label}{why}")
        if el is not None and kw.get("조합설립인가일"):
            print("    ↑ 재건축 3년 트리(예외5~7)는 위 인가 일자로 자동 판정됨"
                  " · 소유 3년 요건은 등기부 필요")
        print()
        print("  나머지 예외는 개인 서류가 있어야 확정됩니다 (공공데이터에 없음):")
        print("    📄 등기부등본 (매도인 취득일)")
        print("    📄 주민등록초본 (매도인 거주기간, 배우자·직계존비속 합산)")
        print("    📄 (해당 시) 상속·해외이주·경매 증빙")
    elif site is None or site.승계제한[0] == "확인필요":
        print()
        print("  13예외 판정에 필요한 서류 (공공데이터에 없음 — 업로드가 유일한 경로):")
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
    p.add_argument("--계약", "--contract", dest="계약", metavar="YYYY-MM-DD",
                   help="계약일 — 등기일과 함께 주면 두 시점을 각각 판정")
    p.add_argument("--등기", "--register", dest="등기", metavar="YYYY-MM-DD",
                   help="등기(잔금)일")
    a = p.parse_args(argv)
    kw = " ".join(a.keyword).strip()
    if not kw:
        p.error('주소를 입력하세요. 예: python check.py "서울 관악구 신림동 10-10"')
    run(kw, a.mock, a.by,
        _parse_date(a.계약) if a.계약 else None,
        _parse_date(a.등기) if a.등기 else None)


if __name__ == "__main__":
    main()
