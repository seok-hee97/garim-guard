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

W, H = 1680, 900                      # 가로:세로 ≈1.8 — 슬라이드 `![h:540]` 에서 폭을 최대로
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
             ("★ before_tool 게이트", True), ("도구 실행", False), ("after_tool", False)]
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
    P = []
    P.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}" font-family="{FONT}">')
    P.append('<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
             f'markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="{MUTED}"/>'
             '</marker></defs>')
    P.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')

    # ── 헤더 ──
    # ★큰 제목 제거★ — 슬라이드 제목과 중복. 논지 한 줄만 남기고 공간을 밴드에 넘긴다.
    P.append(f'<text x="44" y="30" font-size="16" fill="{MUTED}">'
             '강제(enforcement)는 결정적 게이트가, 탐지(detection)는 학습형 레일이 담당한다 — '
             '두 축이 코드 구조로 분리돼 있다</text>')
    P.append(f'<path d="M44 44 L{W - 44} 44" stroke="{GREEN}" stroke-width="2.5"/>')

    LX, RW = 44, W - 88          # 밴드 x, 폭
    CX = LX + 250                # 밴드 내부 콘텐츠 시작 x

    # ── ① 서비스 계층 ──
    y = 56
    P.append(band(LX, y, RW, 112, bg="#ffffff", border=LINE))
    P.append(band_title(LX + 18, y + 32, "①", "서비스 계층", "HTTP · 인증 · 승인 큐", INK))
    P.append(chips_row(CX, y + 10, [
        dict(label="FastAPI", ico="fastapi"), dict(label="Pydantic", ico="pydantic"),
        dict(label="uvicorn", fg="#2f8f83", bg="#e6f4f2", border="#9ccfc8"),
        dict(label="X-API-Key → principal (agent·approver·auditor)", size=14),
    ]))
    P.append(chips_row(CX, y + 48, [
        dict(label="HITL 승인 큐(요청자≠승인자·재승인 409)", size=14),
        dict(label="Idempotency-Key 제공 시 재시도 중복 제거", size=14),
        # 멀티테넌트 안전성의 핵심 — 조회는 고객 A, 발송은 고객 B 를 구조적으로 막는다.
        dict(label="대화-고객 바인딩 → confused-deputy 차단", size=14,
             fg=GREEN, bg=GREEN_BG, border="#a5d6a7"),
    ]))
    P.append(f'<text x="{CX}" y="{y + 100}" font-size="13" fill="{MUTED}">'
             'fail-closed: 키 없거나 역할 불일치 → 401/403 · prod 부팅 시 데모키 거부</text>')

    # ── ② 에이전트 런타임 (핵심) ──
    y = 180
    P.append(band(LX, y, RW, 212, bg=GREEN_BG, border=GREEN))
    P.append(band_title(LX + 18, y + 32, "②", "에이전트 런타임 (4-seam)",
                        "★강제가 일어나는 유일한 지점★", GREEN))
    P.append(chips_row(CX, y + 10, [
        dict(label="Python 3.11 · asyncio", ico="python"),
        dict(label="AFC off(도구 자동실행 금지)", size=14, fg=GREEN, bg="#ffffff", border=GREEN),
        dict(label="병렬 tool_call 보존(≤8)", size=14),
        dict(label="부작용 도구 abandon 금지", size=14),
    ]))
    P.append(seam_flow(LX + 18, y + 58))
    P.append(chips_row(LX + 18, y + 130, [
        dict(label="Tier A 결정적 게이트 — 위험티어 4종 · allowlist · 인자스키마 · egress DLP",
             fg="#ffffff", bg=GREEN, border=GREEN, size=14),
        dict(label="Tier B taint(value-provenance)", fg=GREEN, bg="#ffffff", border=GREEN, size=14),
        dict(label="Tier C 격리 — 설계전용·미구현", fg=GREY, bg=GREY_BG, border=GREY,
             size=14, dashed=True),
    ]))
    # 도구별 티어 대비가 게이트 설계의 설득력 — '어떤 도구를 왜 다르게 다루나'를 한 줄로 보인다.
    P.append(f'<text x="{LX + 18}" y="{y + 176}" font-size="13" fill="{MUTED}">'
             '도구 5종 · 위험티어: '
             '<tspan font-weight="700" fill="#2f6b33">search_kb</tspan>(readonly) · '
             '<tspan font-weight="700" fill="#2f6b33">lookup_customer·escalate_to_human</tspan>(internal) · '
             '<tspan font-weight="700" fill="#b1361e">send_email</tspan>(external→allowlist) · '
             '<tspan font-weight="700" fill="#b1361e">issue_refund</tspan>(irreversible→HITL 승인)</text>')
    P.append(f'<text x="{LX + 18}" y="{y + 200}" font-size="13" fill="{GREEN}">'
             '★게이트는 공격 텍스트의 의미를 분류하지 않는다 — 도구 identity·구조화 인자·allowlist 로만 판정(난독화 불변)★</text>')

    # ── ③ 모델 & 레일 ──
    y = 404
    P.append(band(LX, y, RW, 178, bg="#ffffff", border=LINE))
    P.append(band_title(LX + 18, y + 34, "③", "모델 · 안전 레일",
                        "운영 설정 log-only 봉인 · classifier_gate 는 대조군 전용", ORANGE))
    P.append(f'<text x="{CX}" y="{y + 26}" font-size="13" font-weight="700" fill="{MUTED}">'
             '생성(P-LLM)</text>')
    P.append(chips_row(CX + 78, y + 12, [
        dict(label="Gemini 3.5 Flash", ico="gemini"),
        dict(label="OpenAI gpt-4o-mini", fg="#0b7a6e", bg="#e8f6f4", border="#8fcac2"),
        dict(label="카세트 record/replay (무료티어 5 RPM 우회)", size=14),
    ]))
    P.append(f'<text x="{CX}" y="{y + 70}" font-size="13" font-weight="700" fill="{MUTED}">'
             '탐지 레일</text>')
    P.append(chips_row(CX + 78, y + 56, [
        # 사다리의 바닥 — 의존성 0 이라 봉인 테스트·폴백을 담당한다(5종 중 하나로 반드시 표기).
        dict(label="Heuristic(의존성 0)", size=14),
        dict(label="Kanana Safeguard 2.1B", ico="kakao", ico_color="#3C1E1E",
             bg="#fff8dc", border="#e6c84a"),
        dict(label="ShieldGemma-2B", ico="huggingface"),
        dict(label="e5 + LR", ico="sklearn"),
        dict(label="KLUE-RoBERTa R6/R7", ico="huggingface"),
        dict(label="RTX 4090", ico="nvidia"),
    ]))
    P.append(f'<text x="{CX}" y="{y + 114}" font-size="13" font-weight="700" fill="{MUTED}">'
             'DLP</text>')
    P.append(chips_row(CX + 78, y + 100, [
        dict(label="Presidio", fg="#0a58a6", bg=BLUE_BG, border="#9dc2e8"),
        # ★여권을 넣지 말 것★ — recognizers_ko.py 가 구현한 5종은 RRN·전화·카드·계좌·이메일이다.
        # Presidio 의 KrPassportRecognizer 는 supported_language="kr"(오타)라 ko 파이프라인에
        # 로드되지 않으며, 그게 바로 이 프로젝트가 보고한 '결함 1'이다. 여권을 동작하는 인식기로
        # 적으면 자체 기여(결함 보고)와 정면으로 모순된다.
        dict(label="KR 인식기 5종(RRN·전화·카드·계좌·이메일)", size=14, fg=BLUE,
             bg=BLUE_BG, border="#9dc2e8"),
        dict(label="요청스코프 가역 볼트", size=14, fg=BLUE, bg=BLUE_BG, border="#9dc2e8"),
        # 비식별 실패 시 원본을 모델에 넘기지 않고 차단 — 볼트 0% 결과보다 강한 보장.
        dict(label="fail: closed(비식별 실패=차단)", size=14, fg="#b1361e",
             bg="#fdecea", border="#e6a49c"),
    ]))
    # ★모델 사용 범위를 못박는다★ — "Google 모델 우선" 지침과 실제 헤드라인 측정 모델이 다르므로,
    #   어느 모델이 어디에 쓰였는지 한 줄로 분리해 심사 질의를 예방한다.
    P.append(f'<text x="{CX}" y="{y + 158}" font-size="13" fill="{MUTED}">'
             '<tspan font-weight="700">사용 범위</tspan> — '
             'Gemini 3.5 Flash: function-calling 스모크 · '
             '<tspan font-weight="700">gpt-4o-mini: 헤드라인 애블레이션</tspan> · '
             'ShieldGemma: EN-KO 오프라인 실험 · FakeProvider(scripted): 키 불요 결정적 데모</text>')

    # ── ④ 관측 ──
    y = 594
    P.append(band(LX, y, RW, 100, bg=TEAL_BG, border=TEAL))
    P.append(band_title(LX + 18, y + 34, "④", "관측 · 모니터링",
                        "PII 없음 — 정수 + enum 라벨만", TEAL))
    P.append(chips_row(CX, y + 14, [
        dict(label="GET /v1/metrics", mono=True, size=13.5, fg=TEAL, bg="#ffffff", border=TEAL),
        dict(label="scripts.monitor 라이브 대시보드", size=14),
        dict(label="구조적 로그 + X-Request-ID", size=14),
        dict(label="eval 진행 heartbeat", size=14),
    ]))
    P.append(chips_row(CX, y + 54, [
        dict(label="gate_blocks_total", mono=True, size=13),
        dict(label="dlp_redactions_total", mono=True, size=13),
        dict(label="★ leaks_total = 0 불변식", size=14, fg="#ffffff", bg="#b1361e",
             border="#b1361e"),
        dict(label="지연 p50/p95", size=14),
    ]))

    # ── ⑤ 평가·재현 ──
    y = 706
    P.append(band(LX, y, RW, 100, bg="#ffffff", border=LINE))
    P.append(band_title(LX + 18, y + 34, "⑤", "평가 · 재현성",
                        "수치는 전부 results/ 에서 유도", INK))
    P.append(chips_row(CX, y + 14, [
        dict(label="KoTIP-Bench (자체 구축)", size=14, fg=GREEN, bg=GREEN_BG, border="#a5d6a7"),
        dict(label="McNemar exact · Wilson CI", ico="scipy", size=14),
        dict(label="125 tests", ico="pytest", size=14),
        dict(label="Docker", ico="docker", size=14),
    ]))
    P.append(chips_row(CX, y + 54, [
        dict(label="난독화 9종 — 학습 4 / holdout 5(자모분해·초성·이모지 스머글링·태그·간격)",
             size=14, fg="#8a5a00", bg="#fff6e0", border="#e0c48a"),
        dict(label="오염 감사 GREEN", size=14),
        dict(label="run_id 계보 해시 → 신규 실행부터 stale 자동 격리", size=14),
    ]))

    # ── 하단 논지 ──
    P.append(f'<rect x="{LX}" y="{H - 78}" width="{RW}" height="46" rx="10" fill="{INK}"/>')
    P.append(f'<text x="{W / 2}" y="{H - 48}" font-size="17" font-weight="700" fill="#ffffff" '
             'text-anchor="middle">강제는 ②의 결정적 게이트가 — 분류기(③)는 '
             '&quot;어디서 뚫리는지&quot;를 재는 계측기</text>')

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
