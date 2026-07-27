"""프로젝트 아키텍처 다이어그램 생성기 (제출용).

graphviz로 '안전 하드닝 한국어 AI 에이전트'의 구조를 렌더링한다.
출력: docs/architecture.png, docs/architecture.svg

실행:  python docs/architecture_diagram.py
필요:  graphviz(dot) + python-graphviz, 한국어 폰트(AppleGothic)
"""

from __future__ import annotations

import graphviz

# macOS 한국어 렌더링 폰트
FONT = "AppleGothic"

# 색상 팔레트 (의미별)
C_TRUST = {"fillcolor": "#e8f5e9", "color": "#2e7d32"}   # 신뢰/사용자
C_GATE = {"fillcolor": "#c8e6c9", "color": "#1b5e20"}    # ★ 진짜 방어(결정적)
C_DLP = {"fillcolor": "#e3f2fd", "color": "#1565c0"}     # DLP/PII
C_MODEL = {"fillcolor": "#fff3e0", "color": "#e65100"}   # Google/LLM 모델
C_TELE = {"fillcolor": "#f5f5f5", "color": "#757575"}    # 텔레메트리(LOG-ONLY)
C_THREAT = {"fillcolor": "#ffebee", "color": "#c62828"}  # 비신뢰/위협
C_OUT = {"fillcolor": "#ede7f6", "color": "#4527a0"}     # 출력


def node(g, name, label, palette, shape="box", **extra):
    g.node(
        name,
        label=label,
        shape=shape,
        style="filled,rounded",
        fontname=FONT,
        fontsize="11",
        margin="0.14,0.09",
        penwidth="1.6",
        **palette,
        **extra,
    )


def build() -> graphviz.Digraph:
    g = graphviz.Digraph("architecture")
    g.attr(
        rankdir="TB",
        bgcolor="white",
        fontname=FONT,
        labelloc="t",
        fontsize="18",
        label="안전 하드닝 한국어 AI 에이전트 — 아키텍처\\n(도구 사용 에이전트 루프 + 4개 가드레일 seam + 3계층 인젝션 방어 + 관통 PIIVault)",
        splines="ortho",
        nodesep="0.35",
        ranksep="0.45",
    )
    g.attr("edge", fontname=FONT, fontsize="9", color="#455a64")

    # 신뢰 입력 + 계획
    node(g, "user", "① 사용자 요청 (신뢰)", C_TRUST, shape="stadium")
    node(
        g,
        "planner",
        "P-LLM 계획 → FROZEN plan  [Tier C]\\n(비신뢰 콘텐츠 진입 前 도구 시퀀스 고정)",
        C_MODEL,
    )

    # 에이전트 루프 클러스터
    with g.subgraph(name="cluster_loop") as c:
        c.attr(
            label="에이전트 루프  (google-genai, AFC off, iteration cap)",
            style="rounded,dashed",
            color="#37474f",
            fontname=FONT,
            fontsize="13",
            labeljust="l",
        )
        node(
            c,
            "before_model",
            "╔ before_model ╗\\nDLP 비식별(vault)·spotlight\\n·Prompt Guard 2 (LOG-ONLY)",
            C_DLP,
        )
        node(
            c,
            "model",
            "모델 호출  (P-LLM)\\nGemini / OpenAI / Gemma\\n+ safety_settings ×4",
            C_MODEL,
        )
        node(
            c,
            "before_tool",
            "★ before_tool 게이트 ★  [Tier A+B]\\n위험티어 allowlist · 인자검증(수신자/URL/SQL)\\n· taint(.sources) 검사 · 토큰 rehydrate(내부만)",
            C_GATE,
        )
        node(
            c,
            "tools",
            "도구 실행 (mock)\\nlookup_customer · search_kb\\nissue_refund · send_email · escalate",
            C_TRUST,
        )
        node(
            c,
            "after_tool",
            "╔ after_tool ╗  [Tier B/C]\\nQ-LLM 격리(Gemini Flash/로컬 Gemma)→Pydantic\\n·Prompt Guard 재스캔(LOG)·DLP 토큰화·Tainted 래핑",
            C_DLP,
        )

    node(
        g,
        "after_model",
        "╔ after_model ╗\\nShieldGemma-2B 유해 + 한국어 레일\\n· DLP 재스캔 · HTML-escape · deanonymize",
        C_MODEL,
    )
    node(g, "output", "② 최종 응답 + 트레이스\\n(plan·tool·taint·blocked·DLP spans·HITL·latency)", C_OUT, shape="stadium")

    # 위협 + 관통 vault
    node(
        g,
        "threat",
        "⚠ 비신뢰 데이터\\n도구출력·RAG 문서 내 숨은 명령\\n(간접 프롬프트 인젝션, IPI)",
        C_THREAT,
        shape="note",
    )
    node(
        g,
        "vault",
        "PIIVault  (요청당 정확히 1개, 루프 관통)\\nPresidio 가역 익명화 · 한국어 인식기(KR_RRN 등)\\ncoreference 유지 · fail-closed · 무로깅(PIPA)",
        C_DLP,
        shape="cylinder",
    )
    node(
        g,
        "hitl",
        "HITL 승인\\n(비가역/유출 도구)",
        C_GATE,
        shape="hexagon",
    )

    # 메인 흐름
    g.edge("user", "planner")
    g.edge("planner", "before_model")
    g.edge("before_model", "model")
    g.edge("model", "before_tool", label="functionCall(들)")
    g.edge("before_tool", "tools", label="None=허용")
    g.edge("before_tool", "hitl", label="게이트", style="dashed")
    g.edge("tools", "after_tool")
    g.edge("after_tool", "before_model", label="function_response (반복)", constraint="false", style="bold", color="#00695c")
    g.edge("model", "after_model", label="text-only=종료", style="bold")
    g.edge("after_model", "output")

    # 위협/차단
    g.edge("threat", "after_tool", label="주입 진입", color="#c62828", style="dashed")
    g.edge("before_tool", "output", label="BLOCK(카나리 차단)", color="#c62828", style="dashed", constraint="false")

    # vault 관통 (점선)
    for seam in ("before_model", "before_tool", "after_tool", "after_model"):
        g.edge("vault", seam, style="dotted", color="#1565c0", arrowhead="none", constraint="false")

    # 범례
    with g.subgraph(name="cluster_legend") as lg:
        lg.attr(label="범례", style="rounded", color="#90a4ae", fontname=FONT, fontsize="12", labeljust="l")
        node(lg, "lg_gate", "진짜 방어(결정적): Tier A 게이트·Tier B taint·Tier C 고정계획", C_GATE)
        node(lg, "lg_tele", "텔레메트리(LOG-ONLY, 우회가능): Prompt Guard·ShieldGemma", C_TELE)
        node(lg, "lg_dlp", "DLP/PII: PIIVault·Presidio 한국어 인식기", C_DLP)
        node(lg, "lg_model", "Google/LLM 모델: Gemma·Gemini·OpenAI", C_MODEL)
        lg.edge("lg_gate", "lg_tele", style="invis")
        lg.edge("lg_tele", "lg_dlp", style="invis")
        lg.edge("lg_dlp", "lg_model", style="invis")

    return g


if __name__ == "__main__":
    import pathlib

    out = pathlib.Path(__file__).resolve().parent / "architecture"
    dot = build()
    dot.render(str(out), format="png", cleanup=False)
    dot.render(str(out), format="svg", cleanup=True)
    print(f"wrote {out}.png and {out}.svg")
