"""Best-effort secret redaction for exported transcripts.

Read docs/README before relying on this. In short: it catches the common,
well-shaped credential formats and obvious ``password = ...`` assignments. It
is a safety net, not a guarantee. Anything unusual -- a bare password with no
label, a private key pasted as prose, a customer name -- will pass straight
through. Review an export before you share it.

Everything is a plain ``re`` pattern so a maintainer can read, test and extend
the list without learning a framework.
"""

from __future__ import annotations

import re
from typing import List, Pattern, Tuple

PLACEHOLDER = "[REDACTED:{0}]"

#: Values that look secret-shaped but are obviously not real.
_OBVIOUS_PLACEHOLDERS = re.compile(
    r"^(?:x{3,}|\*{3,}|\.{3,}"
    r"|(?:your|my|our|the|some|a)[-_ ][\w\-]*"           # your-password-here
    r"|[\w\-]*(?:here|placeholder|example|goes[-_ ]here)"  # secret-goes-here
    r"|<[^>]+>|\$\{?\w+\}?|\{\{[^}]+\}\}"                 # <token>, ${VAR}, {{var}}
    r"|none|null|true|false|undefined|todo|tbd"
    r"|changeme|change[-_]me|example|placeholder|redacted"
    r"|os\.getenv.*|process\.env.*|System\.getenv.*)$",
    re.IGNORECASE,
)


def _looks_like_a_real_secret(value: str) -> bool:
    """Filter out placeholders so prose and templates survive intact."""
    value = value.strip().strip("\"'")
    if len(value) < 8:
        return False
    if _OBVIOUS_PLACEHOLDERS.match(value):
        return False
    has_alpha = any(c.isalpha() for c in value)
    has_other = any(c.isdigit() or c in "-_+/=." for c in value)
    return has_alpha and has_other


# (name, pattern, group-to-redact). Group 0 means "the whole match".
_RULES: List[Tuple[str, Pattern, int]] = [
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}"), 0),
    ("openai-key", re.compile(r"\bsk-(?!ant-)[A-Za-z0-9]{20,}"), 0),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), 0),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), 0),
    ("google-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), 0),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), 0),
    ("groq-key", re.compile(r"\bgsk_[A-Za-z0-9]{20,}"), 0),
    ("stripe-key", re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}"), 0),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), 0),
    ("private-key-block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL), 0),
    ("bearer-token", re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._\-]{20,})"), 1),
    ("basic-auth-url", re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^\s:/@]+:)([^\s@]{4,})@"), 2),
    (
        "assigned-credential",
        re.compile(
            r"(?i)\b((?:password|passwd|pwd|secret|api[_\- ]?key|access[_\- ]?token|"
            r"auth[_\- ]?token|client[_\- ]?secret|private[_\- ]?key)\s*[:=]\s*)"
            r"[\"']?([^\s\"',;]{8,})[\"']?"
        ),
        2,
    ),
]


def redact(text: str) -> Tuple[str, int]:
    """Return ``(redacted_text, replacement_count)``.

    Patterns with a specific group keep the label and only mask the value, so
    the export still reads sensibly: ``password = [REDACTED:assigned-credential]``.
    """
    if not text:
        return text, 0

    count = 0

    def make_sub(name: str, group: int):
        def _sub(match):
            nonlocal count
            value = match.group(group) if group else match.group(0)
            if group and not _looks_like_a_real_secret(value):
                return match.group(0)
            count += 1
            masked = PLACEHOLDER.format(name)
            if group == 0:
                return masked
            return match.group(0).replace(value, masked, 1)

        return _sub

    for name, pattern, group in _RULES:
        text = pattern.sub(make_sub(name, group), text)
    return text, count
