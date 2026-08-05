# 가림(Garim)

가림은 앱·챗봇의 프롬프트가 외부 LLM API로 나가기 전에 한국 개인정보, 시크릿,
기밀 표식과 프롬프트 공격을 로컬에서 검사하는 라이브러리입니다. 현재 저장소에는
외부 모델 없이 동작하는 P0(L0) 코어와 L1 다중 헤드 모델의 연동 계약이 구현되어
있습니다. 전체 기획과 모델·평가 계획은 [설계 문서](docs/DESIGN.md)를 기준으로 합니다.

## 현재 구현 범위

- 손실 없는 세그먼트 분리와 prose/code/structured/log 타입 판정
- 7단계 가역 정규화, `norm_nospace`, base64/hex 1회 디코드, 원문 스팬 역매핑
- 한국 식별번호·공통 PII·시크릿·기밀 표식·고정밀 공격 문구 L0 규칙
- `allow / mask / warn / block` 정책과 YAML 검증
- 세션/태스크 볼트, 동일값 토큰 유지, 관용적 토큰 복원과 한국어 조사 교정
- L1 단일 인코더/4개 헤드 출력 계약과 실패 시 `degraded` 격리
- 라이브러리 API와 `garim check`, `garim scan` CLI

L1 ONNX 가중치와 L2 로컬 LLM은 아직 저장소에 포함하지 않습니다. L1 백엔드를
주입하지 않은 상태는 정상적인 P0 프로파일이며 `degraded`로 표시되지 않습니다.
현재 `thorough` 모드는 L2 미구현을 숨기지 않고 `warn`, `degraded=true`를 반환합니다.

## 설치와 사용

Python 3.11 환경에서 다음과 같이 설치합니다.

```bash
python -m pip install -e '.[dev]'
garim check '연락처는 user@example.test 입니다'
printf '검사할 프롬프트' | garim check --json
garim scan ./prompts
```

라이브러리에서는 입력 역할을 보존할 수 있습니다.

```python
from garim import Inspector, Segment

inspector = Inspector("config/policy.yaml")
result = inspector.check([
    Segment("당신은 상담 도우미입니다.", origin="system"),
    Segment("문의 내용을 확인해 주세요.", origin="user"),
])

if result.masked_segments is not None:
    outbound_messages = result.masked_segments
```

`CheckResult.public_dict()`는 볼트 원문을 직렬화하지 않습니다. 외부 API의 응답은
같은 `result.vault`의 `restore()`로 앱에 돌아오는 경로에서만 복원합니다.

## 개발

```bash
python -m pytest
ruff check garim tests tools
```

코드는 Apache-2.0입니다. 향후 KLUE-NER(CC-BY-SA-4.0)을 사용해 공개하는 L1
가중치는 코드와 별도로 데이터 및 가중치 라이선스를 병기해야 합니다.
