"""요건 모듈(A) 케이스 — '재개발 될 수 있나' (서울 조례 검증값)."""
from datetime import date
from criteria_engine import Building, Area, Fact, Grade, evaluate, render

def daejang(d, span): return Fact(d, Grade.P1, "건축물대장", span)
def gosi(r, span):    return Fact(r, Grade.S1, "정보몽땅 노후도", span)
def plan(v, span):    return Fact(v, Grade.P1, "정비계획 자료", span)

RC90 = Building(준공일=daejang(date(1990, 5, 1), "사용승인 1990-05-01"), 구조="RC공동주택")

cases = []

# ① 필수(면적·노후도70%) + 선택(과소필지45%) 충족 → 될 수 있음
cases.append(("①노후70·과소45·면적OK", "재개발 될 수 있음", RC90,
    Area(노후불량비율=gosi(0.70, "노후도 70%"), 면적=plan(15000, "구역 15,000㎡"),
         과소필지비율=plan(0.45, "과소필지 45%"))))

# ② 노후도 40%(<60%) → 필수 미달 → 요건 미달
cases.append(("②노후40", "요건 미달", RC90,
    Area(노후불량비율=gosi(0.40, "노후도 40%"), 면적=plan(15000, "구역 15,000㎡"))))

# ③ 노후도 자료 없음 → 확인필요
cases.append(("③노후미상", "확인 필요", RC90,
    Area(면적=plan(15000, "구역 15,000㎡"))))

# ④ 필수는 충족(노후65·면적OK)인데 선택 4종 전부 미달 → 요건 미달
cases.append(("④선택전부미달", "요건 미달", RC90,
    Area(노후불량비율=gosi(0.65, "노후도 65%"), 면적=plan(15000, "구역 15,000㎡"),
         과소필지비율=plan(0.30, "과소필지 30%"), 접도율=plan(0.50, "주택접도율 50%"),
         호수밀도=plan(40, "호수밀도 40호/ha"), 노후연면적비율=plan(0.50, "노후연면적 50%"))))

# ⑤ 노후도 충족인데 면적 미상 → 확인필요 (필수 자료 보완)
cases.append(("⑤면적미상", "확인 필요", RC90,
    Area(노후불량비율=gosi(0.65, "노후도 65%"), 과소필지비율=plan(0.45, "과소필지 45%"))))

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
print("샘플 — ① 재개발 될 수 있음 (필수+선택 충족)")
print("=" * 60)
print(render(evaluate(cases[0][2], cases[0][3])))

print("=" * 60)
print("샘플 — ④ 요건 미달 (선택 4종 전부 미달)")
print("=" * 60)
print(render(evaluate(cases[3][2], cases[3][3])))
