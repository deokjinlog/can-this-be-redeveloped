"""python -m collect <서브커맨드>"""
import argparse

from . import districts, extract, match, paths, report


def main(argv=None):
    p = argparse.ArgumentParser(prog="collect", description="정비사업 고시문 수집·변환")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("cleanup", aliases=["districts"],
                       help="정보몽땅 사업장 → districts.csv")
    d.add_argument("--refetch", action="store_true", help="사이트에서 다시 받기")
    d.add_argument("--sigungu", help="특정 자치구 코드만")

    n = sub.add_parser("notices", help="구청 고시공고 수집")
    n.add_argument("gu", help="자치구 (예: 관악구)")
    n.add_argument("--pages", type=int, default=3)
    n.add_argument("--since", help="이 날짜 이후만 (YYYY-MM-DD)")
    n.add_argument("--limit", type=int, help="상세·첨부는 N건까지만")
    n.add_argument("--no-download", action="store_true", help="첨부는 받지 않고 목록만")

    e = sub.add_parser("convert", aliases=["extract"], help="첨부 → 텍스트")
    e.add_argument("gu")

    m = sub.add_parser("match", help="고시문 ↔ 정보몽땅 매칭")
    m.add_argument("--gu")

    h = sub.add_parser("hosts", help="eminwon 호스트 생존 확인 (25개 구)")
    h.add_argument("--only", nargs="*", help="특정 구만")

    sub.add_parser("report", help="리포트 생성")
    sub.add_parser("where", help="데이터 저장 위치")

    a = p.parse_args(argv)
    if a.cmd == "where":
        print(paths.ensure())
        return
    paths.ensure()
    if a.cmd in ("cleanup", "districts"):
        print("저장:", districts.build(a.refetch, a.sigungu))
    elif a.cmd == "notices":
        from . import notices
        r = notices.collect(a.gu, a.pages, a.since, limit=a.limit,
                            download=not a.no_download)
        print(f"{r['gu']}: 고시 {r['notices']}건(상세 조회 {r['detailed']}) · "
              f"첨부 {r['attachments']}건")
        print("  검색어별 신규:", ", ".join(f"{k} {v}" for k, v in r["per_query"].items()))
        print("  →", r["index"])
    elif a.cmd in ("convert", "extract"):
        s = extract.run(a.gu)
        print(f"{a.gu}: {s['성공']}/{s['총']} 추출 · 확장자 {s['by_ext']}")
        for nm, why in s["fail"][:8]:
            print(f"   ✗ {nm}: {why}")
    elif a.cmd == "match":
        r = match.run(a.gu)
        print(f"매칭 {r['matched']} / 미매칭 {r['unmatched']} ({r['rate']:.0%})")
        print("  →", r["files"][0])
    elif a.cmd == "hosts":
        from . import hosts
        r = hosts.scan(a.only)
        print(f"생존 {len(r['found'])} / 실패 {len(r['missed'])}")
        for gu, v in sorted(r["found"].items()):
            print(f"  ✓ {gu:<8} {v['host']:<30} {v['note']}")
        for gu in sorted(r["missed"]):
            print(f"  ✗ {gu}")
        print("  →", r["file"])
    elif a.cmd == "report":
        print("저장:", report.build())


if __name__ == "__main__":
    main()
