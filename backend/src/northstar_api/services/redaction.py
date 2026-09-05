from __future__ import annotations

import re

_EMAIL = re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.I)
_PROVIDER_KEY = re.compile(r"\b(?:nvapi|sk|ghp|github_pat)-[A-Za-z0-9_-]{12,}\b", re.I)
_CREDENTIAL = re.compile(
    r"\b(password|passwd|pwd|secret|api[ _-]?key)(\s*(?:[:=]|\bis\b)\s*)\S+",
    re.I,
)
_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")


def _luhn_valid(digits: str) -> bool:
    checksum = 0
    parity = len(digits) % 2
    for index, character in enumerate(digits):
        value = int(character)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        checksum += value
    return checksum % 10 == 0


def mask_sensitive_text(value: str) -> str:
    """Mask common credentials and direct identifiers before model/storage use."""

    redacted = _PROVIDER_KEY.sub("[REDACTED_API_KEY]", value)
    redacted = _EMAIL.sub("[REDACTED_EMAIL]", redacted)
    redacted = _CREDENTIAL.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", redacted)

    def replace_card(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        return (
            "[REDACTED_PAYMENT_CARD]" if 13 <= len(digits) <= 19 and _luhn_valid(digits) else match.group(0)
        )

    return _CARD_CANDIDATE.sub(replace_card, redacted)
