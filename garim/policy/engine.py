"""Policy decisions are separate from matching so deployments can tune behavior safely."""

from __future__ import annotations

import re

from garim.models import Finding, Verdict
from garim.policy.schema import Mode, PolicyConfig

SEVERITY = {"allow": 0, "mask": 1, "warn": 2, "block": 3}

DEFAULT_CODE_MODES: dict[str, Mode] = {
    "L-KR-01": "block",
    "L-KR-02": "block",
    "L-KR-03": "mask",
    "L-KR-04": "mask",
    "L-KR-05": "mask",
    "L-KR-06": "mask",
    "L-KR-07": "mask",
    "L-KR-08": "mask",
    "L-CM-01": "mask",
    "L-CM-02": "mask",
    "L-CM-03": "mask",
    "L-CM-04": "mask",
    "L-CT-01": "mask",
    "L-CT-02": "mask",
    "L-CT-03": "mask",
    "L-SEC-01": "block",
    "L-SEC-02": "block",
    "L-SEC-03": "block",
    "L-SEC-04": "block",
    "L-SEC-05": "warn",
    "L-DOC-01": "warn",
}

MASK_LABELS = {
    "L-KR-01": "KR_RRN",
    "L-KR-02": "KR_FRN",
    "L-KR-03": "KR_PASSPORT",
    "L-KR-04": "KR_DL",
    "L-KR-05": "KR_BIZNO",
    "L-KR-06": "KR_CORPNO",
    "L-KR-07": "KR_NHI",
    "L-KR-08": "KR_ACCOUNT",
    "L-CM-01": "CARD",
    "L-CM-02": "PHONE",
    "L-CM-03": "EMAIL",
    "L-CM-04": "NET_ADDR",
    "L-CT-01": "PERSON",
    "L-CT-02": "ADDRESS",
    "L-CT-03": "ORG_ID",
}


class PolicyEngine:
    def __init__(self, config: PolicyConfig) -> None:
        self.config = config
        self._custom_modes = {pattern.code: pattern.mode for pattern in config.custom_patterns}
        self.custom_patterns = [
            (pattern, re.compile(pattern.regex)) for pattern in config.custom_patterns
        ]

    def mode_for(self, code: str) -> Mode:
        if code in self.config.codes:
            return self.config.codes[code]
        if code in self._custom_modes:
            return self._custom_modes[code]
        if code.startswith("A-"):
            return "warn"
        return DEFAULT_CODE_MODES.get(code, self.config.defaults.verdict)

    def threshold_for(self, finding: Finding, content_type: str) -> float:
        if finding.layer == "L1":
            base = (
                self.config.thresholds.l1_atk
                if finding.is_attack
                else self.config.thresholds.l1_pii
            )
        else:
            base = self.config.thresholds.l0
        policy = self.config.content_type_policy.get(content_type)
        if policy is not None:
            if finding.is_attack:
                base += policy.attack_threshold_delta
            elif finding.code.startswith("L-SEC-"):
                base += policy.secret_threshold_delta
            else:
                base += policy.pii_threshold_delta
        return min(1.0, max(0.0, base))

    def accepts(self, finding: Finding, content_type: str) -> bool:
        policy = self.config.content_type_policy.get(content_type)
        if policy is not None and finding.code in policy.suppress:
            return False
        return finding.confidence >= self.threshold_for(finding, content_type)

    def apply_origin_policy(
        self, findings: list[Finding], origins: dict[int, str]
    ) -> list[Finding]:
        """origin 축 강등 — DESIGN §2.5b.

        ★ `trust_declared=False`(기본)면 아무것도 하지 않는다. `origin` 은 자기신고
        필드라, 신뢰 게이트를 기본값으로 켜면 `origin="system"` 선언만으로 공격
        탐지가 꺼진다. 초판이 탐지기 안에서 그렇게 하고 있었다.

        켜져 있으면 system 세그먼트의 **공격 축만** 강등한다 — 유출 축은 건드리지
        않는다. 시스템 프롬프트에 하드코딩된 API 키가 시크릿 유출의 대표 사례이므로
        오히려 강화해야 할 자리다.
        """
        policy = self.config.origin_policy
        if not policy.trust_declared:
            return findings
        kept: list[Finding] = []
        for finding in findings:
            if (
                finding.is_attack
                and origins.get(finding.segment_idx) == "system"
                and finding.code in policy.system_suppress_codes
            ):
                continue
            kept.append(finding)
        return kept

    def origin_verdict_cap(
        self, findings: list[Finding], origins: dict[int, str]
    ) -> Verdict | None:
        """system 세그먼트의 공격 판정 상한. trust_declared 일 때만."""
        policy = self.config.origin_policy
        if not policy.trust_declared:
            return None
        attack_segments = {f.segment_idx for f in findings if f.is_attack}
        if attack_segments and all(origins.get(idx) == "system" for idx in attack_segments):
            return policy.system_attack_verdict_cap
        return None

    def aggregate(self, findings: list[Finding]) -> Verdict:
        if not findings:
            return "allow"
        return max((self.mode_for(finding.code) for finding in findings), key=SEVERITY.__getitem__)

    def aggregate_with_origin(self, findings: list[Finding], origins: dict[int, str]) -> Verdict:
        """판정 합산. origin 상한은 **공격 축에만** 건다 — §2.5b.

        ★ 회귀 — 초판은 `aggregate(findings)` 전체 결과에 상한을 걸었다.
        `origin_verdict_cap` 은 공격 finding 의 세그먼트만 보고 상한을 정하는데,
        그 상한이 유출 축까지 함께 내렸다. 실행으로 확인한 결과:

            system 세그먼트에 A-LEAK-01, user 세그먼트에 주민등록번호(L-KR-01, block)
            → trust_declared=False: block / trust_declared=True: **warn**

        프록시가 `trust_declared` 를 켜는 순간, 시스템 프롬프트에 공격 문구 한 줄만
        있으면 사용자 메시지의 주민등록번호·API 키 block 이 전부 warn 으로 내려갔다.
        §2.5b 가 "system 을 검사에서 빼는 것은 틀린 처방" 이라며 막으려던 우회를
        다른 경로로 열어 준 셈이다.

        두 축을 따로 합산하고 상한을 공격 쪽에만 먹인 뒤 다시 최댓값을 취한다.
        """
        leak_verdict = self.aggregate([f for f in findings if not f.is_attack])
        attack_verdict = self.aggregate([f for f in findings if f.is_attack])
        cap = self.origin_verdict_cap(findings, origins)
        if cap is not None and SEVERITY[attack_verdict] > SEVERITY[cap]:
            attack_verdict = cap
        return max((leak_verdict, attack_verdict), key=SEVERITY.__getitem__)

    def mask_label(self, code: str) -> str:
        return MASK_LABELS.get(code, "SENSITIVE")
