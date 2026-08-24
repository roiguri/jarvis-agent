#!/usr/bin/env python3
"""Offline harness for the block layer — no hub, no service, no model.

Drives the real Form validation, the real jarvis-app wire mapping, the real
router dispatch, and the real send_form tool against fakes at the seams
(HubClient, on_message, channel registry). Run from the repo root:

    JARVIS_ROOT=/app/jarvis_staging ./venv/bin/python scripts/test_app_blocks.py
"""

import asyncio
import pathlib
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

FAILS: list[str] = []


def check(name, got, want=True):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f": {got!r} != {want!r}"))
    if not ok:
        FAILS.append(name)


def raises(name, fn, *needles):
    try:
        fn()
    except (ValueError, TypeError) as e:
        ok = all(n in str(e) for n in needles)
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f": {e}"))
        if not ok:
            FAILS.append(name)
    else:
        print(f"FAIL  {name}: did not raise")
        FAILS.append(name)


# ── 1 · Form validation ────────────────────────────────────────────────────
from gateway.blocks import Form, FormRow, NumberField, TextField, render_submission


def bench_form(**kw):
    args = dict(
        summary="Push day: 2 to log",
        slug="push-day",
        title="Push day",
        rows=(
            FormRow("Bench press", (
                NumberField("bench_reps", default=8, unit="reps"),
                NumberField("bench_kg", default=60, unit="kg"),
            )),
            FormRow("Core", (TextField("core", default="plank 3×45s"),)),
        ),
    )
    args.update(kw)
    return Form(**args)


f = bench_form()
check("valid form builds", f.title, "Push day")
check("callback_id = slug + entropy", f.callback_id.startswith("push-day-") and len(f.callback_id) > len("push-day-"))
check("two forms never share an id", bench_form().callback_id != bench_form().callback_id)
check("describe carries ids and prefills",
      f.describe(), "Bench press: bench_reps=8 reps, bench_kg=60 kg · Core: core=plank 3×45s")

raises("values is unconstructible", lambda: Form(summary="s", slug="s", title="t", rows=(), values={}), "values")
raises("empty rows refused", lambda: bench_form(rows=()), "at least one row")
raises("row cap enforced",
       lambda: bench_form(rows=tuple(FormRow(f"r{i}", (NumberField(f"f{i}", unit="x"),)) for i in range(7))),
       "7 rows")
raises("bare boxes on a multi-field row refused",
       lambda: FormRow("Bench", (NumberField("a", unit="kg"), NumberField("b"))), "unit", "b")
check("single field needs no unit", FormRow("Core", (TextField("core"),)).label, "Core")
raises("text default must be a string", lambda: TextField("t", default=7), "must be a string")
raises("number default must be a number", lambda: NumberField("n", default="sixty"), "must be a number")
raises("bool default refused", lambda: NumberField("n", default=True), "must be a number")
raises("duplicate field_id refused",
       lambda: bench_form(rows=(FormRow("A", (NumberField("x", unit="kg"),)),
                                FormRow("B", (NumberField("x", unit="kg"),)))), "duplicate")
raises("slug is kebab-case", lambda: bench_form(slug="Push Day"), "kebab")
raises("field_id is snake_case", lambda: TextField("Bench-Reps"), "snake_case")
raises("a form needs a title", lambda: bench_form(title=" "), "title")
raises("a form needs a summary", lambda: bench_form(summary=""), "notification preview")

check("null renders as left empty, int stays int",
      render_submission("push-day-a3f1", {"bench_reps": 8, "fly_kg": None, "core": "plank"}),
      "[Submitted form push-day-a3f1] bench_reps: 8 · fly_kg: (left empty) · core: plank")

# ── 2 · Wire mapping ───────────────────────────────────────────────────────
from gateway.channels.jarvis_app.channel import JarvisAppChannel, _form_wire

wire = _form_wire(f)
check("wire kind/summary", (wire["kind"], wire["summary"]), ("form", "Push day: 2 to log"))
check("wire payload keys", sorted(wire["payload"]), ["callback_id", "rows", "title"])
check("wire row", wire["payload"]["rows"][0],
      {"label": "Bench press", "fields": [
          {"field_id": "bench_reps", "type": "number", "unit": "reps", "default": 8},
          {"field_id": "bench_kg", "type": "number", "unit": "kg", "default": 60}]})
check("absent unit/default omitted, not null",
      "unit" not in wire["payload"]["rows"][1]["fields"][0]
      and wire["payload"]["rows"][1]["fields"][0]["default"] == "plank 3×45s")
g = bench_form(subtitle="2 exercises", submit_label="Log workout")
gw = _form_wire(g)["payload"]
check("subtitle/submit_label pass through", (gw["subtitle"], gw["submit_label"]),
      ("2 exercises", "Log workout"))


# ── 3 · Channel send_block ─────────────────────────────────────────────────
class FakeClient:
    def __init__(self):
        self.sent, self.patches = [], []

    async def send_message(self, body):
        self.sent.append(body)
        return {"id": 41}

    async def patch_message_state(self, message_id, state):
        self.patches.append((message_id, state))


fc = FakeClient()
app_channel = JarvisAppChannel(fc, owner_id="owner")
check("jarvis-app supports form", app_channel.supports_block("form"))
check("jarvis-app does not claim card", app_channel.supports_block("card"), False)
asyncio.run(app_channel.send_block("Fill it in.", f))
check("one POST carries text and block",
      (fc.sent[0]["text"], fc.sent[0]["blocks"][0]["kind"]), ("Fill it in.", "form"))

from gateway.base import Channel

check("Channel default declines blocks", Channel.supports_block(app_channel, "form"), False)


# ── 4 · Router dispatch ────────────────────────────────────────────────────
from gateway.channels.jarvis_app.router import JarvisAppInboundRouter


class FakeUI:
    def __init__(self):
        self.calls = []

    async def handle_action(self, **kw):
        self.calls.append(kw)


def build_router(on_message):
    fc = FakeClient()
    channel = JarvisAppChannel(fc, owner_id="owner")
    ui = FakeUI()
    return JarvisAppInboundRouter(channel, fc, on_message, ui), fc, ui


def submit_update(values):
    return {"type": "action", "update_id": 1, "message_id": 412, "action_id": "submit",
            "block_kind": "form", "callback_id": "push-day-a3f1", "values": values}


turns = []


async def ok_turn(inbound):
    turns.append(inbound)
    return "Logged it."


router, fc, ui = build_router(ok_turn)
asyncio.run(router._handle(submit_update({"bench_reps": 8, "core": None})))
check("submit runs a turn with the rendered text",
      turns[0].user_text, "[Submitted form push-day-a3f1] bench_reps: 8 · core: (left empty)")
check("submit lands on the owner thread", turns[0].thread_id, "jarvis-app_owner")
check("reply sent, then PATCH logged",
      (fc.sent[0]["text"], fc.patches), ("Logged it.", [(412, "logged")]))


async def dead_turn(inbound):
    raise RuntimeError("model exploded")


router, fc, ui = build_router(dead_turn)
try:
    asyncio.run(router._handle(submit_update({"a": 1})))
except RuntimeError:
    pass
check("crashed turn leaves the card alone (no PATCH)", fc.patches, [])

router, fc, ui = build_router(ok_turn)
asyncio.run(router._handle({"type": "action", "update_id": 2, "message_id": 9,
                            "action_id": "confirm", "block_kind": "confirmation",
                            "callback_id": "cb1"}))
check("confirmation still routes below the LLM",
      ui.calls, [{"action_id": "confirm", "message_id": 9,
                  "block_kind": "confirmation", "callback_id": "cb1"}])

n_turns = len(turns)
asyncio.run(router._handle({"type": "action", "update_id": 3, "message_id": 10,
                            "action_id": "pick", "block_kind": "buttons",
                            "callback_id": None}))
check("unhandled kind ignored quietly", (len(turns), fc.patches), (n_turns, []))


# ── 5 · The tool ───────────────────────────────────────────────────────────
import turn_context
from gateway import factory
from gateway import outbox as outbox_mod
from gateway.outbox import Outbox
from tools.core.forms import send_form

ROWS = [{"label": "Bench press", "fields": [
    {"field_id": "bench_reps", "type": "number", "unit": "reps", "default": 8},
    {"field_id": "bench_kg", "type": "number", "unit": "kg", "default": 60}]}]
ARGS = dict(message_text="Push day — fill in what you hit.", slug="push-day",
            title="Push day", summary="Push day: log it", rows=ROWS)

# A loop in a background thread stands in for the host loop, so the tool's
# sync body can block on submit(...).result() exactly as it does in production.
loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True).start()
outbox_mod.bind_loop(loop)

fc = FakeClient()
app_channel = JarvisAppChannel(fc, owner_id="owner")
factory.register_channel(app_channel, Outbox(app_channel))


class Blockless:
    name = "telegram"

    def supports_block(self, kind):
        return False


factory._registry["telegram"] = factory._Registered(Blockless(), Outbox(Blockless()))

tok = turn_context.CURRENT_THREAD_ID.set("jarvis-app_owner")
out = send_form.func(**ARGS)
check("tool sends on the origin channel",
      out.startswith("Sent form push-day-") and "bench_reps=8 reps" in out)
check("hub got text + form", (fc.sent[0]["text"], fc.sent[0]["blocks"][0]["kind"]),
      ("Push day — fill in what you hit.", "form"))
turn_context.CURRENT_THREAD_ID.reset(tok)

tok = turn_context.CURRENT_THREAD_ID.set("telegram_42")
out = send_form.func(**ARGS)
check("blockless origin declines with the prefills",
      out.startswith("Forms aren't available on telegram — nothing was sent")
      and "bench_reps=8 reps" in out)
turn_context.CURRENT_THREAD_ID.reset(tok)

out = send_form.func(**{**ARGS, "rows": [{"label": "Bench", "fields": [
    {"field_id": "reps", "type": "choice"}]}]})
check("bad field type is a correctable error",
      out.startswith("Error: invalid form") and "'text' or 'number'" in out)

out = send_form.func(**{**ARGS, "rows": []})
check("no rows is a correctable error", out.startswith("Error: invalid form"))

out = send_form.func(**{**ARGS, "rows": [{"label": "Bench", "fields": [
    {"field_id": "reps", "type": "number", "value": 8}]}]})
check("unknown field key refused, not dropped",
      out.startswith("Error: invalid form") and "'value'" in out)

out = send_form.func(**{**ARGS, "rows": [{"label": "Bench", "group": "push", "fields": [
    {"field_id": "reps", "type": "number"}]}]})
check("unknown row key refused", out.startswith("Error: invalid form") and "'group'" in out)

# Timeout path: a send_block that never resolves must come back as an explicit
# delivery-unknown directive, not a blank error.
import tools.core.forms as forms_mod


class Hanging:
    name = "jarvis-app"

    def supports_block(self, kind):
        return True

    async def send_block(self, text, block):
        await asyncio.sleep(3600)


factory._registry["jarvis-app"] = factory._Registered(Hanging(), Outbox(Hanging()))
forms_mod._SEND_TIMEOUT_S = 0.2
tok = turn_context.CURRENT_THREAD_ID.set("jarvis-app_owner")
out = send_form.func(**ARGS)
check("timeout returns delivery-unknown directive",
      out.startswith("Error: the form send was not confirmed")
      and "Do not send it again" in out)
turn_context.CURRENT_THREAD_ID.reset(tok)
factory._registry["jarvis-app"] = factory._Registered(app_channel, Outbox(app_channel))

loop.call_soon_threadsafe(loop.stop)

print()
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILURES: {FAILS}")
sys.exit(1 if FAILS else 0)
