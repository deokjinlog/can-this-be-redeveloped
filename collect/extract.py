"""첨부 → 텍스트.

관악구 실측으로는 첨부가 대부분 PDF 다. HWP 가 섞이면 변환이 필요한데 이 환경엔
LibreOffice 가 없고(sudo 불가) 설치도 못 한다 → HWP 는 pyhwp(uv) 로 텍스트만 뽑는다.
어차피 목적이 텍스트라 PDF 를 경유할 이유가 없다.

  PDF  : pdfminer.six
  HWP  : pyhwp 의 hwp5txt
  HWPX : zip + XML 이라 표준 라이브러리로 읽는다
"""
import csv
import glob
import os
import re
import subprocess
import zipfile

from . import paths

UV = os.path.expanduser("~/.local/bin/uv")
VENV = os.path.join(paths.ROOT, ".venv-extract")   # 리포 밖. 한 번 만들고 재사용


def _tools() -> dict:
    """추출 전용 환경.

    `uv run --with` 를 파일마다 부르면 매번 의존성을 다시 풀어 66개에 수 분이 걸린다
    (실측: 100건에서 사실상 멈춤). venv 를 한 번 만들고 그 안의 실행파일을 직접 쓴다.
    """
    bin_ = os.path.join(VENV, "bin")
    t = {n: os.path.join(bin_, n) for n in ("hwp5txt", "python")}
    if not os.path.exists(t["hwp5txt"]):
        subprocess.run([UV, "venv", VENV], capture_output=True, timeout=300)
        subprocess.run([UV, "pip", "install", "--quiet", "--python", t["python"],
                        "pyhwp", "six", "olefile", "pdfminer.six"],
                       capture_output=True, timeout=900)
    return t


def _hwpx(path: str) -> str:
    """HWPX 는 zip + XML 이라 표준 라이브러리로 읽는다.

    Contents/ 전체를 읽으면 header.xml 의 번호매기기·스타일 정의까지 딸려와
    본문 앞에 '^1. ^2. ^3)' 같은 찌꺼기가 붙는다 → 본문(section*.xml)만 읽는다.
    """
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist()
                 if re.search(r"section\d*\.xml$", n, re.I)]
        if not names:      # 구조가 다르면 Contents 전체로 물러선다
            names = [n for n in z.namelist()
                     if n.startswith("Contents/") and n.endswith(".xml")]
        buf = []
        for n in sorted(names):
            x = z.read(n).decode("utf-8", "replace")
            x = re.sub(r"<hp:lineBreak[^>]*/>|</hp:p>", "\n", x)
            buf.append(re.sub(r"<[^>]+>", " ", x))
    t = " ".join(buf)
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


def _run(cmd: list, timeout=120) -> tuple[bool, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if r.returncode == 0 and r.stdout.strip():
            return True, r.stdout.decode("utf-8", "replace")
        return False, (r.stderr or b"").decode("utf-8", "replace")[:200]
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def to_text(path: str, fmt: str = "") -> tuple[str, str]:
    """(텍스트, 사유). fmt 를 주면 그걸 쓰고, 없으면 매직바이트로 판정한다.
    확장자는 믿지 않는다 — 이름이 .hwpx 인데 내용이 구형 HWP 인 파일이 실제로 있다."""
    from .notices import sniff
    ext = fmt or sniff(path) or os.path.splitext(path)[1].lower().lstrip(".")
    t = _tools()
    if ext == "pdf":
        ok, out = _run([t["python"], "-c",
                        "import sys\nfrom pdfminer.high_level import extract_text\n"
                        "print(extract_text(sys.argv[1]))", path])
        if ok:
            return out, "pdf/pdfminer"
        # 텍스트 레이어가 없는 스캔본 — 실패가 아니라 'OCR 이 필요한 것' 이다.
        # 다음 단계(VLM 추출)가 골라야 할 대상이므로 사유를 구분해 남긴다.
        code = ("import sys\nfrom pdfminer.high_level import extract_text\n"
                "sys.exit(0 if extract_text(sys.argv[1]).strip() else 9)")
        r = subprocess.run([t["python"], "-c", code, path], capture_output=True, timeout=120)
        if r.returncode == 9:
            return "", "이미지 PDF(텍스트 레이어 없음) — OCR 필요"
        return "", f"pdf 실패: {out[:80]}"
    if ext == "hwpx":
        try:
            return _hwpx(path), "hwpx/zip"
        except Exception as e:
            return "", f"hwpx 실패: {type(e).__name__}"
    if ext == "hwp":
        ok, out = _run([t["hwp5txt"], path])
        return (out, "hwp/pyhwp") if ok else ("", f"hwp 실패: {out[:80]}")
    if ext in ("txt", "csv"):
        return open(path, encoding="utf-8", errors="replace").read(), "plain"
    return "", f"미지원 확장자: {ext}"


def run(gu: str) -> dict:
    src = paths.gu_dir(gu, "raw")
    dst = paths.gu_dir(gu, "text")
    stat = {"총": 0, "성공": 0, "실패": 0, "by_ext": {}, "fail": []}
    for p in sorted(glob.glob(os.path.join(src, "*"))):
        if os.path.isdir(p):
            continue
        stat["총"] += 1
        ext = os.path.splitext(p)[1].lower().lstrip(".")
        stat["by_ext"][ext] = stat["by_ext"].get(ext, 0) + 1
        out = os.path.join(dst, os.path.basename(p) + ".txt")
        if os.path.exists(out) and os.path.getsize(out) > 0:
            stat["성공"] += 1
            continue
        txt, why = to_text(p)
        if txt.strip():
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(txt)
            stat["성공"] += 1
        else:
            stat["실패"] += 1
            stat["fail"].append((os.path.basename(p), why))
    return stat
