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

import turn_context
from gateway import outbox as outbox_mod
from gateway.blocks import Form, FormRow, NumberField, TextField
from gateway.outbox import EVENT_HEARTBEAT
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
    """Send a small prefilled form card submitted in one tap.

    Use ONLY to let the user confirm/correct prefilled guesses from real evidence
    (logged history, stated plan):
    - Open questions or a single value -> ask in prose, never a form.
    - No evidence for a field -> leave `default` empty; never invent a value.
    - Do not announce the form separately; `message_text` accompanies the card.

    Submissions arrive as `[Submitted form <id>]` naming each `field_id` — handle
    them then (e.g. log with the right tool). `(left empty)` means explicit "no value".

    Args:
        message_text: Sentence shown with the card (required).
        slug: Short kebab-case name (e.g. 'push-day').
        title: Card heading (e.g. 'Push day').
        summary: One-line fallback for notification previews.
        rows: Up to 6 rows: `[{"label": str, "fields": [{"field_id": snake_case,
            "type": "text"|"number", "unit": str (required if 2+ fields in row),
            "default": typed prefill}]}]`.
        subtitle: Optional subheading.
        submit_label: Optional button label (default 'Submit').
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

    # A heartbeat-sent card and its submission live on different threads; the
    # event tag routes the card's text through the pending-mirror drain so the
    # submission's turn has its antecedent. User-turn sends stay untagged —
    # send and submit already share the thread.
    background = turn_context.current_scope() == "heartbeat"
    event = EVENT_HEARTBEAT if background else None
    try:
        outcome = outbox_mod.submit(
            origin_outbox().send_block_to_owner(message_text, form, event=event)
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
    sent = (
        f"Sent form {form.callback_id} ({title}): {form.describe()}. "
        f"The user's submission will arrive as a new message."
    )
    if background:
        # The ack's notification is a second, separate send.
        sent += (
            " Its message_text has already reached the user — don't repeat it in "
            "heartbeat_respond's notification_text."
        )
    return sent
