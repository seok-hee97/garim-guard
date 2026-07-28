# Gemini 교차-provider 확인 실험 — 시도 기록 (미완)

**시도:** `python -m eval.expansion_v2 --provider gemini --arms none,tier_a --limit 6` (30 공격 + 6 benign × 2 arm).

**결과: 미완 — 무료 티어 쿼터 소진(429 RESOURCE_EXHAUSTED).**
- 60 공격 에피소드 중 **59건이 429 quota 에러**로 실패, 1건만 완료(비에러) → 통계적으로 무의미(n=1).
- 진단(단발 호출 3회): 1건 성공 후 2건 429 → 모델은 정상 동작하나 **free-tier 쿼터가 배치 실행을 허용하지 않음**.
- 이는 `eval/run.py` 가 provider 기본값을 OpenAI 로 두는 이유(무료 Gemini 병목)와 동일한 제약.

**함의(정직):**
- 이것은 **모델 능력의 문제가 아니라 과금 티어(쿼터)의 문제**다. 단발 호출은 성공했다.
- 헤드라인 결과(v0.1 7-arm, v0.2 독립 시나리오 확대)는 **gpt-4o-mini 단일 provider** 로 측정됐다는
  기존 한계(RESULTS.md §5 "모델 단일")가 **여전히 유효**하다. 교차-provider 일반화는 미확인.
- 완전한 확인에는 **유료 Gemini 티어** 또는 쿼터 리셋 후 재실행(`--provider gemini`)이 필요하다.
  런타임 자체는 provider-무관(동일 4-seam·게이트)이므로 재실행만 하면 된다.

**부분 관찰(비통계적, 참고만):** 사전 단발 테스트에서 gemini-3.5-flash 는 `fake_system` 인젝션에
gpt-4o-mini 와 달리 발동하지 않고 iteration_cap 에 도달 — provider 별 인젝션 감수성이 다를 수 있음을
시사(헤드룸 차이). 이는 **재실행으로 검증해야 할 가설**이지 결과가 아니다.
