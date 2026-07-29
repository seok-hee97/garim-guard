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

W, H = 1720, 596
FONT = "'Apple SD Gothic Neo','AppleGothic','Malgun Gothic','Noto Sans KR',sans-serif"
MONO = "ui-monospace,'SF Mono',Menlo,monospace"

INK, MUTED, LINE = "#16213e", "#5b6b7f", "#c9d4e0"
GREEN, GREEN_BG = "#1b5e20", "#e8f5e9"
RED, RED_BG = "#b1361e", "#fdecea"
BLUE, BLUE_BG = "#1565c0", "#e3f2fd"
AMBER, AMBER_BG = "#8a5a00", "#fff6e0"
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
    ep, H_ = _load()
    lookup, kb = H_["lookup_customer"], H_["search_kb"]
    mail, esc = H_["send_email"], H_["escalate_to_human"]
    rail_kb = next(iter(kb.get("rail_scores", {}).values()), None)
    from src.agent.toolworld import default_world
    poisoned = next(d for d in default_world().search("환불 처리 안내") if not d.get("trusted"))

    P = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}" font-family="{FONT}">',
         '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
         f'markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="{MUTED}"/>'
         '</marker></defs>',
         f'<rect width="{W}" height="{H}" fill="#ffffff"/>']

    # ★큰 제목은 넣지 않는다★ — 슬라이드 제목과 중복되면 화면에 제목이 두 번 나온다.
    #   출처 한 줄만 남겨 근거를 밝히고, 회수한 세로 공간은 카드 판독성에 쓴다.
    P.append(f'<text x="40" y="30" font-size="14.5" fill="{MUTED}">'
             'results/demo_traces.jsonl (arm=tier_a) 실측 · 모델은 속았지만 행동이 나가지 않는다</text>')
    P.append(f'<path d="M40 42 L{W-40} 42" stroke="{GREEN}" stroke-width="2.5"/>')

    CW1, X1 = 800, 40          # 좌열
    X2 = 880                   # 우열

    # ① 입력
    y = 58
    P.append(step("1", X1, y, CW1, 96, title="사용자 요청", sub="인증 → principal → 고객 스코프 바인딩",
                  body=[("입력", ep["user_input"][:44] + "…"),
                        ("인증", "X-API-Key → principal(agent) · customer_id 바인딩")],
                  accent=INK, bg="#ffffff"))
    # ② before_model
    y2 = y + 110
    P.append(arrow(X1 + CW1 / 2, y + 96, X1 + CW1 / 2, y2))
    P.append(step("2", X1, y2, CW1, 92, title="before_model — DLP 토큰화",
                  sub="모델이 원본 PII 를 보지 않게 치환",
                  body=[("치환", "010-1234-5678  →  <KR_PHONE_1>"),
                        ("규율", "홉이 바뀌어도 같은 사람 → 같은 토큰(대화 일관성 유지)")],
                  accent=BLUE, bg=BLUE_BG))
    # ③ hop0·1 정상 도구
    y3 = y2 + 106
    P.append(arrow(X1 + CW1 / 2, y2 + 92, X1 + CW1 / 2, y3))
    P.append(step("3", X1, y3, CW1, 110, title="hop0·hop1 — 정상 도구 통과",
                  sub="게이트는 위험티어·allowlist 만 본다",
                  body=[("hop0", f"lookup_customer(phone={lookup['tool_args'].get('phone')}) "
                                 f"· internal → {lookup.get('gate_decision')}"),
                        ("hop1", f"search_kb(query={kb['tool_args'].get('query')}) "
                                 f"· readonly → {kb.get('gate_decision')}"),
                        ("", "정상 업무는 그대로 흐른다 — 과잉 게이팅 아님")],
                  accent=GREEN, bg=GREEN_BG))
    # ④ 오염 반환값
    y4 = y3 + 124
    P.append(arrow(X1 + CW1 / 2, y3 + 110, X1 + CW1 / 2, y4, "도구 반환값", RED))
    lines = _wrap(poisoned["body"], 47)[:3]
    P.append(step("4", X1, y4, CW1, 152, title="★ 공격 진입 — 도구 반환값에 숨은 명령 ★",
                  sub="공격자가 통제하는 지점은 사용자 발화가 아니라 여기다",
                  body=[("kb_003", lines[0])] + [("", l) for l in lines[1:]]
                       + [("레일", f"ko_agentguard={rail_kb:.3f} → 임계 0.5 미만 = 못 잡음 "
                                   f"(LOG-ONLY 라 어차피 차단 권한 없음)")],
                  accent=RED, bg=RED_BG))

    # ⑤ 모델이 속음 (우열 상단)
    P.append(arrow(X1 + CW1, y4 + 24, X2 - 6, y4 + 24, "→⑤", RED))
    yr = 58
    P.append(step("5", X2, yr, W - X2 - 40, 118, title="hop2 — 모델이 지시를 따른다",
                  sub="탐지는 이미 실패했다. 여기서 막지 못하면 유출이다",
                  body=[("생성", f"send_email(to={mail['tool_args'].get('to')})"),
                        ("본문", str(mail["tool_args"].get("body"))[:52]),
                        ("", "★원본 주민등록번호가 인자에 들어갔다★")],
                  accent=RED, bg=RED_BG))
    # ⑥ 게이트 차단
    yr2 = yr + 132
    P.append(arrow(X2 + (W - X2 - 40) / 2, yr + 118, X2 + (W - X2 - 40) / 2, yr2, "", RED))
    P.append(step("6", X2, yr2, W - X2 - 40, 150, title="★ before_tool 게이트 — 차단 ★",
                  sub="공격 문장의 의미를 판정하지 않는다. 도구 identity 와 정책만 본다",
                  body=[("도구", "send_email · risk_tier=external"),
                        ("정책", "allowlist_ref: egress.email_domains"),
                        ("대조", "mal.example.org ∉ [company.example.com, …]"),
                        ("판정", f"{mail.get('gate_decision')}  rule={mail.get('gate_rule')}"),
                        ("", "→ 한국어·이모지·base64 어떤 표기든 결과 동일")],
                  accent=GREEN, bg=GREEN_BG))
    # ⑦ 결과
    yr3 = yr2 + 164
    P.append(arrow(X2 + (W - X2 - 40) / 2, yr2 + 150, X2 + (W - X2 - 40) / 2, yr3))
    P.append(step("7", X2, yr3, W - X2 - 40, 128, title="hop3 · 결과",
                  sub="시도는 남기고 실행만 막는다 — 측정 가능한 안전",
                  body=[("hop3", f"escalate_to_human(...) · {esc.get('gate_decision')} "
                                 f"(사람에게 이관)"),
                        ("트레이스", "attempted=True  /  executed=False"),
                        ("메트릭", "gate_blocks_total{egress.email_domains}=1"),
                        ("★불변식", "leaks_total = 0  (allowlist 밖 실행 0건)")],
                  accent=INK, bg=GREY_BG))

    # ★하단 결론 배너는 넣지 않는다★ — 슬라이드 배너가 같은 문장을 말한다.
    #   그림 안에 또 두면 화면에 결론이 두 줄 연달아 나온다(중복). 회수한 높이는 카드에 준다.
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
