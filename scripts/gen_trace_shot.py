"""발표 슬라이드용 트레이스 뷰어 스크린샷 — 재현 가능한 캡처(수동 캡처 금지).

    python -m app.trace_view          # 먼저 HTML 갱신
    python -m scripts.gen_trace_shot  # → report/figs/trace_view.png

`app.trace_view --sections timeline` 로 ① 실행과정 타임라인만 담은 HTML 을 임시 생성한 뒤
헤드리스 Chrome 으로 찍는다. 애블레이션 표 행 수가 늘어도 프레임에 걸리지 않는다.
슬라이드(`![w:1040](figs/trace_view.png)`)가 이 파일을 그대로 쓴다.

★수동 캡처를 금지하는 이유★: 뷰어 CSS 를 고쳐도 PNG 가 안 따라오면 슬라이드에 옛 렌더가
남는다(배지 잘림 회귀). 이 스크립트가 있으면 `gen_slide_figs` 처럼 한 명령으로 재생성된다.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRACES = ROOT / "results" / "demo_traces.jsonl"
OUT = ROOT / "report" / "figs" / "trace_view.png"

# macOS / Linux 표준 설치 경로 — 없으면 PATH 에서 탐색한다.
CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]


def _find_chrome() -> str | None:
    for c in CANDIDATES:
        if Path(c).exists():
            return c
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--traces", default=str(TRACES), help="에피소드 hop 트레이스 JSONL")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--width", type=int, default=2000)
    ap.add_argument("--height", type=int, default=540, help="창 높이 = 크롭 높이")
    # ★after_tool 전문을 실으면 세로가 늘어 차단 배지가 프레임 밖으로 밀린다★ —
    # 슬라이드에서 잘리면 안 되는 건 차단 배지이므로 미리보기를 줄여 4홉을 다 담는다.
    ap.add_argument("--preview-chars", type=int, default=150,
                    help="after_tool 미리보기 축약 길이(0=전문)")
    args = ap.parse_args()

    if not Path(args.traces).exists():
        print(f"✗ {args.traces} 없음 — 먼저 `python -m scripts.run_demo` 실행", file=sys.stderr)
        return 2

    chrome = _find_chrome()
    if chrome is None:
        print("✗ Chrome/Chromium 을 찾지 못했습니다. 뷰어 HTML 을 브라우저로 열어 수동 캡처하세요.",
              file=sys.stderr)
        return 3

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        html = Path(td) / "trace_slide.html"
        rc = subprocess.run(
            [sys.executable, "-m", "app.trace_view", "--sections", "timeline",
             "--preview-chars", str(args.preview_chars),
             "--traces", args.traces, "--out", str(html)],
            cwd=ROOT, capture_output=True, text=True, timeout=120)
        if not html.exists():
            print(f"✗ 슬라이드용 HTML 생성 실패\n{rc.stderr[-800:]}", file=sys.stderr)
            return 1
        cmd = [
            chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--no-sandbox", "--force-device-scale-factor=1",
            f"--window-size={args.width},{args.height}",
            f"--screenshot={out}", html.resolve().as_uri(),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if not out.exists() or out.stat().st_size == 0:
        print(f"✗ 캡처 실패(rc={proc.returncode})\n{proc.stderr[-800:]}", file=sys.stderr)
        return 1
    print(f"→ {out.relative_to(ROOT)}  ({args.width}×{args.height}, {out.stat().st_size // 1024}KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
