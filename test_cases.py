"""입주권 신호등 — 6개 골든 케이스 회귀 테스트.
문서의 '흔히 잘못 알려진 두 가지'(재건축 전용 오인, 초본 미제출 반올림)를 회귀로 박음."""
from datetime import date
from engine import Case, Fact, Grade, evaluate, render, to_dict

D = date(2026, 9, 1)          # 기준일(양수일)
ISSUE_DEUNGI = date(2026, 8, 20)
ISSUE_CHOBON = date(2026, 8, 25)


def deungi(d, span):
    return Fact(d, Grade.P1, "등기부(2026-08-20 발급)", span, doc_asof=ISSUE_DEUNGI)

def chobon(d, span):
    return Fact(d, Grade.P1, "주민등록초본(2026-08-25 발급)", span, doc_asof=ISSUE_CHOBON)

def gosi(d, span):
    return Fact(d, Grade.S1, "정보몽땅 고시", span)


cases = []

# ① 소유 15년 + 거주 3년 → 불충족(둘 다 필요)
cases.append(("①소유15·거주3", "승계불가(현금청산 대상)", Case(
    사업유형="재개발", 기준일=D, 기준일_기준="등기일", 투기과열지구=True,
    취득일=deungi(date(2011, 6, 14), "갑구 순위3 소유권이전 접수 2011년6월14일"),
    거주개시일=chobon(date(2023, 9, 1), "2023-09-01 전입"),
)))

# ② 소유 12년 + 배우자 거주 합산 5년 → 충족
cases.append(("②소유12·합산거주5", "승계가능", Case(
    사업유형="재개발", 기준일=D, 기준일_기준="등기일", 투기과열지구=True,
    취득일=deungi(date(2014, 9, 1), "갑구 소유권이전 접수 2014년9월1일"),
    거주개시일=chobon(date(2021, 6, 1), "본인 2023~ + 배우자 2021~ 합산 5.3년"),
)))

# ③ 재개발인데 '사업시행인가 3년 지연' → 불충족(①②는 재건축 전용)
cases.append(("③재개발·3년지연오인", "승계불가(현금청산 대상)", Case(
    사업유형="재개발", 기준일=D, 기준일_기준="등기일", 투기과열지구=True,
    취득일=deungi(date(2024, 9, 1), "갑구 접수 2024년9월1일"),
    거주개시일=chobon(date(2025, 9, 1), "2025-09-01 전입"),
    조합설립인가일=gosi(date(2020, 1, 1), "조합설립인가 2020-01-01"),
    사업시행계획인가일=Fact(None, Grade.S1, "정보몽땅", "사업시행계획인가 신청 이력 없음"),
)))

# ④ 조합설립인가 3년 초과 미신청 + 소유 2년 → 불충족(둘 다 3년)
cases.append(("④설립3년초과·소유2년", "승계불가(현금청산 대상)", Case(
    사업유형="재건축", 기준일=D, 기준일_기준="등기일", 투기과열지구=True,
    취득일=deungi(date(2024, 9, 1), "갑구 접수 2024년9월1일"),
    거주개시일=chobon(date(2025, 9, 1), "2025-09-01 전입"),
    조합설립인가일=gosi(date(2022, 1, 1), "조합설립인가 2022-01-01"),
    사업시행계획인가일=Fact(None, Grade.S1, "정보몽땅", "사업시행계획인가 신청 없음"),
    착공일=Fact(None, Grade.S1, "정보몽땅", "착공 없음"),
)))

# ⑤ 등기부 취득일 ≠ 조합명부 등재일 → CONFLICT
cases.append(("⑤등기≠조합명부", "확인필요", Case(
    사업유형="재개발", 기준일=D, 기준일_기준="등기일", 투기과열지구=True,
    취득일=deungi(date(2014, 9, 1), "갑구 접수 2014년9월1일"),
    조합명부_등재일=Fact(date(2018, 3, 15), Grade.P1, "조합명부", "조합원 등재 2018-03-15"),
    거주개시일=chobon(date(2020, 9, 1), "2020-09-01 전입"),
)))

# ⑥ 초본 미제출 → INSUFFICIENT, 승계불가로 반올림 안 됨
cases.append(("⑥초본미제출", "확인필요", Case(
    사업유형="재개발", 기준일=D, 기준일_기준="등기일", 투기과열지구=True,
    취득일=deungi(date(2014, 9, 1), "갑구 접수 2014년9월1일"),
    거주개시일=None,
)))


passed = 0
for name, expect, c in cases:
    rep = evaluate(c)
    ok = rep.overall == expect
    passed += ok
    print(f"{'✅' if ok else '❌'} {name}: {rep.overall}  (기대 {expect})")
    if not ok:
        print(render(rep))

print(f"\n{passed}/{len(cases)} 통과\n")

print("=" * 64)
print("샘플 리포트 — ⑥ 초본 미제출 (확인필요 + 요청서류 수렴)")
print("=" * 64)
print(render(evaluate(cases[5][2])))

print("=" * 64)
print("샘플 리포트 — ④ 재건축 OR/AND (설립3년초과 MET 이지만 소유3년 미달)")
print("=" * 64)
print(render(evaluate(cases[3][2])))

print("=" * 64)
print("샘플 리포트 — ⑤ CONFLICT (전문가로)")
print("=" * 64)
print(render(evaluate(cases[4][2])))
