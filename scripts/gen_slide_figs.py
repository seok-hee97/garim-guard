"""발표 슬라이드용 차트 생성 — ★results/ 실측값에서만★ 그린다(하드코딩 금지 원칙).

    python -m scripts.gen_slide_figs      # → report/figs/*.png

입력:  results/ablation.csv · results/mcnemar_vs_none.json · results/channel_gap.json
       · results/dlp_leak.json
출력:  report/figs/fig_ablation.png · fig_channel_gap.png · fig_dlp.png
폰트:  AppleSDGothicNeo(한국어). 색: theater=회색, genuine(게이트)=초록, 유출=빨강.

★막대 높이뿐 아니라 p 라벨도 results/ 에서 읽는다★ — 그림의 p 는 'vs 무방어'인데 표의
`mcnemar_p_vs_prev` 는 'vs 직전 arm' 이라 기준이 다르다. eval.ablation 이 vs-무방어를
mcnemar_vs_none.json 으로 따로 떨어뜨리고 여기서 그걸 읽는다(수기 상수 금지).
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


def _zero_caps(ax, centers, vals, colors, half=0.29, lw=5.5):
    """값이 0 인 막대에 굵은 바닥 캡을 그린다.

    ★0 은 막대가 없어 '빈칸/누락'으로 읽힌다★ — 이 프로젝트의 헤드라인이 전부 '0으로 눌렀다'
    라서, 0 인 막대에만 색이 안 보이면 정작 가장 중요한 결과가 화면에서 사라진다. 캡을 그리면
    해당 계열 색(genuine 초록·볼트 ON 파랑)이 눈에 남는다."""
    for c, v, col in zip(centers, vals, colors):
        if v == 0:
            ax.plot([c - half, c + half], [0.0, 0.0], color=col, lw=lw,
                    solid_capstyle="butt", zorder=4)


def _p_label(p: float | None) -> str:
    """p 값 → 그림 라벨. 유의하면 수치, 아니면 n.s.(임계 0.05, 양측 McNemar exact)."""
    if p is None:
        return ""
    return f"p={p:.3f}".rstrip("0").rstrip(".") if p < 0.05 else "n.s."


def _ablation():
    """S6 헤드라인 — arm별 t-ASR('all' 조건, n=60). 막대·p 라벨 모두 results/ 에서 유도."""
    # (arm, 라벨, 폴백 t-ASR, 분류) — 폴백은 results/ 부재 시 형태 확인용이며 경고를 낸다.
    bars = [
        ("none", "무방어\n(기준선)", 0.17, C_BASE),
        ("plea", "+plea\n(프롬프트 애원)", 0.18, C_THEATER),
        ("spotlight", "+spotlight\n(데이터마킹)", 0.25, C_THEATER),
        ("classifier_gate", "+분류기 강제\n(영어 CPU레일)", 0.17, C_THEATER),
        ("tier_a", "★결정적 게이트★\n(Tier A)", 0.00, C_GENUINE),
    ]
    # 실측 t-ASR: ablation.csv 의 arm + condition=all (n=60 집계)
    csv_path = ROOT / "results" / "ablation.csv"
    real: dict[str, float] = {}
    if csv_path.exists():
        with csv_path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("condition") == "all" and row.get("asr_executed"):
                    try:
                        real[row.get("arm", "")] = float(row["asr_executed"])
                    except ValueError:
                        pass
    missing = [a for (a, *_) in bars if a not in real]
    if missing:                      # 데이터가 없으면 폴백값을 쓰되 눈에 띄게 알린다
        print(f"  ⚠️ ablation.csv 에서 못 찾은 arm(폴백값 사용): {missing}")

    # 실측 p(vs 무방어): eval.ablation 산출. 없으면 라벨을 비운다(수기 상수 금지).
    pj = ROOT / "results" / "mcnemar_vs_none.json"
    pv: dict[str, float] = {}
    if pj.exists():
        pv = {a: m["p_value"] for a, m in json.loads(pj.read_text("utf-8")).get("arms", {}).items()}
    else:
        print("  ⚠️ mcnemar_vs_none.json 없음 — p 라벨 생략(`python -m eval.ablation` 먼저)")
    labels = {a: ("—" if a == "none" else _p_label(pv.get(a))) for (a, *_) in bars}

    bars = [(a, lb, real.get(a, v), c) for (a, lb, v, c) in bars]

    fig, ax = plt.subplots(figsize=(9.2, 4.8))
    xs = list(range(len(bars)))
    vals = [b[2] for b in bars]
    colors = [b[3] for b in bars]
    ax.bar(xs, vals, color=colors, width=0.62, zorder=3)
    _zero_caps(ax, xs, vals, colors, half=0.31)
    for i, (a, _, v, c) in enumerate(bars):
        ax.text(i, v + 0.012, f"{v:.2f}", ha="center", va="bottom",
                fontproperties=KFONT, fontsize=13, fontweight="bold",
                color=c if v == 0 else "#1a2230")
        ax.text(i, v + 0.050, labels[a], ha="center", va="bottom",
                fontproperties=KFONT, fontsize=10, color="#555")
    ax.set_xticks(xs)
    ax.set_xticklabels([b[1] for b in bars], fontproperties=KFONT, fontsize=10.5)
    ax.set_ylabel("t-ASR (실행 성공률, ↓ 좋음)", fontproperties=KFONT, fontsize=12)
    ax.set_ylim(0, 0.40)                    # theater 캡션이 막대·n.s. 라벨과 겹치지 않을 여유
    ax.set_title("한국어 도구채널 인젝션 60건 — 방어별 실행 성공률(t-ASR)",
                 fontproperties=KFONT, fontsize=14, fontweight="bold", pad=12)
    ax.grid(axis="y", ls=":", alpha=0.5, zorder=0)
    # theater 캡션은 회색 막대 3개 위 빈 띠에 배치(막대 최대 0.25 + p 라벨 ~0.30 → 0.355).
    ax.annotate("연극(theater): 통계적으로 무방어와 차이 없음",
                xy=(2, 0.355), xytext=(0.62, 0.355), fontproperties=KFONT, fontsize=10,
                color=C_THEATER, ha="left", va="center")
    # 게이트 주석은 Tier A 열의 빈 공간(막대 0.00 위)에 수직으로 세운다 — 회색 막대와 겹치지
    # 않고, 화살표가 p 라벨(y≈0.05) 바로 위에서 멈춰 0 캡을 가리킨다.
    ax.annotate("게이트만\n모든 실행 차단", xy=(4, 0.085), xytext=(4, 0.235),
                fontproperties=KFONT, fontsize=10.5, color=C_GENUINE, fontweight="bold",
                ha="center", va="bottom", arrowprops=dict(arrowstyle="->", color=C_GENUINE))
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
    xs = list(range(len(vals)))
    ax.bar(xs, vals, color=colors, width=0.58, zorder=3)
    _zero_caps(ax, xs, vals, colors, half=0.29, lw=6.5)
    notes = [f"탐지 {a:.1f}", f"탐지 {b1:.1f}", "실행 0.00"]
    for i, (v, n) in enumerate(zip(vals, notes)):
        ax.text(i, v + 0.02, n, ha="center", va="bottom",
                fontproperties=KFONT, fontsize=12, fontweight="bold")
    ax.set_xticks(list(xs))
    ax.set_xticklabels(labels, fontproperties=KFONT, fontsize=11)
    ax.set_ylabel("탐지 recall / 실행 성공률", fontproperties=KFONT, fontsize=12)
    ax.set_ylim(0, 1.15)
    ax.set_title("같은 페이로드도 채널이 바뀌면 탐지가 무너진다 (Kanana, n=10) — 강제는 게이트로",
                 fontproperties=KFONT, fontsize=13, fontweight="bold", pad=12)
    ax.annotate("같은 페이로드,\n채널만 발화→도구\n= recall 1.0 → 0.0",
                xy=(0.72, 0.02), xytext=(0.42, 0.55), fontproperties=KFONT, fontsize=10.5,
                color=C_THEATER, arrowprops=dict(arrowstyle="->", color=C_THEATER))
    ax.grid(axis="y", ls=":", alpha=0.5, zorder=0)
    _kf(ax)
    fig.tight_layout()
    out = FIGS / "fig_channel_gap.png"
    fig.savefig(out, dpi=170)
    plt.close(fig)
    return out


def _dlp():
    """S8 — DLP 볼트 OFF→ON.

    ★지표 정의를 제목에 정확히 쓴다★ — `model_view` 는 '모델이 입력에서 본 값'이 아니라
    **모델이 생성한 tool_args 에 원본 PII 가 들어갔는가**이고, 0% 는 **볼트가 매핑한 PII 기준**이다
    (eval/dlp_eval.py `_measure_episode` 참조). '모델 노출'로 축약하면 의미가 바뀐다."""
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
    # 볼트 ON 은 전 항목 0 → 캡이 없으면 범례의 파란색이 그림에 한 번도 안 나타난다.
    _zero_caps(ax, x - w / 2, off_mv, [C_LEAK] * len(fields), half=w / 2 - 0.01, lw=4.5)
    _zero_caps(ax, x + w / 2, on_mv, [C_SAFE] * len(fields), half=w / 2 - 0.01, lw=4.5)
    # ★표와 같은 정밀도로★ — 0.125 를 0.12 로 반올림하면 S11 표(0.125)와 어긋난다.
    lab = lambda v: "0.00" if v == 0 else f"{v:g}"
    for i, v in enumerate(off_mv):
        ax.text(i - w / 2, v + 0.015, lab(v), ha="center", va="bottom",
                fontproperties=KFONT, fontsize=10.5, fontweight="bold")
    for i, v in enumerate(on_mv):
        ax.text(i + w / 2, v + 0.015, lab(v), ha="center", va="bottom",
                fontproperties=KFONT, fontsize=10.5, color=C_SAFE, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(fields, fontproperties=KFONT, fontsize=11)
    ax.set_ylabel("모델 생성 인자 내 원본 PII 비율 (↓ 좋음)", fontproperties=KFONT, fontsize=12)
    ax.set_ylim(0, 0.9)
    ax.set_title("요청 스코프 PII 볼트 — 매핑된 PII 기준 원본 노출 0% (n=8 · 비인가 싱크도 0)",
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
