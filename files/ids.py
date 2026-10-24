"""File ids: `file:` + 8 random Crockford base32 characters.

Random, not sequential: a mistyped id almost always lands on nothing instead of on
another real file. Uniqueness is the index's job (a unique key; a clash draws
again), so the length only has to keep clashes rare.
"""

import re
import secrets

PREFIX = "file:"
_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"  # Crockford: no i, l, o, u
_LENGTH = 8

# Exactly 8 Crockford characters with word boundaries, so ordinary text
# (`file:///`, `file:readme.txt`) never matches. Hits are still confirmed
# against the index by whoever scans.
ID_RE = re.compile(r"(?<![\w:])file:([0-9a-hjkmnp-tv-z]{8})(?![\w])", re.IGNORECASE)


def new_id() -> str:
    return PREFIX + "".join(secrets.choice(_ALPHABET) for _ in range(_LENGTH))


def normalize(raw: str) -> str | None:
    """A typed id in canonical form, or None if it can't be one. Case-insensitive;
    I and L read as 1, O as 0 (the Crockford rule), so a misread id still resolves."""
    s = (raw or "").strip().lower()
    if s.startswith(PREFIX):
        s = s[len(PREFIX):]
    s = s.translate(str.maketrans({"i": "1", "l": "1", "o": "0"}))
    if len(s) != _LENGTH or any(c not in _ALPHABET for c in s):
        return None
    return PREFIX + s


def find_ids(text: str) -> set[str]:
    """Every id-shaped token in `text`, canonical. Not checked against the index."""
    return {PREFIX + m.group(1).lower() for m in ID_RE.finditer(text or "")}
