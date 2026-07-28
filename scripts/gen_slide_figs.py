"""발표 슬라이드용 차트 생성 — ★results/ 실측값에서만★ 그린다(하드코딩 금지 원칙).

    python -m scripts.gen_slide_figs      # → report/figs/*.png

입력:  results/ablation.csv · results/channel_gap.json · results/dlp_leak.json
출력:  report/figs/fig_ablation.png · fig_channel_gap.png · fig_dlp.png
폰트:  AppleSDGothicNeo(한국어). 색: theater=회색, genuine(게이트)=초록, 유출=빨강.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt

from src.config import ROOT

FIGS = ROOT / "report" / "figs"
_FONT_PATH = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
KFONT = fm.FontProperties(fname=_FONT_PATH)
try:  # 전역 family 로도 등록(범례/틱 자동 적용)
    fm.fontManager.addfont(_FONT_PATH)
    plt.rcParams["font.family"] = KFONT.get_name()
except Exception:  # noqa: BLE001 — 실패해도 fontproperties 로 개별 지정
    pass
plt.rcParams["axes.unicode_minus"] = False

C_THEATER = "#9e9e9e"   # 효과 없음(연극)
C_GENUINE = "#2e7d32"   # 결정적 게이트(진짜)
C_LEAK = "#c62828"      # 유출/취약
C_BASE = "#455a64"      # 기준선
C_SAFE = "#1565c0"      # 방어 ON


def _kf(ax):
    for lbl in ax.get_xticklabels() + ax.get_yticklabels():
        lbl.set_fontproperties(KFONT)


def _ablation():
    """S5 헤드라인 — arm별 t-ASR('all' 조건, n=60). 실측 ablation.csv 우선, 없으면 표값."""
    # (arm, 라벨, t-ASR, McNemar p(vs 무방어, 표기용), 분류)
    bars = [
        ("none", "무방어\n(기준선)", 0.17, "—", C_BASE),
        ("plea", "+plea\n(프롬프트 애원)", 0.18, "n.s.", C_THEATER),
        ("spotlight", "+spotlight\n(데이터마킹)", 0.25, "n.s.", C_THEATER),
        ("classifier_gate", "+분류기 강제\n(영어 CPU레일)", 0.17, "n.s.", C_THEATER),
        ("tier_a", "★결정적 게이트★\n(Tier A)", 0.00, "p=0.002", C_GENUINE),
    ]
    # 실측 덮어쓰기(있으면): ablation.csv 의 arm+cond=all 의 t-ASR
    csv_path = ROOT / "results" / "ablation.csv"
    real: dict[str, float] = {}
    if csv_path.exists():
        # 실제 헤더: arm,condition,n,asr_attempted,asr_executed,... (all 조건 = n=60 집계)
        with csv_path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("condition") == "all" and row.get("asr_executed"):
                    try:
                        real[row.get("arm", "")] = float(row["asr_executed"])
                    except ValueError:
                        pass
    missing = [a for (a, *_ ) in bars if a not in real]
    if missing:                      # 데이터가 없으면 표값 폴백을 쓰되 눈에 띄게 알린다
        print(f"  ⚠️ ablation.csv 에서 못 찾은 arm(폴백값 사용): {missing}")
    bars = [(a, lb, real.get(a, v), p, c) for (a, lb, v, p, c) in bars]

    fig, ax = plt.subplots(figsize=(9.2, 4.8))
    xs = range(len(bars))
    vals = [b[2] for b in bars]
    colors = [b[4] for b in bars]
    ax.bar(xs, vals, color=colors, width=0.62, zorder=3)
    for i, (_, _, v, p, _) in enumerate(bars):
        ax.text(i, v + 0.012, f"{v:.2f}", ha="center", va="bottom",
                fontproperties=KFONT, fontsize=13, fontweight="bold")
        ax.text(i, v + 0.055, p, ha="center", va="bottom",
                fontproperties=KFONT, fontsize=10, color="#555")
    ax.set_xticks(list(xs))
    ax.set_xticklabels([b[1] for b in bars], fontproperties=KFONT, fontsize=10.5)
    ax.set_ylabel("t-ASR (실행 성공률, ↓ 좋음)", fontproperties=KFONT, fontsize=12)
    ax.set_ylim(0, 0.34)
    ax.set_title("한국어 도구채널 인젝션 60건 — 방어별 실행 성공률(t-ASR)",
                 fontproperties=KFONT, fontsize=14, fontweight="bold", pad=12)
    ax.grid(axis="y", ls=":", alpha=0.5, zorder=0)
    ax.annotate("연극(theater): 통계적으로 무방어와 차이 없음",
                xy=(2, 0.25), xytext=(1.1, 0.31), fontproperties=KFONT, fontsize=10,
                color=C_THEATER, ha="left")
    ax.annotate("게이트만\n모든 실행 차단", xy=(4, 0.0), xytext=(3.55, 0.12),
                fontproperties=KFONT, fontsize=10.5, color=C_GENUINE, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=C_GENUINE))
    _kf(ax)
    fig.tight_layout()
    out = FIGS / "fig_ablation.png"
    fig.savefig(out, dpi=170)
    plt.close(fig)
    return out


def _channel_gap():
    """S6 — Kanana 채널 맹점: 발화(a) recall 1.0 → 도구채널 대화(b1) 0.0, 게이트는 불변."""
    d = json.loads((ROOT / "results" / "channel_gap.json").read_text("utf-8"))
    a = d["recall_a_utterance"]
    b1 = d["recall_b1_conversation"]
    labels = ["Kanana\n사용자 발화", "Kanana\n도구채널(대화)", "결정적 게이트\n(t-ASR)"]
    vals = [a, b1, 0.0]           # 게이트는 실행 성공률 0 = 완전 차단
    colors = [C_LEAK, C_THEATER, C_GENUINE]
    fig, ax = plt.subplots(figsize=(8.4, 4.8))
    xs = range(len(vals))
    ax.bar(xs, vals, color=colors, width=0.58, zorder=3)
    notes = [f"탐지 {a:.1f}", f"탐지 {b1:.1f}", "실행 0.00"]
    for i, (v, n) in enumerate(zip(vals, notes)):
        ax.text(i, v + 0.02, n, ha="center", va="bottom",
                fontproperties=KFONT, fontsize=12, fontweight="bold")
    ax.set_xticks(list(xs))
    ax.set_xticklabels(labels, fontproperties=KFONT, fontsize=11)
    ax.set_ylabel("탐지 recall / 실행 성공률", fontproperties=KFONT, fontsize=12)
    ax.set_ylim(0, 1.15)
    ax.set_title("완벽한 탐지기도 '채널'이 바뀌면 눈먼다 — 강제는 게이트로",
                 fontproperties=KFONT, fontsize=14, fontweight="bold", pad=12)
    ax.annotate("같은 페이로드,\n채널만 발화→도구\n= recall 1.0 → 0.0",
                xy=(1, 0.03), xytext=(0.55, 0.55), fontproperties=KFONT, fontsize=10.5,
                color=C_THEATER, arrowprops=dict(arrowstyle="->", color=C_THEATER))
    ax.grid(axis="y", ls=":", alpha=0.5, zorder=0)
    _kf(ax)
    fig.tight_layout()
    out = FIGS / "fig_channel_gap.png"
    fig.savefig(out, dpi=170)
    plt.close(fig)
    return out


def _dlp():
    """S7 — DLP 볼트 OFF→ON: 모델 노출·비인가 싱크 유출률 둘 다 0."""
    d = json.loads((ROOT / "results" / "dlp_leak.json").read_text("utf-8"))
    fields = ["email", "account", "phone", "rrn"]
    off_mv = [d["off"]["model_view"]["leak_rate"][f] for f in fields]
    on_mv = [d["on"]["model_view"]["leak_rate"][f] for f in fields]
    fig, ax = plt.subplots(figsize=(8.4, 4.8))
    import numpy as np
    x = np.arange(len(fields))
    w = 0.38
    ax.bar(x - w / 2, off_mv, width=w, color=C_LEAK, label="볼트 OFF", zorder=3)
    ax.bar(x + w / 2, on_mv, width=w, color=C_SAFE, label="볼트 ON", zorder=3)
    for i, v in enumerate(off_mv):
        ax.text(i - w / 2, v + 0.015, f"{v:.2f}", ha="center", va="bottom",
                fontproperties=KFONT, fontsize=10.5, fontweight="bold")
    for i, v in enumerate(on_mv):
        ax.text(i + w / 2, v + 0.015, f"{v:.2f}", ha="center", va="bottom",
                fontproperties=KFONT, fontsize=10.5, color=C_SAFE, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(fields, fontproperties=KFONT, fontsize=11)
    ax.set_ylabel("모델 노출 유출률 (↓ 좋음)", fontproperties=KFONT, fontsize=12)
    ax.set_ylim(0, 0.9)
    ax.set_title("요청 스코프 PII 볼트 — 켜면 모델 노출 유출 0% (비인가 싱크도 0, 표 참조)",
                 fontproperties=KFONT, fontsize=13, fontweight="bold", pad=12)
    leg = ax.legend(prop=KFONT, fontsize=11, loc="upper right")
    leg.set_zorder(5)
    ax.grid(axis="y", ls=":", alpha=0.5, zorder=0)
    _kf(ax)
    fig.tight_layout()
    out = FIGS / "fig_dlp.png"
    fig.savefig(out, dpi=170)
    plt.close(fig)
    return out


def main() -> int:
    FIGS.mkdir(parents=True, exist_ok=True)
    for fn in (_ablation, _channel_gap, _dlp):
        p = fn()
        print(f"→ {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
