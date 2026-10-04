"""The field-type vocabulary: what a custom field may be, and how a value of
each type is checked and normalised before it is stored.

A value that fails raises FieldError phrased for the model. An empty value
(None, "", an empty list) normalises to None, which callers treat as "clear".
"""

import re
from datetime import date
from urllib.parse import urlparse

TYPES = ("text", "url", "number", "money", "date", "rating", "choice", "tags")

# Core item columns: a custom field by one of these names would shadow them.
RESERVED = frozenset({
    "id", "item_id", "collection_id", "title", "notes", "section", "status",
    "position", "fields", "extra", "created_at", "updated_at",
})

DEFAULT_CURRENCY = "ILS"
_CURRENCY_SYMBOLS = {"₪": "ILS", "$": "USD", "€": "EUR", "£": "GBP"}
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


class FieldError(ValueError):
    pass


def _empty(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == []


def check_name(name) -> str:
    n = str(name or "").strip().lower()
    if not _NAME_RE.match(n):
        raise FieldError(
            f"Field name {name!r} is invalid: use snake_case, starting with a letter "
            "(e.g. 'price', 'weight_kg')."
        )
    if n in RESERVED:
        raise FieldError(f"{n!r} is a built-in item attribute, not a custom field name.")
    return n


def check_defs(defs) -> list[dict]:
    """Normalise a list of field definitions [{name, type, options?}]."""
    out: list[dict] = []
    seen: set[str] = set()
    for d in defs or []:
        if not isinstance(d, dict):
            raise FieldError(f"A field definition is {{name, type, options?}}, got {d!r}.")
        unknown = set(d) - {"name", "type", "options"}
        if unknown:
            raise FieldError(f"Field definition has unknown key(s) {sorted(unknown)}; "
                             "use name, type, options.")
        name = check_name(d.get("name"))
        if name in seen:
            raise FieldError(f"Field {name!r} is defined twice.")
        seen.add(name)
        out.append(check_def(name, d.get("type"), d.get("options")))
    return out


def check_def(name: str, ftype, options=None) -> dict:
    t = str(ftype or "").strip().lower()
    if t not in TYPES:
        raise FieldError(f"Field {name!r}: unknown type {ftype!r}. Types: {', '.join(TYPES)}.")
    if t == "choice":
        opts = [str(o).strip() for o in (options or []) if str(o).strip()]
        if len(opts) < 2:
            raise FieldError(f"Field {name!r}: a choice field needs at least two options.")
        if len({o.lower() for o in opts}) != len(opts):
            raise FieldError(f"Field {name!r}: choice options repeat.")
        return {"name": name, "type": t, "options": opts}
    if options:
        raise FieldError(f"Field {name!r}: only a choice field takes options.")
    return {"name": name, "type": t}


def describe(field: dict) -> str:
    if field["type"] == "choice":
        return f"{field['name']} (choice: {' / '.join(field['options'])})"
    return f"{field['name']} ({field['type']})"


def normalize(field: dict, value):
    """The stored form of `value` for this field, or None to clear."""
    if _empty(value):
        return None
    t, name = field["type"], field["name"]
    try:
        return _NORMALIZERS[t](field, value)
    except FieldError:
        raise
    except (TypeError, ValueError):
        raise FieldError(f"{name} ({t}): can't use {value!r}. {_HINTS[t]}")


def _text(field, value):
    if isinstance(value, (dict, list)):
        raise ValueError
    return str(value).strip()


def _url(field, value):
    s = str(value).strip()
    p = urlparse(s)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ValueError
    return s


def _number(field, value):
    if isinstance(value, bool):
        raise ValueError
    if isinstance(value, (int, float)):
        n = value
    else:
        n = float(str(value).strip().replace(",", ""))
    return int(n) if float(n).is_integer() else float(n)


def _money(field, value):
    if isinstance(value, dict):
        unknown = set(value) - {"amount", "currency"}
        if unknown or "amount" not in value:
            raise ValueError
        amount = _number(field, value["amount"])
        currency = str(value.get("currency") or DEFAULT_CURRENCY).strip().upper()
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        amount, currency = _number(field, value), DEFAULT_CURRENCY
    else:
        s = str(value).strip()
        currency = DEFAULT_CURRENCY
        for sym, code in _CURRENCY_SYMBOLS.items():
            if sym in s:
                s, currency = s.replace(sym, ""), code
        m = re.fullmatch(r"([A-Za-z]{3})?\s*([\d.,]+)\s*([A-Za-z]{3})?", s.strip())
        if not m or (m.group(1) and m.group(3)):
            raise ValueError
        currency = (m.group(1) or m.group(3) or currency).upper()
        amount = _number(field, m.group(2))
    if not re.fullmatch(r"[A-Z]{3}", currency) or amount < 0:
        raise ValueError
    return {"amount": amount, "currency": currency}


def _date(field, value):
    return date.fromisoformat(str(value).strip()).isoformat()


def _rating(field, value):
    n = _number(field, value)
    if not isinstance(n, int) or not 1 <= n <= 5:
        raise ValueError
    return n


def _choice(field, value):
    s = str(value).strip().lower()
    for opt in field["options"]:
        if opt.lower() == s:
            return opt
    raise FieldError(f"{field['name']}: {value!r} is not an option. "
                     f"Options: {', '.join(field['options'])}.")


def _tags(field, value):
    raw = value.split(",") if isinstance(value, str) else value
    if not isinstance(raw, list):
        raise ValueError
    out: list[str] = []
    for t in raw:
        if isinstance(t, (dict, list)):
            raise ValueError
        t = str(t).strip().lower()
        if t and t not in out:
            out.append(t)
    return out or None


_NORMALIZERS = {
    "text": _text, "url": _url, "number": _number, "money": _money,
    "date": _date, "rating": _rating, "choice": _choice, "tags": _tags,
}

_HINTS = {
    "text": "Give a string.",
    "url": "Give a full http(s) URL.",
    "number": "Give a number; units belong in the field name.",
    "money": "Give an amount, optionally with a currency: 3400, '3400 USD', '$12', "
             "or {amount, currency}.",
    "date": "Give YYYY-MM-DD.",
    "rating": "Give a whole number from 1 to 5.",
    "choice": "Give one of the options.",
    "tags": "Give a list of strings (or one comma-separated string).",
}


def render(field: dict, value) -> str:
    """A stored value as one short line of text."""
    t = field["type"]
    if t == "money":
        amount = value["amount"]
        num = f"{amount:,}" if isinstance(amount, int) else f"{amount:,.2f}"
        return f"{num} {value['currency']}"
    if t == "rating":
        return f"{value}/5"
    if t == "tags":
        return ", ".join(value)
    return str(value)
