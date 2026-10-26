---
language: [ko]
library_name: transformers
base_model: skt/A.X-Encoder-base
license: other
tags: [token-classification, prompt-injection, pii, korean]
---

# Garim L1 joint encoder

> 이 파일은 배포 전 채워야 하는 템플릿입니다. 빈 측정값을 예상치로 대체하지 마십시오.

## 모델

- 백본: `skt/A.X-Encoder-base`
- 백본 revision: `9708f9c404ace91efd25c06fac2d73413616f4ef`
- 구조: 공유 encoder + PII BIO 29-way + 공격 4-way + 이진 보조 헤드 2개
- 학습 seed: `13`, `37`, `73`
- 체크포인트/데이터 준비 commit: `TODO`

## 학습 데이터와 고지

| 데이터 | 용도 | 고정 revision/해시 | 라이선스 |
|---|---|---|---|
| KDPII revised | PII train/dev/test 연결성분 분할 | `4017f68284596ffc357dd23363fb9ab9eb2eb730f01973af8beded54430d3e74` | CC-BY-4.0 |
| KLUE-NER train | 필터된 PS→PERSON 학습 | `349481ec73fff722f88e0453ca05c77a447d967c` | CC-BY-SA-4.0 |
| PolyGuardMix ko | 교사 재라벨 공격 학습 | `5b7d93e9e9a636c6c152df071745e04130a21ff4` | CC-BY-4.0 |
| deepset prompt-injections train | 분할 후 한국어 번역·교사 재라벨 | `4f61ecb038e9c3fb77e21034b22511b523772cdd` | CC-BY-4.0 |
| bc-pii-ko | 값 사전만 사용 | `327c775b68850ca59cf4ff2ad0b084fcd4916da4` | CC-BY-4.0 |
| Garim synthetic | PII/공격 보강 | generator/version `TODO` | 프로젝트 생성 |

KLUE-NER의 CC-BY-SA-4.0 학습 사실과 출처를 명시합니다. 가중치 라이선스에 대한
법적 결론은 공개 전 별도로 검토하며 이 문서는 ShareAlike 전파 여부를 단정하지 않습니다.

## 교사

- `kakaocorp/kanana-safeguard-prompt-2.1b@167d74d4706b236580b0e48318337c7ac6ba7848`
- `google/gemma-4-E2B-it@3e22461f65e89153144f8adb70e3b8c2cc9845a7`
- `Qwen/Qwen3Guard-Gen-4B@6ec42827da0c1ff11e7a49dc269d2e810d27e108` (Gen-0.6B 에서 교체 — RUNPOD.md 6b 참조)

일반 다수결이 아니라 클래스별 두 교사 합의를 사용했습니다. 불일치·누락 행은 자동으로
`NONE` 처리하지 않고 사람 검토 큐로 분리했습니다.

## 결과

| 지표 | FP32 평균±표준편차 | ONNX QInt8 | 평가 근거 |
|---|---:|---:|---|
| PII character recall | TODO | TODO | 데이터셋별 분리 보고 |
| PII entity F1 | TODO | TODO | 데이터셋별 분리 보고 |
| 공격 macro recall/F1 | TODO | TODO | T4 + 외부 test |
| 공격 FPR | TODO | TODO | benign + 실 한국어 FPR 하네스 |
| 128/256/512 token p50/p95 | TODO | TODO | 기준 머신 사양 병기 |
| 파일 크기/RSS | TODO | TODO | 기준 머신 사양 병기 |

합성 T1 수치는 구현 회귀 지표이며 실분포 재현율의 증거로 주장하지 않습니다.

## 알려진 한계

- KDPII에 건강보험번호가 없어 `KR_NHI` 외부 실값 재현율 근거가 없습니다.
- KLUE PS는 유명인 중심이며 일반 고객·직원 성명 분포를 대표하지 않습니다.
- bc-pii-ko 문맥은 합성 번역체이므로 값만 사용했습니다.
- RRN 및 일부 국내 식별자는 적법한 비생성기 실값 근거가 없습니다.
- L2는 이 모델에 포함되지 않으며 별도 성능·지연 게이트 전까지 비활성입니다.
