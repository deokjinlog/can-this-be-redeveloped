from .eminwon import EminwonAdapter
from .gwanak import Gwanak

# CMS 어댑터 — 첨부까지 받을 수 있는 곳. robots 를 지킬 수 있는 구만 등록한다.
ADAPTERS = {a.gu: a for a in (Gwanak,)}


def get(gu: str):
    if gu not in ADAPTERS:
        raise SystemExit(f"CMS 어댑터 없음: {gu} (가능: {', '.join(ADAPTERS)})")
    return ADAPTERS[gu]()


def eminwon(gu: str, host: str) -> EminwonAdapter:
    """메타데이터 전용(첨부 불가). 호스트만 주면 어느 구든 된다."""
    return EminwonAdapter(gu, host)
