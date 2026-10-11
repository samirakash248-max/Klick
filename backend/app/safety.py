"""Safety guardrails and prompt-injection detection module.

Responsible for:
1. Identifying potentially destructive browser actions (e.g. database purge, account deletion).
2. Detecting adversarial prompt injections embedded in web page content or DOM attributes.
3. Enforcing human-in-the-loop confirmation policies for critical operations.

Owned by: QA & Security Engineer
"""

import re
from typing import List, Optional
from .schemas import (
    ActionType,
    BrowserAction,
    PageObservation,
    RiskLevel,
    SafetyCheckResult,
)


class SafetyGuard:
    """Evaluates proposed actions and page content against safety policies."""

    # Keywords that suggest destructive, irrecoverable, or high-consequence operations
    DESTRUCTIVE_KEYWORDS = [
        "delete",
        "purge",
        "drop",
        "destroy",
        "wipe",
        "truncate",
        "format",
        "terminate",
        "reset database",
        "remove all",
        "clear all data",
        "revoke",
        "transfer funds",
    ]

    # Regex patterns typical of adversarial prompt injection attempts in untrusted web content
    INJECTION_PATTERNS = [
        r"(?i)ignore\s+(all\s+)?(previous|prior|above)\s+instructions",
        r"(?i)disregard\s+(all\s+)?(previous|prior)\s+instructions",
        r"(?i)system\s+prompt\s+override",
        r"(?i)you\s+are\s+now\s+(in\s+)?(developer|admin|debug|dan)\s+mode",
        r"(?i)new\s+system\s+directive",
        r"(?i)attention\s+ai\s+agent",
        r"(?i)important\s+instruction\s+for\s+assistant",
        r"(?i)exfiltrate|leak\s+(secret|password|token|key|credential)",
    ]

    def __init__(self, strict_mode: bool = True, block_destructive: bool = True) -> None:
        self.strict_mode = strict_mode
        self.block_destructive = block_destructive
        self._compiled_injections = [re.compile(p) for p in self.INJECTION_PATTERNS]

    def inspect_observation(self, observation: PageObservation) -> List[SafetyCheckResult]:
        """Scan the page text, title, and interactive element contents for adversarial prompt injections."""
        findings: List[SafetyCheckResult] = []

        # Texts to check: page snippet, DOM summary, interactive elements text
        texts_to_check = [
            ("Page Text", observation.page_text_snippet or ""),
            ("DOM Summary", observation.dom_summary or ""),
            ("Page Title", observation.title or ""),
        ]

        for elem in observation.interactive_elements:
            if elem.text:
                texts_to_check.append((f"Element text ({elem.selector})", elem.text))
            if elem.aria_label:
                texts_to_check.append((f"ARIA label ({elem.selector})", elem.aria_label))

        for source_name, text in texts_to_check:
            for pattern in self._compiled_injections:
                match = pattern.search(text)
                if match:
                    findings.append(
                        SafetyCheckResult(
                            is_safe=False,
                            risk_level=RiskLevel.HIGH,
                            reason=f"Suspected prompt injection detected in {source_name}: '{match.group(0)}'",
                            flagged_pattern=match.group(0),
                            requires_human_confirmation=True,
                        )
                    )

        # TODO (QA & Security Engineer): Add semantic classification using lightweight embedding distance
        # TODO (QA & Security Engineer): Add heuristics for CSS-hidden injection vectors (display:none, opacity:0)
        return findings

    def evaluate_action(
        self, action: BrowserAction, observation: Optional[PageObservation] = None
    ) -> SafetyCheckResult:
        """Evaluate a proposed action for destructive impact or policy violations."""
        # 1. Non-destructive actions are generally safe
        if action.action_type in (ActionType.NAVIGATE, ActionType.WAIT, ActionType.SCROLL, ActionType.EXTRACT):
            # Check target URL for restricted protocols
            if action.url:
                clean_url = action.url.lower().replace("\\", "/")
                is_local_mock = "mock-site" in clean_url or "localhost" in clean_url or "127.0.0.1" in clean_url
                if (action.url.startswith("file://") and not is_local_mock) or action.url.startswith("javascript:") or action.url.startswith("data:"):
                    return SafetyCheckResult(
                        is_safe=False,
                        risk_level=RiskLevel.HIGH,
                        reason=f"Restricted URL scheme detected in navigation target: {action.url}",
                        flagged_pattern=action.url,
                        requires_human_confirmation=True,
                    )
            return SafetyCheckResult(is_safe=True, risk_level=RiskLevel.LOW)

        # 2. Check for destructive action keywords in selector, text, or target description
        action_text_corpus = " ".join(
            filter(
                None,
                [
                    action.selector,
                    action.text,
                    action.description,
                    action.target_element_description,
                ],
            )
        ).lower()

        for kw in self.DESTRUCTIVE_KEYWORDS:
            if kw in action_text_corpus:
                reason = f"Action targets a potentially destructive operation matching keyword '{kw}'"
                return SafetyCheckResult(
                    is_safe=not self.block_destructive,
                    risk_level=RiskLevel.CRITICAL if self.block_destructive else RiskLevel.HIGH,
                    reason=reason,
                    flagged_pattern=kw,
                    requires_human_confirmation=True,
                )

        # 3. Check corresponding element in observation if available
        if observation and action.selector:
            for elem in observation.interactive_elements:
                if elem.selector == action.selector:
                    elem_content = f"{elem.text or ''} {elem.aria_label or ''}".lower()
                    for kw in self.DESTRUCTIVE_KEYWORDS:
                        if kw in elem_content:
                            return SafetyCheckResult(
                                is_safe=not self.block_destructive,
                                risk_level=RiskLevel.CRITICAL if self.block_destructive else RiskLevel.HIGH,
                                reason=f"Target element ({elem.selector}) contains destructive label '{kw}'",
                                flagged_pattern=kw,
                                requires_human_confirmation=True,
                            )

        # TODO (QA & Security Engineer): Integrate allowlists for authorized destructive test environments
        # TODO (QA & Security Engineer): Support dynamic per-domain permission models

        return SafetyCheckResult(
            is_safe=True,
            risk_level=RiskLevel.LOW,
            reason="Action passed basic safety guardrail checks",
            requires_human_confirmation=False,
        )


# Global default instance
safety_guard = SafetyGuard()
