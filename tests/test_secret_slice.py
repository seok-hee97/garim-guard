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
    assert [r.text for r in generate_secret_slice()] == [
        r.text for r in generate_secret_slice()
    ]


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


def test_axis_d_placeholder_false_positives_are_the_documented_gap() -> None:
    """오탐이 남는 것이 정상이다 — §6.4.5 가 축 (d) 를 L2 파이프라인으로 규정한 근거다.

    다만 **없어지지도, 늘어나지도** 않아야 한다. 형태가 진짜와 같은 플레이스홀더만
    남고 `YOUR_API_KEY` 류는 걸러져야 한다.
    """
    rows = generate_secret_slice()
    negatives = [r for r in rows if not r.meta["expected_secret"]]
    flagged = [r for r in negatives if detect_secrets(r.text)]
    rate = len(flagged) / len(negatives)
    assert 0.05 < rate < 0.30, rate
    assert not any("YOUR_API_KEY" in r.text for r in flagged)


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
