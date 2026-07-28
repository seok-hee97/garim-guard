"""KananaRail — kakaocorp/kanana-safeguard-prompt-2.1b 추론 레일 (RunPod GPU).

⚠️ torch/transformers 필요 → RunPod 에서만 실행. 로컬(torch 없음)에서는 import 시점엔
   에러 없고, .load()/.score() 호출 시 안내 후 실패한다(지연 임포트).

검증된 사용법 (workflow 2026-07):
 - LlamaForCausalLM, **bfloat16**(카드의 float16 아님 — config native dtype), trust_remote_code 불필요.
 - apply_chat_template([{user:text}]) → 템플릿이 assistant 헤더까지 붙여 다음 토큰이 taxonomy 코드.
 - 점수: 마지막 토큰 로짓에서 단일토큰 3개만 softmax.
     SAFE=128257, UNSAFE-A1(인젝션)=128256, UNSAFE-A2(리킹)=128258.
     P(주입) = 1 − P(SAFE) = p(A1)+p(A2).  (우리 방법 — 카드는 argmax만 문서화)
 - ★ 챗템플릿은 마지막 user+assistant 턴만 남기고 tool/기타 role 을 드롭 → 도구채널 구조적 맹점.
   score_conversation() 이 이 붕괴 경로(b1)를 그대로 노출한다.
"""

from __future__ import annotations

MODEL_ID = "kakaocorp/kanana-safeguard-prompt-2.1b"
TOK_SAFE, TOK_A1, TOK_A2 = 128257, 128256, 128258
MIN_TRANSFORMERS = (4, 51, 3)   # Llama-3 확장 vocab + safeguard chat template 정상 적용 하한(run_gpu.sh 와 동일)


def _assert_transformers_version() -> None:
    """버전 하한 검증 — 낮으면 apply_chat_template 이 assistant 헤더/특수토큰을 조용히
    다르게 구성해 마지막 토큰 로짓이 taxonomy 코드가 아니게 된다(무증상 오점수). 실패는 명시적으로."""
    import transformers
    ver = tuple(int(x) for x in transformers.__version__.split(".")[:3] if x.isdigit())
    if ver < MIN_TRANSFORMERS:
        raise RuntimeError(
            f"transformers {transformers.__version__} < {'.'.join(map(str, MIN_TRANSFORMERS))} — "
            "Kanana safeguard 채점이 무증상으로 틀립니다. `pip install -U 'transformers>=4.51.3'`.")


class KananaRail:
    name = "kanana_injection"

    def __init__(self, threshold: float = 0.5, model_id: str = MODEL_ID):
        self.threshold = threshold
        self.model_id = model_id
        self._model = None
        self._tok = None

    def load(self) -> None:
        import torch
        _assert_transformers_version()
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self._tok = AutoTokenizer.from_pretrained(self.model_id)
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_id, torch_dtype=torch.bfloat16, device_map="auto").eval()

    def _p_inject(self, messages: list[dict]) -> float:
        import torch
        if self._model is None:
            self.load()
        ids = self._tok.apply_chat_template(messages, tokenize=True, return_tensors="pt")
        ids = ids.to(self._model.device)
        with torch.no_grad():
            logits = self._model(ids).logits[0, -1, :]
        three = logits[[TOK_SAFE, TOK_A1, TOK_A2]].float()
        p = torch.softmax(three, dim=-1)
        return float(p[1] + p[2])   # P(주입) = P(A1)+P(A2) = 1 − P(SAFE)

    # ── RailScorer 계약 ──
    def score(self, text: str) -> float:
        """단일 발화(또는 도구출력 문자열)를 user content 로 채점 — arm a / b2."""
        if not text:
            return 0.0
        return self._p_inject([{"role": "user", "content": text}])

    # ── 채널 조건부 격차 (P1a): 대화 배선 b1 ──
    def score_conversation(self, messages: list[dict]) -> float:
        """멀티턴 대화를 그대로 채점. 챗템플릿이 tool/중간 턴을 드롭하므로, 도구채널에
        박힌 인젝션은 모델에 도달조차 못 한다 → 구조적으로 낮은 점수(b1)."""
        return self._p_inject(messages)
