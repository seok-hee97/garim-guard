"""Ko-AgentGuard 인젝션 탐지 레일 — CPU 구현 (sklearn TF-IDF + LogisticRegression).

계약: RailScorer.score(text) -> [0,1] 인젝션 확률.

구현 사다리 (project_plan §4.10, 전부 동일 RailScorer 계약):
  - 이 파일(TF-IDF+LR): 의존성 sklearn 만, CPU, 오늘 학습 가능. char/word n-gram 이라
    한국어 형태소 분석 불필요. 난독화(제로폭·자모분해)에 취약함이 오히려 논지의 측정 대상.
  - (RunPod 업그레이드) e5-small 임베딩 + LR, 또는 Kanana 로짓 — 동일 인터페이스로 스왑.

학습/평가는 eval/train_rail.py 가 Nemotron projector 산출(data/kotip/v0.1/nemotron_rail.*)로 수행.
이 파일은 학습된 아티팩트(joblib)를 로드해 추론만 한다.

⚠️ 이것은 텍스트를 읽는 분류기다 → 난독화로 우회 가능. Tier A 게이트와 달리 강제 경계가
   될 수 없다. 헤드라인이 아니라 '측정 대상'이자 (classifier_gate arm 에서) negative control.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT = ROOT / "train" / "artifacts" / "ko-agentguard-lr.joblib"


class KoAgentGuardRail:
    name = "ko_agentguard"

    def __init__(self, artifact_path: str | Path | None = None, threshold: float = 0.5):
        self.threshold = threshold
        self._pipe: Any = None
        self._path = Path(artifact_path) if artifact_path else DEFAULT_ARTIFACT
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            raise FileNotFoundError(
                f"Ko-AgentGuard 아티팩트 없음: {self._path}. "
                "먼저 `python -m eval.train_rail` 로 학습하라."
            )
        import joblib
        self._pipe = joblib.load(self._path)

    def score(self, text: str) -> float:
        if not text or self._pipe is None:
            return 0.0
        # predict_proba[:,1] = 인젝션(label=1) 확률
        return float(self._pipe.predict_proba([text])[0][1])


def build_pipeline():
    """학습용 파이프라인 정의 (eval/train_rail.py 가 fit 한다).

    char n-gram(3-5) + word n-gram(1-2) 결합. char n-gram 은 한국어 토큰화 없이도
    동작하고, 자모 분해/제로폭 같은 표층 난독화에 대한 민감도를 자연스럽게 노출한다.
    """
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import FeatureUnion, Pipeline

    feats = FeatureUnion([
        ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, max_features=40000)),
        ("word", TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=2, max_features=20000)),
    ])
    return Pipeline([
        ("feats", feats),
        ("clf", LogisticRegression(max_iter=2000, class_weight="balanced", C=4.0)),
    ])
