#!/usr/bin/env bash
# SafeAgent-KR — RunPod GPU 런북 (RTX 4090 24GB, SSD 확장). 검증된 스펙 반영.
# 사전: 이 repo 클론, .env 에 GEMINI/OPENAI + HF_TOKEN, ShieldGemma 라이선스 HF 계정 수락.
#   bash scripts/run_gpu.sh
set -euo pipefail

export HF_HOME=${HF_HOME:-/workspace/hf-cache}     # 영속 볼륨(컨테이너 오버레이 아님)
source .env 2>/dev/null || true
: "${HF_TOKEN:?HF_TOKEN 필요 — ShieldGemma gated}"

echo "== 0. 환경 (transformers>=4.51.3: Kanana/Gemma-2 전부 충족) =="
pip install -U "transformers>=4.51.3" "torch>=2.2" "datasets>=2.20" \
    accelerate sentence-transformers scikit-learn seqeval duckdb joblib huggingface_hub
python -c "import torch; print('CUDA:', torch.cuda.is_available(), torch.cuda.get_device_name(0))"
python -c "from huggingface_hub import login; import os; login(token=os.environ['HF_TOKEN'])"

echo "== 1. 학습(파인튜닝) — 각 <4GB VRAM, 독립 산출물 =="
python -m eval.train_pii_ner        # R6: klue-roberta-small 토큰분류 (openpii-ko)
python -m eval.train_harm           # R7: klue-roberta-base 멀티라벨 (K-MHaS, BCEWithLogits)
python -m eval.train_rail --encoder e5   # Ko-AgentGuard e5+LR 업그레이드 (선택)

echo "== 2. 추론 레일 → KoTIP 한국어 채널갭 (P1a/P1b) =="
python -m eval.kanana_compare --rail kanana        # Kanana: a vs b1(대화붕괴) vs b2 + 난독화
python -m eval.kanana_compare --rail shieldgemma   # ShieldGemma: EN-vs-KO 갭 (log-only)

echo "== 3. 애블레이션에 GPU 레일 편입 (log-only 행 추가) =="
# 레일별로 스윕(카세트 replay 로 모델 호출 없이 레일 점수만 갱신 가능하면 이상적).
python -m eval.run --split test --provider openai --arms classifier_gate --rail kanana
python -m eval.ablation                            # 표 재집계

echo "== 4. (옵션) kanana-8b 유해 비교 — 마지막·단독 (16GB) =="
# 다른 레일 종료 후에만. VRAM 경쟁 금지.
# python -m eval.train_harm --compare kanana8b

echo "완료. results/ 의 channel_gap.json · ablation_table.md · detector_multi.json 확인."
