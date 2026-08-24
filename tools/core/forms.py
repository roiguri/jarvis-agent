"""The send_form tool — put a small prefilled form in front of the owner.

A thin caller over the gateway's block seam: build a neutral Form from the
model's arguments (all validation lives in the Form itself), pre-flight the
origin channel's capability, send through that channel's outbox. The tool
returns a description of what was asked — the thread is the only store a form
has, so the return string is what later makes the submission intelligible.
"""

import concurrent.futures
import logging

from langchain_core.tools import tool

from gateway import outbox as outbox_mod
from gateway.blocks import Form, FormRow, NumberField, TextField
from tools.registry import tool_register

logger = logging.getLogger(__name__)

_SEND_TIMEOUT_S = 20.0


_ROW_KEYS = {"label", "fields"}
_FIELD_KEYS = {"field_id", "type", "unit", "default"}


def _parse_rows(rows: list) -> list[FormRow]:
    """Model-supplied dicts -> block dataclasses. Raises ValueError with a
    correctable message; the Form's own validation runs after. Unknown keys are
    refused rather than dropped — a stray "value" for "default" would otherwise
    silently ship a box without its intended prefill."""
    parsed: list[FormRow] = []
    for row in rows or []:
        unknown = set(row) - _ROW_KEYS
        if unknown:
            raise ValueError(
                f"row {row.get('label')!r}: unknown key(s) {sorted(unknown)} — "
                f"a row has exactly {sorted(_ROW_KEYS)}"
            )
        fields = []
        for f in row.get("fields") or []:
            unknown = set(f) - _FIELD_KEYS
            if unknown:
                raise ValueError(
                    f"field {f.get('field_id')!r}: unknown key(s) {sorted(unknown)} — "
                    f"a field has {sorted(_FIELD_KEYS)}"
                )
            ftype = f.get("type", "text")
            kwargs = {
                "field_id": f.get("field_id", ""),
                "default": f.get("default"),
                "unit": f.get("unit"),
            }
            if ftype == "number":
                fields.append(NumberField(**kwargs))
            elif ftype == "text":
                fields.append(TextField(**kwargs))
            else:
                raise ValueError(
                    f"field {f.get('field_id')!r}: type must be 'text' or 'number', "
                    f"got {ftype!r}"
                )
        parsed.append(FormRow(label=row.get("label", ""), fields=tuple(fields)))
    return parsed


@tool_register(namespace="core")
@tool
def send_form(
    message_text: str,
    slug: str,
    title: str,
    summary: str,
    rows: list[dict],
    subtitle: str = "",
    submit_label: str = "",
) -> str:
    """Send the user a small form — labelled boxes, prefilled with your best guess,
    submitted in one tap.

    ONLY use a form when you can prefill every box from real data (their logged
    history, their stated plan) and expect the user to submit it unchanged or with
    small corrections. A form is for CORRECTING guesses, not for collecting answers:
    - asking open questions -> just ask in conversation, never a form
    - one single value -> just ask in conversation
    - anything you cannot prefill from evidence -> leave that box empty (no default);
      NEVER invent a plausible-looking default, since unchanged submission records it
      as fact
    Do not announce the form separately; message_text accompanies the card.

    After the user submits, the values arrive in this conversation as a
    "[Submitted form <id>]" message naming each field_id — handle it then (e.g. log
    it with the right tool). A value of "(left empty)" means the user saw the box
    and left it blank: respect that as an explicit "no value", don't substitute one.

    Args:
        message_text: The sentence shown with the card (e.g. "Push day — fill in
            what you hit."). Required; without it the form opens cold.
        slug: Short kebab-case name for this form (e.g. 'push-day'). A unique id
            is derived from it.
        title: Card heading (e.g. 'Push day').
        summary: One-line stand-in shown where the card doesn't render
            (notification preview).
        rows: Up to 6 rows. Each: {"label": str, "fields": [{"field_id":
            snake_case str, "type": "text"|"number", "unit": str (required when a
            row has 2+ fields), "default": prefill matching the type}]}.
            Example: [{"label": "Bench press", "fields": [
                {"field_id": "bench_reps", "type": "number", "unit": "reps", "default": 8},
                {"field_id": "bench_kg", "type": "number", "unit": "kg", "default": 60}]}]
        subtitle: Optional line under the title (e.g. '4 exercises').
        submit_label: Optional label for the submit button (default 'Submit').
    """
    from gateway.factory import origin_channel, origin_outbox

    try:
        form = Form(
            summary=summary,
            slug=slug,
            title=title,
            rows=tuple(_parse_rows(rows)),
            subtitle=subtitle or None,
            submit_label=submit_label or None,
        )
    except (ValueError, TypeError) as e:
        return f"Error: invalid form — {e}"

    channel = origin_channel()
    if not channel.supports_block("form"):
        # No fallback is sent for the caller: prose is the model's own output,
        # not a tool's. Echo the prepared guesses so the recovery is a rewrite.
        return (
            f"Forms aren't available on {channel.name} — nothing was sent. "
            f"Say it conversationally instead. You had prepared: {form.describe()}"
        )

    try:
        outcome = outbox_mod.submit(
            origin_outbox().send_block_to_owner(message_text, form)
        ).result(timeout=_SEND_TIMEOUT_S)
    except concurrent.futures.TimeoutError:
        # The send is still running on the loop — expiry means unknown, not
        # failed, so the directive must prevent a duplicate card.
        return (
            "Error: the form send was not confirmed within "
            f"{_SEND_TIMEOUT_S:.0f}s — it may still reach the user. Do not "
            "send it again; ask the user whether the card arrived."
        )
    if not outcome.ok:
        return f"Error: form could not be sent — {outcome.error}"
    return (
        f"Sent form {form.callback_id} ({title}): {form.describe()}. "
        f"The user's submission will arrive as a new message."
    )
