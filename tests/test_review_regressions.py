"""외부 검토가 짚은 결함 다섯. 전부 재현했고, 여기서 되돌아오지 못하게 고정한다."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from garim import Inspector
from garim.detect.l1 import NORMALIZATION_DELTA_ORDER
from garim.models import Segment
from garim.proxy import _text_of, _with_text

_LEAKY = "hong@example.test"


def test_proxy_never_forwards_an_unmasked_text_part() -> None:
    """★ [높음] 마스킹본과 **원문이 함께** 상류로 나갔다.

    `_text_of` 는 텍스트 파트를 전부 합쳐 검사에 넘기는데 `_with_text` 는 첫 파트만
    교체하고 나머지를 원문 그대로 남겼다. §1 이 "외부로 아무것도 내보내지 않는다" 를
    이 프로젝트의 유일한 이유라고 선언했고, 프록시는 그 성질이 상류를 부를 때도
    유지되는지를 책임지는 자리다.
    """
    content = [
        {"type": "text", "text": "안녕하세요"},
        {"type": "text", "text": f"메일은 {_LEAKY} 입니다"},
    ]
    joined = _text_of(content)
    result = Inspector("config/policy.yaml").check(joined)
    forwarded = _with_text(content, result.masked_text or joined)
    assert _LEAKY not in json.dumps(forwarded, ensure_ascii=False)


def test_proxy_keeps_non_text_parts() -> None:
    """이미지 파트는 검사 대상이 아니었으므로 손댈 이유가 없다."""
    content = [
        {"type": "text", "text": f"메일 {_LEAKY}"},
        {"type": "image_url", "image_url": {"url": "https://example.test/a.png"}},
    ]
    forwarded = _with_text(content, "메일 <EMAIL_1>")
    assert any(part.get("type") == "image_url" for part in forwarded)
    assert _LEAKY not in json.dumps(forwarded, ensure_ascii=False)


def test_export_metadata_width_follows_the_runtime_contract(tmp_path: Path) -> None:
    """★ [높음] 내보내기가 8차원을 적고 모델은 7차원을 받아 검증이 ValueError 를 냈다.

    런타임 어댑터가 이 파일을 읽어 입력을 만든다 — 학습·내보내기·추론이 서로 다른 폭을
    믿으면 조용히 어긋나는 것이 아니라 터진다. 터지는 편이 낫지만, 애초에 폭을 두 군데
    적어 둔 것이 원인이다.
    """
    from garim.training.export import write_l1_metadata

    payload = json.loads(write_l1_metadata(tmp_path).read_text(encoding="utf-8"))
    assert payload["normalization_delta_order"] == list(NORMALIZATION_DELTA_ORDER)


def test_overlap_merge_follows_the_active_policy(tmp_path: Path) -> None:
    """★ [높음] 사용자가 block 이라고 적은 코드가 병합에서 사라져 allow 가 나왔다.

    겹침 해소가 `DEFAULT_CODE_MODES`(하드코딩 기본값)를 봤다. 사용자가 심각도를
    뒤집으면 기본값 기준으로 이긴 코드가 남고, 사용자가 무겁게 본 코드가 폐기된다 —
    정책 엔진이 있는 이유를 부정하는 fail-open 이다.
    """
    source = Path("config/policy.yaml").read_text(encoding="utf-8")
    flipped = tmp_path / "policy.yaml"
    flipped.write_text(
        source.replace("  L-KR-01: block", "  L-KR-01: allow\n  L-CM-03: block"),
        encoding="utf-8",
    )
    text = "주민등록번호 900101-1234567 hong@example.test"
    codes = {finding.code for finding in Inspector(str(flipped)).check(text).leaks}
    assert "L-CM-03" in codes, "사용자가 block 으로 지정한 코드가 살아남아야 한다"


def test_scan_refuses_to_report_success_when_it_inspected_nothing(tmp_path: Path) -> None:
    """★ [중간] 읽지 못한 파일을 조용히 건너뛰고 0 을 냈다 — CI 게이트가 fail-open."""
    from garim.cli import main

    (tmp_path / "a.txt").write_bytes(b"\xff\xfe\x00\x01binary")
    (tmp_path / "b.txt").write_bytes(b"\xff\xfe\x00\x02more")
    assert main(["scan", str(tmp_path)]) == 4


def test_segment_cap_records_why_it_degraded() -> None:
    """★ [낮음] `degraded=True` 만 세우고 사유를 안 적어 감사 로그가 말을 못 했다."""
    inspector = Inspector("config/policy.yaml")
    cap = inspector.policy.config.limits.max_segments
    segments = [Segment(text=f"문장 {index}", origin="user") for index in range(cap + 5)]
    result = inspector.check(segments)
    assert result.degraded
    assert "segment_cap_exceeded" in result.degraded_reasons


@pytest.mark.parametrize("code", ["L-KR-01", "L-CM-03"])
def test_severity_resolver_is_required_not_defaulted(code: str) -> None:
    """기본값을 두면 넘기는 것을 잊은 호출부가 조용히 옛 동작으로 돌아간다.

    실제로 `api.py` 의 청크 병합이 세 번째 호출부였고, 필수 인자로 만든 덕에 드러났다.
    """
    from garim.detect.merge import merge_overlaps

    with pytest.raises(TypeError, match="mode_for"):
        merge_overlaps([])  # type: ignore[call-arg]
    assert code


def test_scan_reports_degraded_when_only_some_files_were_inspected(tmp_path: Path) -> None:
    """★ 일부만 읽으면 0 이 나왔다 — CP949 파일 하나가 검사되지 않아도 CI 가 통과했다."""
    from garim.cli import main

    (tmp_path / "a.txt").write_bytes("주민등록번호 900101-1234561".encode("cp949"))
    (tmp_path / "b.txt").write_text("안녕하세요", encoding="utf-8")
    assert main(["scan", str(tmp_path)]) == 3


def test_invalid_custom_regex_fails_at_startup_with_exit_4(tmp_path: Path) -> None:
    """★ `re.error` 가 트레이스백과 종료 코드 1(= warn)로 나왔다."""
    from garim.cli import main

    policy = tmp_path / "policy.yaml"
    policy.write_text(
        "version: 1\ncustom_patterns:\n  - name: broken\n    regex: '('\n", encoding="utf-8"
    )
    assert main(["--policy", str(policy), "check", "안녕하세요"]) == 4


def test_directory_scan_includes_dotenv_but_not_hidden_directories(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """★ 숨은 파일 필터가 `.env` 를 늘 건너뛰었다."""
    from garim.cli import main

    secret = "OPENAI_API_KEY=sk-proj-" + "A" * 40 + "\n"
    (tmp_path / ".env").write_text(secret, encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config.txt").write_text(secret, encoding="utf-8")
    assert main(["scan", str(tmp_path)]) == 2
    output = capsys.readouterr().out
    assert ".env: block" in output
    assert ".git" not in output
