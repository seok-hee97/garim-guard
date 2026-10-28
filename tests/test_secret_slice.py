"""§8.1-8d 축 (c)(d) 평가 슬라이스와 그것이 드러낸 §2.4 앵커 구멍."""

from __future__ import annotations

import pytest

from garim.detect.rules import detect_markers, detect_secrets
from garim.training.data import validate_corpus
from garim.training.secret_slice import generate_marker_slice, generate_secret_slice


def test_slices_satisfy_the_corpus_contract() -> None:
    records = generate_secret_slice() + generate_marker_slice()
    report = validate_corpus(records)
    assert report["records"] == 700
    # 평가 전용이다 — 골격을 양성·음성이 공유하므로 템플릿 단위 분할과 양립하지 않는다.
    assert report["splits"] == {"test": 700}


def test_slices_are_deterministic() -> None:
    assert [r.text for r in generate_secret_slice()] == [r.text for r in generate_secret_slice()]


def test_positive_and_negative_share_the_carrier() -> None:
    """값으로만 갈려야 축 (d) 를 재는 것이다 — 문맥이 다르면 문맥을 재게 된다."""
    rows = {r.id: r for r in generate_secret_slice(per_bucket=5)}
    real, fake = rows["secret-real-0000"], rows["secret-placeholder-0000"]
    assert real.pair_id == fake.pair_id
    assert real.template_id == fake.template_id
    assert real.meta["expected_secret"] and not fake.meta["expected_secret"]


@pytest.mark.parametrize(
    ("value", "rule"),
    [
        ("test_sk_AbCd1234efGH5678ijKL", "toss_secret_key"),
        ("live_sk_AbCd1234efGH5678ijKL", "toss_secret_key"),
        ("test_gsk_AbCd1234efGH5678ijKL", "toss_secret_key"),
        ("KakaoAK AbCd1234efGH5678ijKL", "kakao_admin_key"),
        ("x-ncp-iam-access-key: ABCD1234efgh", "korean_service_header"),
        ("x-ncp-apigw-api-key: ABCD1234efgh", "korean_service_header"),
        ("serviceKey=abcd1234ABCD5678efgh", "public_data_service_key"),
        (r"C:\NPKI\yessign\signPri.key", "npki_certificate"),
    ],
)
def test_domestic_anchors_from_the_design_table_are_wired(value: str, rule: str) -> None:
    """§2.4 표의 11개 앵커 중 6개가 미구현이었다 — 축 (d) 재현율 0.640 의 원인이다."""
    findings = detect_secrets(value)
    assert findings, f"{value} matches no rule"
    assert rule in {item.rule for item in findings}


def test_axis_d_recall_holds_on_the_slice() -> None:
    rows = generate_secret_slice()
    positives = [r for r in rows if r.meta["expected_secret"]]
    recall = sum(bool(detect_secrets(r.text)) for r in positives) / len(positives)
    assert recall >= 0.9, recall


def test_placeholder_shaped_prefixes_are_downgraded_not_dropped() -> None:
    """★ 고정밀 prefix 도 플레이스홀더 검사를 거친다.

    `_PLACEHOLDER` 가 (A) 단계에 안 걸려 있어 `AKIAIOSFODNN7EXAMPLE`(AWS 공식 예제)과
    `sk-proj-xxxxxxxx` 가 신뢰도 1.0 으로 나갔다 — 축 (d) 오탐 0.170 의 실체다.

    버리지 않고 강등한다(§2.4 (C)) — `sk-proj-xxxx` 는 진짜 키를 부분 가린 것일 수도
    있다. Finding 은 남고 기본 임계 아래로 내려간다.
    """
    for value in ("AKIAIOSFODNN7EXAMPLE", "sk-proj-xxxxxxxxxxxxxxxxxxxx"):
        findings = detect_secrets(f"설정에 {value} 를 넣었습니다")
        assert findings, "강등이지 삭제가 아니다 — 감사에는 남아야 한다"
        assert all(f.rule.endswith("_placeholder") for f in findings)
        assert all(f.confidence < 0.5 for f in findings)


def test_real_secrets_keep_full_confidence() -> None:
    (finding,) = detect_secrets("설정에 sk-proj-AbCd1234efGH5678ijKL 를 넣었습니다")
    assert finding.confidence == 1.0
    assert not finding.rule.endswith("_placeholder")


def test_axis_d_verdict_level_false_positives_are_zero() -> None:
    """정책 판정 기준으로는 플레이스홀더가 하나도 안 올라온다 — CredSweeper 와 동률."""
    from garim import Inspector

    inspector = Inspector("config/policy.yaml")
    rows = generate_secret_slice()
    negatives = [r for r in rows if not r.meta["expected_secret"]]
    positives = [r for r in rows if r.meta["expected_secret"]]
    assert not any(inspector.check(r.text).leaks for r in negatives)
    recall = sum(bool(inspector.check(r.text).leaks) for r in positives) / len(positives)
    assert recall >= 0.9, recall


def test_axis_c_marker_slice_is_clean() -> None:
    rows = generate_marker_slice()
    positives = [r for r in rows if r.meta["expected_marker"]]
    negatives = [r for r in rows if not r.meta["expected_marker"]]
    assert all(detect_markers(r.text) for r in positives)
    assert not any(detect_markers(r.text) for r in negatives)


def test_slice_carries_no_real_credentials() -> None:
    """값은 전부 문서화된 테스트 값이거나 형태만 맞춘 난수다."""
    for record in generate_secret_slice():
        assert "sk-ant-" not in record.text
        assert "AIza" not in record.text
