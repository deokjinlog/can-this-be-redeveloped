"""
docparse.py — 등기부등본·주민등록초본에서 판정 근거 뽑기

C게이트의 마지막 조각. 소유기간·거주기간은 공공데이터에 없어서 사용자가 서류를 줘야 하는데,
지금까지는 UI 에서 **연도를 손으로 입력**받아 그대로 MET 을 냈다. 화면 아래에는
"T 단독은 MET 불가" 라고 써놓고 실제로는 진술을 원본문서처럼 취급한 것이다.

여기서는 서류 원문을 받아 값과 **원문 한 줄(source_span)** 을 함께 돌려준다.
그래야 근거등급 P1 이 정당해지고, 붙여넣지 않으면 T 로 남아 확정되지 않는다.

  python docparse.py --등기부 sample.txt
  python docparse.py --초본 sample.txt --지번 "신림동 675-159"

⚠ 개인정보 — 서류에는 이름·주민등록번호가 들어 있다.
   · 파싱은 로컬에서만 하고 아무 데도 보내지 않는다(이 파일은 네트워크를 쓰지 않는다).
   · 주민번호는 즉시 마스킹하고, 이름은 성만 남긴다.
   · 원문을 파일로 저장하지 않는다 — 뽑은 값과 인용 한 줄만 넘긴다.
⚠ 서류 양식은 발급처·시점에 따라 달라진다. 못 읽으면 **못 읽었다고 한다**(추측 금지).
표준 라이브러리만 사용.
"""

import argparse
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

SRC_DEUNGI = "등기부등본(사용자 제출)"
SRC_CHOBON = "주민등록초본(사용자 제출)"

# 2011년6월14일 / 2011.6.14 / 2011-06-14 / 2011 년 6 월 14 일
_DATE = re.compile(r"(\d{4})\s*[년.\-/]\s*(\d{1,2})\s*[월.\-/]\s*(\d{1,2})\s*일?")
_RRN = re.compile(r"(\d{6})\s*[-–]\s*(\d{7})")          # 주민등록번호
_HYPHEN = "‐‑‒–—―−-"  # 각종 하이픈


def _norm(t: str) -> str:
    """전각·특수 하이픈·공백을 정리해 양식 차이를 흡수한다."""
    t = unicodedata.normalize("NFKC", t or "")
    t = re.sub(f"[{_HYPHEN}]", "-", t)
    t = t.replace(" ", " ")
    return t


def mask(t: str) -> str:
    """개인정보 마스킹 — 주민번호 뒷자리, 이름은 성만."""
    t = _RRN.sub(lambda m: m.group(1) + "-*******", t)
    t = re.sub(r"(소유자|공유자|채무자)\s+([가-힣])[가-힣]{1,3}", r"\1 \2○○", t)
    return t


def _d(m) -> date:
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


@dataclass
class Row:
    date: Optional[date]
    kind: str          # 등기목적 / 변동사유
    reason: str = ""   # 등기원인 (매매·상속·증여 …)
    line: str = ""     # 원문 한 줄 (마스킹 후)


@dataclass
class Deungi:
    """등기부 갑구에서 읽은 소유권 이력."""
    rows: list = field(default_factory=list)
    warn: list = field(default_factory=list)

    @property
    def 최근취득(self) -> Optional[Row]:
        """현재 소유자의 취득 — 소유권 이전/보존 중 가장 나중 것."""
        got = [r for r in self.rows if r.date and ("이전" in r.kind or "보존" in r.kind)]
        return max(got, key=lambda r: r.date) if got else None

    @property
    def 상속이혼(self) -> bool:
        """§39② 본문 — 상속·이혼으로 취득했으면 '양수' 가 아니라 제한 대상이 아니다.

        등기원인 표기가 '협의분할에 의한 상속' 처럼 길어질 수 있어 원문 줄까지 함께 본다.
        """
        r = self.최근취득
        if not r:
            return False
        return bool(re.search(r"상속|이혼|재산분할", r.reason + " " + r.line))


@dataclass
class Chobon:
    """초본 주소 변동 이력."""
    rows: list = field(default_factory=list)
    warn: list = field(default_factory=list)

    def 거주개시(self, 지번: str = "") -> Optional[Row]:
        """그 지번으로 **처음 전입한 날**. 지번을 안 주면 가장 이른 전입."""
        cand = [r for r in self.rows if r.date and "전출" not in r.kind]
        if 지번:
            key = re.sub(r"\s+", "", 지번)
            cand = [r for r in cand if key in re.sub(r"\s+", "", r.line)]
        return min(cand, key=lambda r: r.date) if cand else None


# ── 등기부 ──

# 표제 형태(【 갑 구 】 / '갑구 (소유권에 관한 사항)')를 우선 찾는다.
# 본문에 '갑구' 라는 낱말이 지나가는 것만으로 거기서 자르면 엉뚱한 데를 읽는다.
_GAP_STRONG = re.compile(r"(?:【\s*갑\s*구\s*】|갑\s*구\s*[\(（]?\s*소유권에\s*관한)")
_GAP_WEAK = re.compile(r"갑\s*구")
_EUL = re.compile(r"(?:【\s*을\s*구\s*】|을\s*구\s*[\(（]?\s*소유권\s*이외)")
_PURPOSE = re.compile(r"(소유권\s*(?:이전|보존|일부이전)|가등기|압류|가압류|경매개시결정|신탁)")
# 긴 표현이 앞에 와야 한다 — '협의분할에 의한 상속' 을 '협의분할' 로 끊으면 상속을 놓친다
_REASON = re.compile(
    r"(협의분할에\s*의한\s*상속|재산분할에\s*의한\s*이혼|협의분할|재산분할"
    r"|상속|증여|매매|교환|이혼|공매|경매|수용|판결|신탁)")


def parse_deungi(text: str) -> Deungi:
    t = _norm(text)
    out = Deungi()
    gap = _GAP_STRONG.search(t)
    if not gap:
        gap = _GAP_WEAK.search(t)
        if gap:
            out.warn.append("'【 갑구 】' 표제를 못 찾아 '갑구' 낱말 위치부터 읽었습니다 — 결과를 확인하세요")
    if not gap:
        out.warn.append("'갑구' 를 못 찾음 — 등기부등본 전체(갑구 포함)를 붙여넣었는지 확인")
        body = t
    else:
        eul = _EUL.search(t, gap.end())
        body = t[gap.end():eul.start() if eul else len(t)]

    # 표가 줄바꿈으로 흩어지므로 '순위번호로 시작하는 덩어리' 단위로 자른다
    chunks = re.split(r"\n(?=\s*\d{1,3}(?:-\d+)?\s+)", body)
    for ch in chunks:
        p = _PURPOSE.search(ch)
        if not p:
            continue
        dates = list(_DATE.finditer(ch))
        if not dates:
            continue
        kind = re.sub(r"\s+", "", p.group(1))
        # 접수일이 먼저, 등기원인일자가 뒤에 오는 양식 → 첫 날짜를 접수일로 본다
        rec = _d(dates[0])
        rsn = _REASON.search(ch)
        line = mask(re.sub(r"\s+", " ", ch).strip())[:160]
        out.rows.append(Row(rec, kind, rsn.group(1) if rsn else "", line))

    if not out.rows:
        out.warn.append("소유권 관련 등기를 못 읽음 — 양식이 다르면 취득일을 직접 입력하세요")
    return out


# ── 초본 ──

_MOVE = re.compile(r"(전입|전출|세대주변경|주소변경|재등록|말소)")


def parse_chobon(text: str) -> Chobon:
    t = _norm(text)
    out = Chobon()
    for raw in t.split("\n"):
        line = raw.strip()
        if len(line) < 8:
            continue
        m = _DATE.search(line)
        if not m:
            continue
        if not re.search(r"(시|도)\s|구\s|동\s|로\s|길\s", line) and not _MOVE.search(line):
            continue
        mv = _MOVE.search(line)
        out.rows.append(Row(_d(m), mv.group(1) if mv else "주소",
                            "", mask(re.sub(r"\s+", " ", line))[:160]))
    if not out.rows:
        out.warn.append("주소 변동 이력을 못 읽음 — '주소 변동 사항' 이 포함된 초본인지 확인")
    return out


# ── engine 연결 ──

def to_case_facts(d: Optional[Deungi] = None, c: Optional[Chobon] = None,
                  지번: str = "", 발급일: Optional[date] = None) -> dict:
    """서류 → engine.Case Fact (P1). 못 읽은 건 넣지 않는다."""
    from engine import Fact, Grade
    out = {}
    if d is not None:
        r = d.최근취득
        if r:
            out["취득일"] = Fact(r.date, Grade.P1, SRC_DEUNGI,
                              f"갑구 {r.kind} 접수 {r.date}"
                              + (f" · 원인 {r.reason}" if r.reason else ""),
                              doc_asof=발급일)
            if d.상속이혼:
                out["취득사유_상속이혼"] = Fact(
                    True, Grade.P1, SRC_DEUNGI,
                    f"갑구 등기원인 '{r.reason}' — §39② 의 '양수' 에서 제외", doc_asof=발급일)
    if c is not None:
        r = c.거주개시(지번)
        if r:
            out["거주개시일"] = Fact(r.date, Grade.P1, SRC_CHOBON,
                                 f"초본 {r.kind} {r.date}" + (f" · {지번}" if 지번 else ""),
                                 doc_asof=발급일)
    return out


def render(d: Optional[Deungi] = None, c: Optional[Chobon] = None, 지번: str = "") -> str:
    L = []
    if d is not None:
        L.append(f"■ 등기부 갑구 — 소유권 이력 {len(d.rows)}건")
        for r in sorted([x for x in d.rows if x.date], key=lambda x: x.date):
            L.append(f"   {r.date}  {r.kind:<10} {r.reason or '':<6} {r.line[:70]}")
        r = d.최근취득
        L.append(f"   → 최근 취득: {r.date} ({r.reason or '원인 미상'})" if r else "   → 취득일을 못 읽음")
        if d.상속이혼:
            L.append("   ⚠ 상속·이혼 취득 → §39② 의 '양수' 가 아니라 지위 양도 제한 대상이 아님")
        for w in d.warn:
            L.append(f"   ⚠ {w}")
    if c is not None:
        L.append(f"\n■ 초본 — 주소 변동 {len(c.rows)}건")
        for r in sorted([x for x in c.rows if x.date], key=lambda x: x.date)[:12]:
            L.append(f"   {r.date}  {r.kind:<8} {r.line[:70]}")
        r = c.거주개시(지번)
        L.append(f"   → 거주 개시: {r.date}" if r else
                 f"   → {지번 or '대상 주소'} 전입 기록을 못 찾음")
        for w in c.warn:
            L.append(f"   ⚠ {w}")
    L.append("\n※ 개인정보는 마스킹했고 원문은 저장하지 않습니다. 값과 인용 한 줄만 판정에 씁니다.")
    return "\n".join(L)


def main(argv=None):
    p = argparse.ArgumentParser(description="등기부·초본에서 판정 근거 추출")
    p.add_argument("--등기부", "--deungi", dest="deungi", help="등기부등본 텍스트 파일")
    p.add_argument("--초본", "--chobon", dest="chobon", help="주민등록초본 텍스트 파일")
    p.add_argument("--지번", dest="jibun", default="", help="대상 주택 지번 (거주 판정용)")
    a = p.parse_args(argv)
    if not (a.deungi or a.chobon):
        p.error("--등기부 또는 --초본 파일을 주세요")
    d = parse_deungi(open(a.deungi, encoding="utf-8").read()) if a.deungi else None
    c = parse_chobon(open(a.chobon, encoding="utf-8").read()) if a.chobon else None
    print(render(d, c, a.jibun))


if __name__ == "__main__":
    main()
