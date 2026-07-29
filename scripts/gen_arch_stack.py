"""기술스택 아키텍처 이미지 생성 — 계층 × 실제 사용 기술(브랜드 아이콘) 한 장.

    python -m scripts.gen_arch_stack          # → docs/architecture_stack.png (+ .svg)

기존 `gen_model_arch.py`(graphviz 제어흐름 다이어그램)와 **역할이 다르다**:
  - gen_model_arch  : 요청이 어떤 seam 을 어떤 순서로 통과하는가(제어 흐름). 밀도 높음 → 부록/문서용.
  - gen_arch_stack  : 어떤 계층을 **무슨 기술로** 구현했는가(스택). 발표 슬라이드 본문용.

정확성 규율 — 이 그림에 적는 기술은 전부 저장소에 실재하는 것만 쓴다.
  · FastAPI/uvicorn/Pydantic  → src/agent/server.py
  · google-genai / openai      → src/providers/{gemini,openai_provider}.py
  · Presidio + KR 인식기       → src/dlp/{vault_presidio,recognizers_ko}.py
  · transformers(Kanana·ShieldGemma·KLUE-RoBERTa) → src/rails/*, models/gpu_trained/
  · scikit-learn(TF-IDF·e5+LR) → src/rails/{ko_agentguard,e5_lr}.py
  · pytest / Docker            → tests/, Dockerfile
미구현(Tier C)은 회색 + '설계전용'으로 표기한다. 아이콘이 없는 기술(OpenAI·Presidio·uvicorn 등)은
색 글자칩으로 대체 — ★없는 아이콘을 비슷한 걸로 대신 쓰지 않는다(오표기 방지)★.

아이콘: scripts/assets/brand_icons.json (simple-icons, CC0 — 경로 데이터만 벤더링).
렌더: SVG 조립 → 헤드리스 Chrome 으로 PNG. matplotlib/graphviz 불필요.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICONS = json.loads((ROOT / "scripts" / "assets" / "brand_icons.json").read_text("utf-8"))
OUT_PNG = ROOT / "docs" / "architecture_stack.png"
OUT_SVG = ROOT / "docs" / "architecture_stack.svg"

W, H = 1680, 720
FONT = "'Apple SD Gothic Neo','AppleGothic','Malgun Gothic','Noto Sans KR',sans-serif"
MONO = "ui-monospace,'SF Mono',Menlo,monospace"

INK = "#16213e"
MUTED = "#5b6b7f"
LINE = "#c9d4e0"
GREEN = "#1b5e20"
GREEN_BG = "#e8f5e9"
BLUE = "#1565c0"
BLUE_BG = "#e3f2fd"
TEAL = "#00695c"
TEAL_BG = "#e0f2f1"
ORANGE = "#e65100"
ORANGE_BG = "#fff3e0"
RED = "#b1361e"
GREY = "#78909c"
GREY_BG = "#f4f6f8"


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _tw(text: str, size: float) -> float:
    """텍스트 폭 근사 — 한글/전각은 1.0em, ASCII 는 0.55em. 칩 폭 계산용(레이아웃 전용)."""
    w = 0.0
    for ch in text:
        w += 1.0 if ord(ch) > 0x2000 else 0.56
    return w * size


def icon(name: str, x: float, y: float, size: float, color: str | None = None) -> str:
    """simple-icons 24x24 path 를 (x,y) 좌상단 기준 size 로 배치."""
    ic = ICONS[name]
    fill = color or ("#" + ic["hex"])
    s = size / 24.0
    return (f'<g transform="translate({x:.1f},{y:.1f}) scale({s:.4f})">'
            f'<path d="{ic["path"]}" fill="{fill}"/></g>')


def chip(x: float, y: float, label: str, *, ico: str | None = None, fg: str = INK,
         bg: str = "#ffffff", border: str = LINE, size: float = 15.0,
         mono: bool = False, dashed: bool = False,
         ico_color: str | None = None) -> tuple[str, float]:
    """아이콘+라벨 칩 하나. (svg, 폭) 반환 — 호출측이 폭을 받아 가로로 흘린다."""
    pad, gap, ih = 10.0, 7.0, size * 1.15
    tw = _tw(label, size)
    w = pad + (ih + gap if ico else 0) + tw + pad
    h = 30.0
    dash = ' stroke-dasharray="4 3"' if dashed else ""
    parts = [f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h}" rx="8" '
             f'fill="{bg}" stroke="{border}" stroke-width="1.2"{dash}/>']
    tx = x + pad
    if ico:
        # 브랜드색이 너무 밝아 흰 배경에서 안 보이는 아이콘(Kakao 노랑 등)은 ico_color 로 대비 확보.
        parts.append(icon(ico, x + pad, y + (h - ih) / 2, ih, ico_color))
        tx += ih + gap
    ff = MONO if mono else FONT
    parts.append(f'<text x="{tx:.1f}" y="{y + h / 2 + size * 0.36:.1f}" font-family="{ff}" '
                 f'font-size="{size}" fill="{fg}">{_esc(label)}</text>')
    return "".join(parts), w


def chips_row(x: float, y: float, items: list[dict], gap: float = 9.0) -> str:
    out, cx = [], x
    for it in items:
        svg, w = chip(cx, y, it.pop("label"), **it)
        out.append(svg)
        cx += w + gap
    return "".join(out)


def band(x: float, y: float, w: float, h: float, *, bg: str, border: str) -> str:
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{bg}" '
            f'stroke="{border}" stroke-width="1.6"/>')


def band_title(x: float, y: float, num: str, title: str, sub: str, color: str) -> str:
    return (f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="19" font-weight="700" '
            f'fill="{color}">{_esc(num + " " + title)}</text>'
            f'<text x="{x}" y="{y + 21}" font-family="{FONT}" font-size="13" '
            f'fill="{MUTED}">{_esc(sub)}</text>')


def seam_flow(x: float, y: float) -> str:
    """4-seam 흐름 한 줄 — before_tool 만 강조(강제 지점)."""
    seams = [("before_model", False), ("모델(P-LLM)", False), ("after_model", False),
             ("before_tool 게이트", True), ("도구 실행", False), ("after_tool", False)]
    out, cx = [], x
    for label, hot in seams:
        fg, bg, bd = (("#ffffff", GREEN, GREEN) if hot else (INK, "#ffffff", LINE))
        svg, w = chip(cx, y, label, fg=fg, bg=bg, border=bd, size=14.5,
                      mono=not hot and "모델" not in label and "도구" not in label)
        out.append(svg)
        cx += w
        if label != "after_tool":
            out.append(f'<path d="M{cx + 4:.1f} {y + 15} L{cx + 18:.1f} {y + 15}" '
                       f'stroke="{MUTED}" stroke-width="1.6" marker-end="url(#ah)"/>')
            cx += 26
    # 반복 화살표
    out.append(f'<path d="M{cx - 30:.1f} {y + 34} q -{(cx - x) / 2:.0f} 26 -{cx - x - 40:.0f} 0" '
               f'stroke="{MUTED}" stroke-width="1.4" fill="none" stroke-dasharray="5 4" '
               f'marker-end="url(#ah)"/>')
    out.append(f'<text x="{x + (cx - x) / 2 - 14:.1f}" y="{y + 62}" font-family="{FONT}" '
               f'font-size="12" fill="{MUTED}">반복(iteration cap · deadline)</text>')
    return "".join(out)


def build() -> str:
    def card(x: int, y: int, w: int, h: int, title: str, *, bg: str = "#ffffff",
             border: str = LINE, color: str = INK) -> str:
        return (
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{bg}" '
            f'stroke="{border}" stroke-width="2"/>'
            f'<text x="{x + 18}" y="{y + 37}" font-size="25" font-weight="700" '
            f'fill="{color}">{_esc(title)}</text>'
        )

    def line(x: int, y: int, text: str, *, size: int = 18, color: str = INK,
             bold: bool = False, mono: bool = False) -> str:
        ff = MONO if mono else FONT
        weight = "700" if bold else "400"
        return (f'<text x="{x}" y="{y}" font-family="{ff}" font-size="{size}" '
                f'font-weight="{weight}" fill="{color}">{_esc(text)}</text>')

    def flow_arrow(x1: int, x2: int, y: int) -> str:
        return (f'<path d="M{x1} {y} L{x2} {y}" stroke="{MUTED}" stroke-width="3" '
                'marker-end="url(#ah)"/>')

    P = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="{FONT}">',
        '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="{MUTED}"/>'
        '</marker></defs>',
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
    ]

    # ── 요청 실행 경로: 발표 화면에서 한눈에 읽히도록 다섯 블록만 남긴다. ──
    y, h = 22, 238
    boxes = [(40, 210), (275, 270), (570, 335), (930, 360), (1315, 325)]
    P.append(card(boxes[0][0], y, boxes[0][1], h, "① 서비스"))
    P.append(icon("fastapi", 58, 77, 28))
    P.append(line(96, 99, "FastAPI · Uvicorn", bold=True))
    P.append(icon("pydantic", 58, 120, 28))
    P.append(line(96, 142, "Pydantic v2"))
    P.append(line(58, 184, "X-API-Key → principal", size=17))
    P.append(line(58, 215, "HITL 승인 큐 · 401/403", size=17, color=MUTED))

    P.append(card(boxes[1][0], y, boxes[1][1], h, "② 에이전트 런타임",
                  bg=GREEN_BG, border=GREEN, color=GREEN))
    P.append(icon("python", 293, 77, 29))
    P.append(line(333, 100, "Python 3.11 · asyncio", bold=True))
    P.append(line(293, 143, "Custom ReAct loop · 4 seams", size=18))
    P.append(line(293, 177, "AFC OFF · tool_call ≤ 8", size=18))
    P.append(line(293, 211, "before_model → model →", size=16, color=MUTED, mono=True))
    P.append(line(293, 236, "before_tool → tool", size=16, color=GREEN, bold=True, mono=True))

    P.append(card(boxes[2][0], y, boxes[2][1], h, "③ 생성 모델(P-LLM)",
                  bg=ORANGE_BG, border="#efb26b",
                  color=ORANGE))
    P.append(icon("gemini", 588, 69, 34))
    P.append(line(634, 93, "Gemini 3.5 Flash", size=21, bold=True))
    P.append(line(634, 117, "기본 설정 · function-call 스모크", size=15, color=MUTED))
    P.append(line(588, 157, "OpenAI  gpt-4o-mini", size=19, bold=True, color="#0b7a6e"))
    P.append(line(588, 181, "헤드라인 5-arm 라이브 애블레이션", size=15, color=MUTED))
    P.append(line(588, 219, "FakeProvider scripted", size=19, bold=True))
    P.append(line(588, 243, "발표영상용 결정적 재생 · API 키 불요", size=15, color=MUTED))

    P.append(card(boxes[3][0], y, boxes[3][1], h, "④ before_tool 게이트",
                  bg=GREEN_BG,
                  border=GREEN, color=GREEN))
    P.append(line(948, 88, "학습 모델 없음 — 결정적 정책", size=18, bold=True))
    P.append(line(948, 124, "policy.yaml · 도구 identity", size=18))
    P.append(line(948, 158, "인자 형식/범위 · allowlist", size=18))
    P.append(line(948, 192, "egress DLP · HITL · fail-closed", size=18))
    P.append(line(948, 226, "한국어/base64/이모지에도 판정 불변", size=16, color=GREEN))

    P.append(card(boxes[4][0], y, boxes[4][1], h, "⑤ 업무 도구"))
    P.append(line(1333, 83, "readonly", size=16, color=MUTED, bold=True))
    P.append(line(1415, 83, "search_kb", size=18, bold=True))
    P.append(line(1333, 117, "internal", size=16, color=MUTED, bold=True))
    P.append(line(1415, 117, "lookup_customer", size=18, bold=True))
    P.append(line(1415, 148, "escalate_to_human", size=17))
    P.append(line(1333, 184, "external", size=16, color=RED, bold=True))
    P.append(line(1415, 184, "send_email", size=18, color=RED, bold=True))
    P.append(line(1333, 218, "HITL", size=16, color=RED, bold=True))
    P.append(line(1415, 218, "issue_refund", size=18, color=RED, bold=True))

    for (x, w), (nx, _nw) in zip(boxes, boxes[1:]):
        P.append(flow_arrow(x + w + 5, nx - 8, 142))

    # ── 보조 축: 탐지와 DLP는 역할과 실제 런타임 편입 여부를 분리해 표기한다. ──
    y2, h2 = 286, 220
    P.append(card(40, y2, 800, h2, "탐지 레일 — 운영 경로는 LOG-ONLY",
                  bg="#fffaf0", border="#e3bd64", color=ORANGE))
    P.append(chips_row(58, y2 + 55, [
        dict(label="Kanana Safeguard 2.1B · 추론전용", ico="kakao",
             ico_color="#3C1E1E", bg="#fff8dc", border="#e6c84a", size=17),
        dict(label="Ko-AgentGuard · TF-IDF+LR 학습", ico="sklearn", size=17),
    ], gap=16))
    P.append(chips_row(58, y2 + 101, [
        dict(label="e5-small(동결)+LR head", ico="sklearn", size=16),
        dict(label="ShieldGemma-2B · 추론전용", ico="huggingface", size=16),
    ], gap=16))
    P.append(chips_row(58, y2 + 147, [
        dict(label="KLUE-RoBERTa R6/R7 · 오프라인 파인튜닝", ico="huggingface", size=16),
        dict(label="RunPod RTX 4090", ico="nvidia", size=16),
    ], gap=16))
    P.append(line(58, y2 + 205, "분류 점수는 관측용 — Tier A 차단을 허용/우회할 권한이 없다",
                  size=17, color=ORANGE, bold=True))

    P.append(card(865, y2, 775, h2, "DLP — 요청 스코프 가역 토큰화",
                  bg=BLUE_BG, border="#8ebbe6", color=BLUE))
    P.append(line(883, y2 + 76, "기본 런타임", size=17, color=MUTED, bold=True))
    P.append(line(1020, y2 + 76, "RegexVault + 한국어 정규식 인식기", size=19, bold=True))
    P.append(line(883, y2 + 112, "탐지 5종", size=17, color=MUTED, bold=True))
    P.append(line(1020, y2 + 112, "RRN · 전화 · 카드 · 계좌 · 이메일", size=18))
    P.append(line(883, y2 + 148, "대안/실험", size=17, color=MUTED, bold=True))
    P.append(line(1020, y2 + 148, "Presidio 2.2.364(실측) · 한국어 결함 2건 패치", size=18))
    P.append(line(883, y2 + 184, "경계 규칙", size=17, color=MUTED, bold=True))
    P.append(line(1020, y2 + 184, "비식별 실패=차단 · 요청 종료 시 매핑 폐기", size=18,
                  color=BLUE, bold=True))
    P.append(line(883, y2 + 211, "※ R6 PII NER은 오프라인 성능 실험이며 현재 런타임 DLP에는 미편입",
                  size=15, color=MUTED))

    # ── 관측·평가 스택 ──
    y3 = 528
    P.append(card(40, y3, 1600, 104, "관측 · 평가 · 재현성", bg=TEAL_BG,
                  border=TEAL, color=TEAL))
    P.append(chips_row(310, y3 + 18, [
        dict(label="JSONL trace", size=16, mono=True),
        dict(label="GET /v1/metrics", size=16, mono=True),
        # ★Streamlit 은 쓰지 않는다★ — app/trace_view.py 첫 줄 "Streamlit 대체(컷)".
        # 뷰어는 의존성 0 의 정적 HTML 이다. requirements 에 선언만 남아 있어도 그림엔 못 쓴다.
        dict(label="정적 HTML 뷰어(의존성 0)", size=16),
        dict(label="pytest 134", ico="pytest", size=16),
        # ★statsmodels·scipy 도 import 하지 않는다★ — eval/stats.py 가 math.comb 로
        # McNemar exact·Wilson CI 를 자체 구현한다.
        dict(label="McNemar exact · Wilson CI (자체 구현)", size=16),
        dict(label="Docker", ico="docker", size=16),
    ], gap=12))
    P.append(line(310, y3 + 85,
                  "핵심 지표: attempted / executed · gate_blocks_total · dlp_redactions_total · leaks_total",
                  size=17, color=TEAL, bold=True))

    P.append(f'<rect x="40" y="655" width="1600" height="45" rx="10" fill="{INK}"/>')
    P.append(f'<text x="840" y="685" font-size="21" font-weight="700" fill="#ffffff" '
             'text-anchor="middle">생성(③) · 탐지(하단) · 강제(④)의 역할을 분리 — '
             '최종 실행 권한은 결정적 게이트만 가진다</text>')
    P.append("</svg>")
    return "".join(P)


def _find_chrome() -> str | None:
    for c in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium",
              "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"):
        if Path(c).exists():
            return c
    for n in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        p = shutil.which(n)
        if p:
            return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=int, default=2, help="PNG 배율(선명도)")
    args = ap.parse_args()

    svg = build()
    OUT_SVG.parent.mkdir(parents=True, exist_ok=True)
    OUT_SVG.write_text(svg, encoding="utf-8")

    chrome = _find_chrome()
    if chrome is None:
        print(f"→ {OUT_SVG.relative_to(ROOT)} (Chrome 없음 → PNG 생략)")
        return 0
    with tempfile.TemporaryDirectory() as td:
        page = Path(td) / "a.html"
        page.write_text(
            f'<html><body style="margin:0">{svg}</body></html>', encoding="utf-8")
        OUT_PNG.unlink(missing_ok=True)  # 실패 시 오래된 PNG를 성공 산출물로 오인하지 않는다.
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        "--no-sandbox", f"--force-device-scale-factor={args.scale}",
                        f"--window-size={W},{H}", f"--screenshot={OUT_PNG}",
                        page.resolve().as_uri()],
                       capture_output=True, text=True, timeout=180)
    if not OUT_PNG.exists():
        print("✗ PNG 캡처 실패", file=sys.stderr)
        return 1
    print(f"→ {OUT_SVG.relative_to(ROOT)} · {OUT_PNG.relative_to(ROOT)} "
          f"({OUT_PNG.stat().st_size // 1024}KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
