"""L1 백엔드 예시 1 — 이름 목록(의존성 없음).

고객 · 임직원 이름 목록에 있는 이름을 성명(PERSON)으로 가린다. 학습된 모델이 아니어도
`L1Backend` 계약(`name` + `infer()` → `L1Output`)만 지키면 L1 자리에 붙는다.

    GARIM_NAME_LIST=names.txt garim --l1-backend examples/backends/l1_name_list.py:build \\
        check '홍길동 고객님 건입니다'

한계: 앞에 한글 음절이 붙으면 잡지 않는다(`김정수` 안의 `정수`). 뒤쪽은 조사가 붙으므로 따지지
않는다 — 두 글자 이름은 다른 낱말의 첫머리(`정수기`)에서도 걸린다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from garim.detect.l1 import L1Entity, L1Output
from garim.models import ContentType, Origin


class NameListBackend:
    name = "name-list"

    def __init__(self, names: list[str]) -> None:
        unique = sorted({name.strip() for name in names if name.strip()}, key=len, reverse=True)
        if not unique:
            raise ValueError("name list is empty")
        # 긴 이름부터 — `홍길동` 과 `홍길` 이 둘 다 있으면 긴 쪽을 잡는다.
        self._pattern = re.compile(r"(?<![가-힣])(?:" + "|".join(map(re.escape, unique)) + ")")

    def infer(
        self,
        text: str,
        *,
        normalization_delta: dict[str, int],
        origin: Origin,
        content_type: ContentType,
    ) -> L1Output:
        del normalization_delta, origin, content_type
        # 점수 1.0 → 런타임 신뢰도 0.9(L1 PII 는 ×0.9) — 기본 임계 l1_pii 0.5 를 넘는다.
        return L1Output(
            entities=[
                L1Entity("PERSON", match.span(), 1.0) for match in self._pattern.finditer(text)
            ]
        )


def build() -> NameListBackend:
    path = os.environ.get("GARIM_NAME_LIST")
    if not path:
        raise ValueError("set GARIM_NAME_LIST to a UTF-8 file with one name per line")
    return NameListBackend(Path(path).read_text(encoding="utf-8").splitlines())
