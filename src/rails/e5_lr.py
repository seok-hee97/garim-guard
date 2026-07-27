"""E5LRRail — intfloat/multilingual-e5-small(동결) + LogisticRegression head.

Ko-AgentGuard 의 교차언어 업그레이드. e5 인코더는 학습하지 않고(freeze), 그 임베딩 위에
sklearn LR 만 학습한다. e5 가 다국어라 영어 학습 데이터로 fit 해도 한국어로 전이된다.

🔴 #1 함정: **모든 입력에 'query: ' 접두 필수**(학습·추론 동일). sentence-transformers 는
   자동으로 안 붙인다. 접두 불일치 시 성능 급락.
파이프라인: 'query: '+text → tokenize(max_length=512) → e5(no_grad) → masked mean-pool
           → L2 정규화(384d) → LR.

train: E5LRRail.fit(texts, labels) → joblib 저장.  infer: score(text)→[0,1].
⚠️ torch/transformers 필요(RunPod). 지연 임포트.
"""

from __future__ import annotations

from pathlib import Path

MODEL_ID = "intfloat/multilingual-e5-small"
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HEAD = ROOT / "train" / "artifacts" / "e5-lr-head.joblib"


class E5LRRail:
    name = "e5_lr"

    def __init__(self, head_path=None, threshold: float = 0.5):
        self.threshold = threshold
        self.head_path = Path(head_path) if head_path else DEFAULT_HEAD
        self._model = None
        self._tok = None
        self._lr = None

    def _load_encoder(self):
        import torch  # noqa: F401
        from transformers import AutoModel, AutoTokenizer
        if self._model is None:
            self._tok = AutoTokenizer.from_pretrained(MODEL_ID)
            self._model = AutoModel.from_pretrained(MODEL_ID).eval()

    def embed(self, texts: list[str]):
        """'query: ' 접두 → mean-pool → L2. numpy (N,384) 반환."""
        import torch
        self._load_encoder()
        prefixed = ["query: " + (t or "") for t in texts]
        out = []
        for i in range(0, len(prefixed), 64):
            batch = prefixed[i:i + 64]
            enc = self._tok(batch, padding=True, truncation=True, max_length=512,
                            return_tensors="pt").to(self._model.device)
            with torch.no_grad():
                hidden = self._model(**enc).last_hidden_state       # (B,T,384)
            mask = enc["attention_mask"].unsqueeze(-1).float()
            pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
            pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
            out.append(pooled.cpu())
        return torch.cat(out).numpy()

    def fit(self, texts: list[str], labels: list[int]):
        from sklearn.linear_model import LogisticRegression
        import joblib
        X = self.embed(texts)
        lr = LogisticRegression(max_iter=2000, class_weight="balanced", C=4.0)
        lr.fit(X, labels)
        self._lr = lr
        self.head_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(lr, self.head_path)
        return self

    def _ensure_head(self):
        import joblib
        if self._lr is None:
            if not self.head_path.exists():
                raise FileNotFoundError(f"e5 LR head 없음: {self.head_path}. 먼저 fit() 하라.")
            self._lr = joblib.load(self.head_path)

    def score(self, text: str) -> float:
        if not text:
            return 0.0
        self._ensure_head()
        X = self.embed([text])
        return float(self._lr.predict_proba(X)[0][1])

    def predict_proba(self, texts: list[str]):
        """배치 채점 — 평가용(한 번에 임베딩 후 LR). P(inject) 리스트."""
        self._ensure_head()
        X = self.embed(list(texts))
        return [float(p[1]) for p in self._lr.predict_proba(X)]
