"""Neutral interactive-block layer — see base.py for the contracts and one
module per kind beside it (form.py). Channel adapters map these to their own
wire; tools and domain code import only from here."""

from gateway.blocks.base import Block, BlockAction, Interactive
from gateway.blocks.form import (
    FORM_STATES,
    MAX_ROWS,
    Form,
    FormRow,
    NumberField,
    TextField,
    render_submission,
)

__all__ = [
    "Block",
    "BlockAction",
    "Interactive",
    "Form",
    "FormRow",
    "TextField",
    "NumberField",
    "FORM_STATES",
    "MAX_ROWS",
    "render_submission",
]
