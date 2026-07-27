# 온디바이스 가드레일 레일 — CPU 단계 관측 (2026-07-25)

## 결정: 온디바이스 분류기를 추가하되 enforcement 경계로는 만들지 않는다
축 분리: (1) 추론 위치(API vs 온디바이스)는 논지와 무관, (2) 역할(telemetry vs enforcement)이 쟁점.
- telemetry 레일: `classifiers.*` (스키마상 log_only 고정).
- enforcement negative-control: `defenses.classifier_gate` (fail-open, Tier A Block override 불가).

## CPU 레일 (torch 불필요, sklearn TF-IDF+LR) — 오늘 학습·측정 완료
학습 1663 / 테스트 304 (Nemotron 영어 in-domain, domain-grouped).

| 조건 | F1 | recall | 해석 |
|---|---|---|---|
| in-domain clean | 0.9936 | 0.9936 | 강한 탐지기 (약체 아님 → anti-strawman) |
| base64_wrap | **0.000** | 0.000 | 완전 붕괴 |
| homoglyph | 0.9705 | 0.9487 | 소폭 |
| zero_width / jamo_split | 0.9936 | 0.9936 | char n-gram 이 견딤 (정직한 미묘함) |

**P3 붕괴폭 = 0.9936** (사전등록 목표 ≥ 0.25 압도).

## 교차언어 실패 (P6d) — 영어 학습 레일은 한국어에 눈멂
| 입력 | ko_agentguard(영어학습) | heuristic(한국어 마커) |
|---|---|---|
| 한국어 오염문서(kb_004) | **0.022** (임계 0.5 통과) | 1.000 (격리) |
| 영어 인젝션(in-domain) | 0.948 | — |

→ classifier_gate 의 clean 발동(P6a)조차 **한국어 가능 레일(RunPod Kanana)** 없이는 불가능.

## 3겹 논지 증거 (요약)
1. 영어학습 탐지기 → 한국어에 눈멂 (0.022).
2. 탐지기가 반응해도 → base64 난독화에 F1 0 붕괴.
3. Tier A 게이트 → 언어·난독화 무관하게 차단 (money smoke: egress 위반, 텍스트 안 읽음).

## RunPod 이관 작업 (GPU)
- Kanana-safeguard-prompt-2.1b: 한국어 가능 레일 → classifier_gate clean 발동(P6a) 측정.
- ShieldGemma-2b: Google 온디바이스 안전분류기 log-only 텔레메트리 → EN-vs-KO 갭 실측.
- e5-small+LR: ko_agentguard 교차언어 업그레이드 (동일 RailScorer 계약으로 스왑).
- 카세트 record 후 replay 로 스윕(GPU 비의존).
