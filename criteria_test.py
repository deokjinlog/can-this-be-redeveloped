"""요건 모듈(A) 케이스 — '재개발 될 수 있나'."""
from datetime import date
from criteria_engine import Building, Area, Fact, Grade, evaluate, render

def daejang(d, span): return Fact(d, Grade.P1, "건축물대장", span)
def gosi(r, span):    return Fact(r, Grade.S1, "정보몽땅 노후도", span)
def plan(v, span):    return Fact(v, Grade.P1, "정비계획 자료", span)

cases = []

# ① 노후도 70% + 과소필지 45% + 내 건물 RC 1990준공 → 노후도 OK, 선택은 조례 미검증 → 확인필요
cases.append(("①노후도70·과소45", "확인 필요",
    Building(준공일=daejang(date(1990, 5, 1), "사용승인 1990-05-01"), 구조="RC공동주택"),
    Area(사업유형="재개발", 지역="서울", 노후불량비율=gosi(0.70, "노후도 70%"),
         과소필지비율=plan(0.45, "과소필지 45%"))))

# ② 지역 노후도 40% (<60%) → 필수요건 미달 → 요건 미달
cases.append(("②노후도40", "요건 미달",
    Building(준공일=daejang(date(2000, 1, 1), "사용승인 2000-01-01"), 구조="RC공동주택"),
    Area(사업유형="재개발", 지역="서울", 노후불량비율=gosi(0.40, "노후도 40%"))))

# ③ 노후도 자료 없음 → 확인필요 (정보몽땅 노후도 필요)
cases.append(("③노후도미상", "확인 필요",
    Building(준공일=daejang(date(1988, 3, 1), "사용승인 1988-03-01"), 구조="RC공동주택"),
    Area(사업유형="재개발", 지역="서울")))

passed = 0
for name, expect, b, a in cases:
    rep = evaluate(b, a)
    ok = rep.overall == expect
    passed += ok
    print(f"{'✅' if ok else '❌'} {name}: {rep.overall}  (기대 {expect})")
    if not ok:
        print(render(rep))

print(f"\n{passed}/{len(cases)} 통과\n")

print("=" * 60)
print("샘플 — ① 노후도 충족·선택요건 미검증 → 확인필요")
print("=" * 60)
print(render(evaluate(cases[0][2], cases[0][3])))

print("=" * 60)
print("샘플 — ② 지역 노후도 미달 → 요건 미달(선행 필수)")
print("=" * 60)
print(render(evaluate(cases[1][2], cases[1][3])))
