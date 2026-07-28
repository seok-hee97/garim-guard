"""등록용 모델 아키텍처 이미지 생성기 — ★실제 구현 상태★를 정확히 반영(R5-#2).

출력: docs/모델_아키텍처_사진.png (NIPA 등록 폼 첨부용)
실행: python -m scripts.gen_model_arch
필요: graphviz(dot) + python-graphviz, 한국어 폰트(AppleGothic)

기존 이미지의 부정확 표기를 정정:
 - provider: Gemini·OpenAI (Gemma provider 없음 → 제거)
 - HITL 대상: issue_refund(환불)만 (send_email 은 승인 없이 실행)
 - Tier C(격리)·after_model 은 ⚠️설계전용/미구현으로 명시
"""

from __future__ import annotations

import graphviz

from src.config import ROOT

FONT = "AppleGothic"
C_TRUST = {"fillcolor": "#e8f5e9", "color": "#2e7d32"}   # 신뢰/사용자·authz
C_GATE = {"fillcolor": "#c8e6c9", "color": "#1b5e20"}    # ★결정적 강제(구현)
C_DLP = {"fillcolor": "#e3f2fd", "color": "#1565c0"}     # DLP/PII
C_MODEL = {"fillcolor": "#fff3e0", "color": "#e65100"}   # LLM provider
C_TELE = {"fillcolor": "#f5f5f5", "color": "#9e9e9e"}    # LOG-ONLY·미구현
C_HITL = {"fillcolor": "#fff8e1", "color": "#f9a825"}    # HITL 승인
C_OUT = {"fillcolor": "#ede7f6", "color": "#4527a0"}     # 출력


def _n(g, name, label, pal, shape="box", **extra):
    g.node(name, label=label, shape=shape, style="filled,rounded", fontname=FONT,
           fontsize="11", margin="0.16,0.10", penwidth="1.7", **pal, **extra)


def build() -> graphviz.Digraph:
    g = graphviz.Digraph("model_arch")
    g.attr(rankdir="TB", bgcolor="white", fontname=FONT, labelloc="t", fontsize="19",
           label=("SafeAgent-KR — 한국어 도구 사용 AI 에이전트 안전 런타임\\n"
                  "ReAct 4-seam 루프 · 결정적 Tier A 게이트 · 요청스코프 PII Vault · authN/authZ · HITL"),
           splines="ortho", nodesep="0.38", ranksep="0.5")

    # 입구: 인증 + 고객 바인딩
    _n(g, "req", "① 클라이언트 요청\\n+ X-API-Key", C_TRUST, shape="stadium")
    _n(g, "authz", "authN/authZ · principal 해석\\n대화-고객(customer_id) 바인딩\\n(confused-deputy 구조적 차단)", C_TRUST)
    _n(g, "vault", "요청스코프 PII Vault\\n(가역 토큰화 · 홉 간 일관)", C_DLP, shape="cylinder")

    with g.subgraph(name="cluster_loop") as c:
        c.attr(label="ReAct 에이전트 루프 (AFC off · iteration cap · deadline)",
               style="rounded,dashed", color="#37474f", fontname=FONT, fontsize="13", labeljust="l")
        _n(c, "bm", "╔ before_model ╗\\nDLP 토큰화 · plea/spotlight\\n· 분류기 [LOG-ONLY]", C_DLP)
        _n(c, "model", "모델 (P-LLM)\\nGemini · OpenAI\\n+ safety_settings", C_MODEL)
        _n(c, "am", "╔ after_model ╗\\n⚠️ pass-through(의도적 no-op)\\nprovider 안전필터 위임", C_TELE)
        _n(c, "bt", "★ before_tool — 결정적 게이트 ★\\n위험티어 · allowlist · 인자 스키마\\n· egress DLP scan · authz(고객 바인딩)\\n· taint 검사(Tier B)", C_GATE)
        _n(c, "tools", "도구 실행\\nlookup_customer · search_kb\\nissue_refund · send_email · escalate", C_TRUST)
        _n(c, "at", "╔ after_tool ╗\\nTier B taint 래핑 · DLP 토큰화\\n· 분류기 재스캔 [LOG-ONLY]", C_DLP)

    _n(g, "hitl", "HITL 승인 큐\\n★issue_refund(환불)만 승인 대기★\\n승인자 역할 · 요청자≠승인자\\n(테넌트 scope · 멱등 실행)", C_HITL)
    _n(g, "backend", "상태 백엔드(ServiceWorld)\\n환불 원장 · 이메일 outbox\\n멱등성 · 불변 소유권 메타", C_TRUST, shape="cylinder")
    _n(g, "out", "② 최종 응답 + 트레이스\\n(attempted/executed · gate_rule\\n· DLP spans · HITL · latency)", C_OUT, shape="stadium")

    # 흐름
    g.attr("edge", fontname=FONT, fontsize="9", color="#455a64")
    g.edge("req", "authz")
    g.edge("authz", "bm")
    g.edge("bm", "model")
    g.edge("model", "am", label="text-only=종료")
    g.edge("am", "bt")
    g.edge("bt", "tools", label="ALLOW")
    g.edge("bt", "hitl", label="requires_hitl\\n(환불)", color="#f9a825")
    g.edge("tools", "at")
    g.edge("at", "bt", label="반복", style="dashed", constraint="false")
    g.edge("hitl", "backend", label="승인 후 실행", color="#f9a825")
    g.edge("tools", "backend", style="dotted", arrowhead="none", constraint="false")
    g.edge("am", "out", label="종료 시", style="bold")
    for s in ("bm", "bt", "at"):
        g.edge("vault", s, style="dotted", color="#1565c0", arrowhead="none", constraint="false")

    # 3계층 + 범례
    with g.subgraph(name="cluster_tier") as t:
        t.attr(label="3계층 인젝션 방어 (전부 결정적)", style="rounded", color="#90a4ae",
               fontname=FONT, fontsize="12", labeljust="l")
        _n(t, "ta", "Tier A — per-tool 정책 게이트\\n✅구현 · 문자열 미판독(난독화 불변)", C_GATE)
        _n(t, "tb", "Tier B — 오염추적(taint)\\n✅구현 · 비신뢰가 싱크 선택 불가", C_GATE)
        _n(t, "tc", "Tier C — 격리 Q-LLM/고정계획\\n⚠️설계전용·미구현(사전등록 branch D)", C_TELE)
        t.edge("ta", "tb", style="invis"); t.edge("tb", "tc", style="invis")

    with g.subgraph(name="cluster_legend") as lg:
        lg.attr(label="범례", style="rounded", color="#90a4ae", fontname=FONT, fontsize="12", labeljust="l")
        _n(lg, "lg1", "결정적 강제(구현): Tier A·Tier B·authz·HITL", C_GATE)
        _n(lg, "lg2", "LOG-ONLY 텔레메트리 / 미구현: 분류기(Kanana·ShieldGemma)·Tier C·after_model", C_TELE)
        _n(lg, "lg3", "DLP/PII: 요청스코프 Vault · Presidio 한국어 인식기", C_DLP)
        _n(lg, "lg4", "LLM provider(구현): Gemini · OpenAI", C_MODEL)
        lg.edge("lg1", "lg2", style="invis"); lg.edge("lg2", "lg3", style="invis"); lg.edge("lg3", "lg4", style="invis")

    return g


def main() -> int:
    g = build()
    out = ROOT / "docs" / "모델_아키텍처_사진"
    g.render(str(out), format="png", cleanup=True)
    print(f"→ {out.with_suffix('.png').relative_to(ROOT)} 생성")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
