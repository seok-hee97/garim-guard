"""발표자료 PDF 내보내기 — PPTX 하나에서 PDF 를 파생시킨다(단일 소스 규율).

    cd scripts/pptx && npm run build      # ① report/발표자료.pptx + 발표자료_대본.md
    python -m scripts.export_deck_pdf     # ② report/발표자료.pdf  ← 이 스크립트

★왜 Keynote 인가★
  PPTX → PDF 변환기는 한글 조판 품질이 갈린다. LibreOffice 는 CJK 와 라틴 문장부호 사이에
  공백을 넣어 제목이 이렇게 깨진다:
      LibreOffice : 공격은 ' 사용자 ' 가 아니라 ' 도구 ' 로 들어온다   ← 12장 전부 영향
      Keynote     : 공격은 '사용자'가 아니라 '도구'로 들어온다        ← 정상
  투사용 PDF 는 "어느 PC 에서도 그대로 보이는 것"이 존재 이유이므로 조판을 희생할 수 없다.
  그래서 macOS 네이티브(Keynote)를 1순위로 쓰고, 없을 때만 LibreOffice 로 떨어진다
  (그 경우 위 결함을 경고로 알린다 — 조용히 나쁜 산출물을 만들지 않는다).

의존: macOS + Keynote(권장) / 또는 LibreOffice(폴백). 둘 다 없으면 exit 3.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PPTX = ROOT / "report" / "발표자료.pptx"
PDF = ROOT / "report" / "발표자료.pdf"

KEYNOTE = Path("/Applications/Keynote.app")
SOFFICE = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")

_APPLESCRIPT = """
set inFile to POSIX file "{src}"
set outFile to POSIX file "{dst}"
tell application "Keynote"
  set d to open inFile
  export d to outFile as PDF with properties {{PDF image quality:Best}}
  close d saving no
end tell
"""


def _via_keynote(src: Path, dst: Path) -> bool:
    if not KEYNOTE.exists():
        return False
    # Keynote 는 기존 파일을 덮어쓰지 않고 실패하므로 임시 경로로 뽑고 옮긴다.
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "deck.pdf"
        script = _APPLESCRIPT.format(src=src, dst=tmp)
        r = subprocess.run(["osascript", "-e", script], capture_output=True,
                           text=True, timeout=600)
        if not tmp.exists() or tmp.stat().st_size == 0:
            print(f"✗ Keynote 내보내기 실패(rc={r.returncode})\n{r.stderr[-600:]}",
                  file=sys.stderr)
            return False
        shutil.copyfile(tmp, dst)
    return True


def _via_soffice(src: Path, dst: Path) -> bool:
    exe = str(SOFFICE) if SOFFICE.exists() else (shutil.which("soffice") or "")
    if not exe:
        return False
    print("⚠️ Keynote 없음 → LibreOffice 폴백. 한글 문장부호 주변에 공백이 삽입되는 "
          "조판 결함이 있으니 최종 제출본은 Keynote/PowerPoint 로 다시 뽑을 것.",
          file=sys.stderr)
    with tempfile.TemporaryDirectory() as td:
        subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", td, str(src)],
                       capture_output=True, text=True, timeout=600)
        made = Path(td) / (src.stem + ".pdf")
        if not made.exists():
            return False
        shutil.copyfile(made, dst)
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pptx", default=str(PPTX))
    ap.add_argument("--out", default=str(PDF))
    ap.add_argument("--engine", choices=("auto", "keynote", "soffice"), default="auto")
    args = ap.parse_args()

    src, dst = Path(args.pptx), Path(args.out)
    if not src.exists():
        print(f"✗ {src} 없음 — 먼저 `cd scripts/pptx && npm run build`", file=sys.stderr)
        return 2
    dst.parent.mkdir(parents=True, exist_ok=True)

    order = {"auto": (_via_keynote, _via_soffice),
             "keynote": (_via_keynote,), "soffice": (_via_soffice,)}[args.engine]
    for fn in order:
        if fn(src, dst):
            print(f"→ {dst.relative_to(ROOT)}  ({dst.stat().st_size // 1024}KB, "
                  f"{fn.__name__.removeprefix('_via_')})")
            return 0
    print("✗ 변환기를 찾지 못했습니다(Keynote/LibreOffice). PPTX 를 직접 열어 PDF 로 내보내세요.",
          file=sys.stderr)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
