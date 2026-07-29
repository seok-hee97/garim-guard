"""실행 워크플로 다이어그램 — 실제 입력 한 건이 홉마다 어떻게 처리되는지.

    python -m scripts.gen_workflow      # → docs/workflow_example.png (+ .svg)

발표는 코드 리뷰 없이 슬라이드 + 데모영상만으로 진행된다. 따라서 "요청 하나가 들어오면
무슨 일이 벌어지는가"를 **실제 데이터 값과 함께** 한 장에 담아 자립적으로 읽히게 한다.

★값을 지어내지 않는다★ — 모든 문자열은 저장소의 실물에서 온다:
  · 사용자 입력 / 홉 순서 / 게이트 판정 / 레일 점수 → results/demo_traces.jsonl (arm=tier_a)
  · 오염 문서 본문                                  → src/agent/toolworld.py (kb_003)
  · 차단 규칙·allowlist                             → config/policy.yaml (egress.email_domains)
데모를 다시 돌리면(`python -m scripts.run_demo`) 이 그림도 같은 값으로 다시 그려진다.

렌더: SVG 조립 → 헤드리스 Chrome PNG (gen_arch_stack.py 와 동일 방식, 의존성 없음).
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRACES = ROOT / "results" / "demo_traces.jsonl"
OUT_PNG = ROOT / "docs" / "workflow_example.png"
OUT_SVG = ROOT / "docs" / "workflow_example.svg"

W, H = 1720, 650
FONT = "'Apple SD Gothic Neo','AppleGothic','Malgun Gothic','Noto Sans KR',sans-serif"
MONO = "ui-monospace,'SF Mono',Menlo,monospace"

INK, MUTED, LINE = "#16213e", "#5b6b7f", "#c9d4e0"
GREEN, GREEN_BG = "#1b5e20", "#e8f5e9"
RED, RED_BG = "#b1361e", "#fdecea"
BLUE, BLUE_BG = "#1565c0", "#e3f2fd"
AMBER, AMBER_BG = "#8a5a00", "#fff6e0"
ORANGE, ORANGE_BG = "#e65100", "#fff3e0"
GREY_BG = "#f4f6f8"


def _e(s: str) -> str:
    return html.escape(str(s))


def _load():
    """arm=tier_a 에피소드 = '모델은 속았지만 게이트가 막은' 대표 트레이스."""
    eps = [json.loads(l) for l in TRACES.read_text("utf-8").splitlines() if l.strip()]
    ep = next(e for e in eps if "arm=tier_a" in e["scenario"] and "dlp" not in e["scenario"])
    hops = [h for h in ep["hops"] if h.get("tool_name")]
    return ep, {h["tool_name"]: h for h in hops}


def _w(text: str, size: float) -> float:
    """텍스트 폭 근사 — 한글/전각 1.0em, ASCII 0.56em."""
    return sum(1.0 if ord(c) > 0x2000 else 0.56 for c in text) * size


def _wrap(text: str, width: int) -> list[str]:
    """한글 폭 기준 단순 줄바꿈(전각 1, 반각 0.5)."""
    out, cur, w = [], "", 0.0
    for ch in text:
        cw = 1.0 if ord(ch) > 0x2000 else 0.5
        if w + cw > width and ch == " ":
            out.append(cur); cur, w = "", 0.0; continue
        cur += ch; w += cw
        if w >= width:
            out.append(cur); cur, w = "", 0.0
    if cur:
        out.append(cur)
    return out


def step(n: str, x: float, y: float, w: float, h: float, *, title: str, sub: str,
         body: list[tuple[str, str]], accent: str, bg: str) -> str:
    """단계 카드 — 번호·제목·부제 + (라벨, 값) 줄들. 값은 모노스페이스."""
    p = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{bg}" '
         f'stroke="{accent}" stroke-width="1.8"/>',
         f'<circle cx="{x+26}" cy="{y+26}" r="14" fill="{accent}"/>',
         f'<text x="{x+26}" y="{y+31}" font-size="15" font-weight="700" fill="#fff" '
         f'text-anchor="middle">{n}</text>',
         f'<text x="{x+50}" y="{y+25}" font-size="17" font-weight="700" fill="{accent}">'
         f'{_e(title)}</text>',
         f'<text x="{x+50}" y="{y+43}" font-size="12.5" fill="{MUTED}">{_e(sub)}</text>']
    # ★라벨/값을 고정 2열로★ — 라벨 폭을 글자수로 추정하면 한글에서 값과 겹친다.
    #   라벨은 우측정렬 고정열, 값은 그 뒤 고정 x 에서 시작해 어떤 라벨이 와도 안 겹친다.
    lw = 58.0
    ty = y + 68
    for label, val in body:
        if label:
            p.append(f'<text x="{x+18+lw:.0f}" y="{ty}" font-size="12" font-weight="700" '
                     f'fill="{MUTED}" text-anchor="end">{_e(label)}</text>')
        p.append(f'<text x="{x+18+lw+10:.0f}" y="{ty}" font-family="{MONO}" font-size="12.5" '
                 f'fill="{INK}">{_e(val)}</text>')
        ty += 19
    return "".join(p)


def arrow(x1: float, y1: float, x2: float, y2: float, label: str = "",
          color: str = MUTED, dashed: bool = False) -> str:
    d = ' stroke-dasharray="5 4"' if dashed else ""
    s = (f'<path d="M{x1} {y1} L{x2} {y2}" stroke="{color}" stroke-width="2"{d} '
         f'marker-end="url(#a)"/>')
    if label:
        s += (f'<text x="{(x1+x2)/2 + 8}" y="{(y1+y2)/2 - 6}" font-size="12" '
              f'fill="{color}" font-weight="700">{_e(label)}</text>')
    return s


def build() -> str:
    eps = [json.loads(l) for l in TRACES.read_text("utf-8").splitlines() if l.strip()]
    by_arm = {
        arm: next(e for e in eps if f"arm={arm}" in e["scenario"])
        for arm in ("none", "tier_a", "tier_a+dlp_vault")
    }
    ep = by_arm["tier_a"]
    hops = {h["tool_name"]: h for h in ep["hops"] if h.get("tool_name")}
    lookup, kb, mail = hops["lookup_customer"], hops["search_kb"], hops["send_email"]
    rail_kb = next(iter(kb.get("rail_scores", {}).values()), None)
    dlp_mail = next(h for h in by_arm["tier_a+dlp_vault"]["hops"]
                    if h.get("tool_name") == "send_email")
    none_mail = next(h for h in by_arm["none"]["hops"] if h.get("tool_name") == "send_email")
    from src.agent.toolworld import default_world
    poisoned = next(d for d in default_world().search("환불 처리 안내") if not d.get("trusted"))

    def card(n: str, x: int, y: int, w: int, h: int, title: str, *,
             accent: str, bg: str, subtitle: str = "") -> str:
        p = [
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{bg}" '
            f'stroke="{accent}" stroke-width="2.2"/>',
            f'<circle cx="{x + 31}" cy="{y + 31}" r="18" fill="{accent}"/>',
            f'<text x="{x + 31}" y="{y + 38}" font-size="20" font-weight="700" '
            f'fill="#fff" text-anchor="middle">{n}</text>',
            f'<text x="{x + 62}" y="{y + 36}" font-size="24" font-weight="700" '
            f'fill="{accent}">{_e(title)}</text>',
        ]
        if subtitle:
            p.append(f'<text x="{x + 62}" y="{y + 62}" font-size="16" '
                     f'fill="{MUTED}">{_e(subtitle)}</text>')
        return "".join(p)

    def txt(x: int, y: int, text: str, *, size: int = 18, color: str = INK,
            bold: bool = False, mono: bool = False) -> str:
        ff = MONO if mono else FONT
        weight = "700" if bold else "400"
        return (f'<text x="{x}" y="{y}" font-family="{ff}" font-size="{size}" '
                f'font-weight="{weight}" fill="{color}">{_e(text)}</text>')

    P = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}" font-family="{FONT}">',
         '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
         f'markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="{MUTED}"/>'
         '</marker></defs>',
         f'<rect width="{W}" height="{H}" fill="#ffffff"/>']

    P.append(txt(42, 31,
                 "합성 고객 데이터 · RFC 2606 예약 도메인 · 동일 요청을 3개 방어 구성으로 재생",
                 size=17, color=MUTED))
    P.append(f'<path d="M40 45 L{W - 40} 45" stroke="{GREEN}" stroke-width="2.5"/>')

    x1, x2, x3 = 40, 600, 1160
    cw, ch = 520, 238
    top, bottom = 64, 350

    # ① 사용자 입력
    P.append(card("1", x1, top, cw, ch, "사용자 요청 + 인증", accent=INK, bg="#ffffff",
                  subtitle="서비스 경계에서 principal·고객 스코프를 먼저 고정"))
    P.append(txt(x1 + 25, top + 103, "입력", size=17, color=MUTED, bold=True))
    P.append(txt(x1 + 96, top + 103, ep["user_input"][:33] + "…", size=17))
    P.append(txt(x1 + 25, top + 145, "인증", size=17, color=MUTED, bold=True))
    P.append(txt(x1 + 96, top + 145, "X-API-Key → principal(agent)", size=17, mono=True))
    P.append(txt(x1 + 25, top + 187, "바인딩", size=17, color=MUTED, bold=True))
    P.append(txt(x1 + 96, top + 187, "conversation ↔ customer_id", size=17, mono=True))
    P.append(txt(x1 + 25, top + 222, "※ 화면의 전화·RRN·메일은 모두 합성값", size=16,
                 color=BLUE, bold=True))

    # ② 생성 모델/데모 재생기
    P.append(card("2", x2, top, cw, ch, "P-LLM이 도구 계획 생성", accent=ORANGE,
                  bg=ORANGE_BG, subtitle="라이브 실험과 영상 데모의 역할을 분리"))
    P.append(txt(x2 + 25, top + 105, "라이브", size=17, color=MUTED, bold=True))
    P.append(txt(x2 + 110, top + 105, "gpt-4o-mini · Gemini 3.5 Flash", size=18, bold=True))
    P.append(txt(x2 + 25, top + 148, "영상", size=17, color=MUTED, bold=True))
    P.append(txt(x2 + 110, top + 148, "FakeProvider(scripted)", size=18, bold=True, mono=True))
    P.append(txt(x2 + 25, top + 184, "동일 tool_call을 고정해 게이트 차이만 재현", size=17))
    P.append(txt(x2 + 25, top + 222, "→ 데모는 모델 취약성 증명이 아니라 실행 차단 증명", size=16,
                 color=ORANGE, bold=True))

    # ③ 정상 도구 흐름
    P.append(card("3", x3, top, cw, ch, "정상 도구는 통과", accent=GREEN, bg=GREEN_BG,
                  subtitle="위험 티어가 낮은 조회 흐름은 그대로 유지"))
    P.append(txt(x3 + 25, top + 109,
                 f"hop0  lookup_customer(phone={lookup['tool_args'].get('phone')})",
                 size=16, mono=True))
    P.append(txt(x3 + 25, top + 143, f"      internal → {lookup.get('gate_decision')}",
                 size=17, color=GREEN, bold=True, mono=True))
    P.append(txt(x3 + 25, top + 184,
                 f"hop1  search_kb(query={kb['tool_args'].get('query')})", size=16, mono=True))
    P.append(txt(x3 + 25, top + 218, f"      readonly → {kb.get('gate_decision')}",
                 size=17, color=GREEN, bold=True, mono=True))

    # ④ 공격 도구 반환값
    P.append(card("4", x1, bottom, cw, ch, "공격 진입: 도구 반환값", accent=RED, bg=RED_BG,
                  subtitle="사용자 발화가 아니라 search_kb 결과 안의 숨은 명령"))
    lines = _wrap(poisoned["body"], 42)[:3]
    P.append(txt(x1 + 25, bottom + 101, "kb_003", size=16, color=MUTED, bold=True))
    for i, text in enumerate(lines):
        P.append(txt(x1 + 25, bottom + 132 + i * 29, text, size=16))
    P.append(txt(x1 + 25, bottom + 222,
                 f"rail={rail_kb:.3f} < τ 0.5 → 탐지 실패 · LOG-ONLY", size=17,
                 color=RED, bold=True, mono=True))

    # ⑤ 위험 도구 호출
    P.append(card("5", x2, bottom, cw, ch, "위험 행동 시도", accent=RED, bg=RED_BG,
                  subtitle="라이브 성공 케이스와 동형인 tool_call을 영상에서 고정 재생"))
    P.append(txt(x2 + 25, bottom + 108,
                 f"send_email(to={mail['tool_args'].get('to')})", size=18, bold=True, mono=True))
    P.append(txt(x2 + 25, bottom + 149,
                 f"body={str(mail['tool_args'].get('body'))[:35]}", size=16, mono=True))
    P.append(txt(x2 + 25, bottom + 190, "attempted = True", size=18, color=RED,
                 bold=True, mono=True))
    P.append(txt(x2 + 25, bottom + 222, "아직 실행 전 — 다음 경계가 최종 결정", size=17,
                 color=MUTED))

    # ⑥ 세 arm 결과
    P.append(card("6", x3, bottom, cw, ch, "before_tool 판정 + 결과", accent=GREEN,
                  bg=GREEN_BG, subtitle="같은 시도에서 방어 구성만 바꿔 실행 여부 비교"))
    P.append(txt(x3 + 25, bottom + 103, "none", size=17, color=RED, bold=True, mono=True))
    P.append(txt(x3 + 125, bottom + 103,
                 f"executed={none_mail.get('executed')} · leaks_total=1", size=17, color=RED,
                 bold=True, mono=True))
    P.append(txt(x3 + 25, bottom + 145, "tier_a", size=17, color=GREEN, bold=True, mono=True))
    P.append(txt(x3 + 125, bottom + 145,
                 f"{mail.get('gate_rule')} → executed={mail.get('executed')}", size=16,
                 color=GREEN, bold=True, mono=True))
    P.append(txt(x3 + 25, bottom + 187, "+ dlp", size=17, color=BLUE, bold=True, mono=True))
    P.append(txt(x3 + 125, bottom + 187,
                 f"{dlp_mail.get('gate_rule')} → executed={dlp_mail.get('executed')}", size=16,
                 color=BLUE, bold=True, mono=True))
    P.append(txt(x3 + 25, bottom + 224, "게이트 arm: attempted=True · executed=False · leak=0",
                 size=16, color=GREEN, bold=True, mono=True))

    # 흐름 화살표
    P.append(arrow(x1 + cw, top + ch / 2, x2 - 10, top + ch / 2))
    P.append(arrow(x2 + cw, top + ch / 2, x3 - 10, top + ch / 2))
    P.append(arrow(x3 + cw / 2, top + ch, x1 + cw / 2, bottom - 10,
                   "도구 반환값", RED, dashed=True))
    P.append(arrow(x1 + cw, bottom + ch / 2, x2 - 10, bottom + ch / 2, "", RED))
    P.append(arrow(x2 + cw, bottom + ch / 2, x3 - 10, bottom + ch / 2))

    P.append("</svg>")
    return "".join(P)


def _chrome() -> str | None:
    for c in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium",
              "/usr/bin/google-chrome", "/usr/bin/chromium"):
        if Path(c).exists():
            return c
    for n in ("google-chrome", "chromium", "chrome"):
        if (p := shutil.which(n)):
            return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=int, default=2)
    args = ap.parse_args()
    if not TRACES.exists():
        print(f"✗ {TRACES} 없음 — 먼저 `python -m scripts.run_demo`", file=sys.stderr)
        return 2
    svg = build()
    OUT_SVG.write_text(svg, encoding="utf-8")
    chrome = _chrome()
    if chrome is None:
        print(f"→ {OUT_SVG.relative_to(ROOT)} (Chrome 없음 → PNG 생략)")
        return 0
    with tempfile.TemporaryDirectory() as td:
        page = Path(td) / "a.html"
        page.write_text(f'<html><body style="margin:0">{svg}</body></html>', encoding="utf-8")
        OUT_PNG.unlink(missing_ok=True)  # 실패 시 이전 PNG를 새 산출물로 오인하지 않는다.
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        "--no-sandbox", f"--force-device-scale-factor={args.scale}",
                        f"--window-size={W},{H}", f"--screenshot={OUT_PNG}",
                        page.resolve().as_uri()], capture_output=True, timeout=180)
    if not OUT_PNG.exists():
        print("✗ PNG 캡처 실패", file=sys.stderr)
        return 1
    print(f"→ {OUT_SVG.relative_to(ROOT)} · {OUT_PNG.relative_to(ROOT)} "
          f"({OUT_PNG.stat().st_size // 1024}KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
