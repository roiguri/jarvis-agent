"""The block layer: the real Form validation, the jarvis-app wire mapping, the
router dispatch and the send_form tool, against fakes at the seams (hub client,
on_message, channel registry)."""

import asyncio
import threading

import pytest

import turn_context
import tools.core.forms as forms_mod
from gateway import outbox as outbox_mod
from gateway.base import Channel
from gateway.blocks import Form, FormRow, NumberField, TextField, render_submission
from gateway.channels.jarvis_app.channel import JarvisAppChannel, _form_wire
from gateway.channels.jarvis_app.router import JarvisAppInboundRouter
from gateway.outbox import Outbox
from tools.core.forms import send_form


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


class FakeClient:
    def __init__(self):
        self.sent, self.patches = [], []

    async def send_message(self, body):
        self.sent.append(body)
        return {"id": 41}

    async def patch_message_state(self, message_id, state):
        self.patches.append((message_id, state))


# ── Form validation ────────────────────────────────────────────────────────

def test_form_builds():
    f = bench_form()
    assert f.title == "Push day", "valid form builds"
    assert f.callback_id.startswith("push-day-") and len(f.callback_id) > len("push-day-"), \
        "callback_id = slug + entropy"
    assert bench_form().callback_id != bench_form().callback_id, "two forms never share an id"
    assert f.describe() == "Bench press: bench_reps=8 reps, bench_kg=60 kg · Core: core=plank 3×45s", \
        "describe carries ids and prefills"
    assert FormRow("Core", (TextField("core"),)).label == "Core", "single field needs no unit"


@pytest.mark.parametrize("build, needle", [
    pytest.param(lambda: Form(summary="s", slug="s", title="t", rows=(), values={}), "values",
                 id="values is unconstructible"),
    pytest.param(lambda: bench_form(rows=()), "at least one row", id="empty rows refused"),
    pytest.param(lambda: bench_form(rows=tuple(
        FormRow(f"r{i}", (NumberField(f"f{i}", unit="x"),)) for i in range(7))), "7 rows",
        id="row cap enforced"),
    pytest.param(lambda: TextField("t", default=7), "must be a string",
                 id="text default must be a string"),
    pytest.param(lambda: NumberField("n", default="sixty"), "must be a number",
                 id="number default must be a number"),
    pytest.param(lambda: NumberField("n", default=True), "must be a number", id="bool default refused"),
    pytest.param(lambda: bench_form(rows=(FormRow("A", (NumberField("x", unit="kg"),)),
                                          FormRow("B", (NumberField("x", unit="kg"),)))), "duplicate",
                 id="duplicate field_id refused"),
    pytest.param(lambda: bench_form(slug="Push Day"), "kebab", id="slug is kebab-case"),
    pytest.param(lambda: TextField("Bench-Reps"), "snake_case", id="field_id is snake_case"),
    pytest.param(lambda: bench_form(title=" "), "title", id="a form needs a title"),
    pytest.param(lambda: bench_form(summary=""), "notification preview", id="a form needs a summary"),
])
def test_form_refuses(build, needle):
    with pytest.raises((ValueError, TypeError), match=needle):
        build()


def test_multi_field_row_needs_units():
    with pytest.raises((ValueError, TypeError), match="unit") as exc:
        FormRow("Bench", (NumberField("a", unit="kg"), NumberField("b")))
    assert "b" in str(exc.value), "names the bare field"


def test_render_submission():
    got = render_submission("push-day-a3f1", {"bench_reps": 8, "fly_kg": None, "core": "plank"})
    assert got == "[Submitted form push-day-a3f1] bench_reps: 8 · fly_kg: (left empty) · core: plank", \
        "null renders as left empty, int stays int"


# ── Wire mapping ───────────────────────────────────────────────────────────

def test_form_wire():
    wire = _form_wire(bench_form())
    assert (wire["kind"], wire["summary"]) == ("form", "Push day: 2 to log"), "wire kind/summary"
    assert sorted(wire["payload"]) == ["callback_id", "rows", "title"], "wire payload keys"
    assert wire["payload"]["rows"][0] == {"label": "Bench press", "fields": [
        {"field_id": "bench_reps", "type": "number", "unit": "reps", "default": 8},
        {"field_id": "bench_kg", "type": "number", "unit": "kg", "default": 60}]}, "wire row"
    core = wire["payload"]["rows"][1]["fields"][0]
    assert "unit" not in core and core["default"] == "plank 3×45s", "absent unit/default omitted, not null"
    gw = _form_wire(bench_form(subtitle="2 exercises", submit_label="Log workout"))["payload"]
    assert (gw["subtitle"], gw["submit_label"]) == ("2 exercises", "Log workout"), \
        "subtitle/submit_label pass through"


# ── Channel send_block ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_channel_send_block():
    fc = FakeClient()
    app_channel = JarvisAppChannel(fc, owner_id="owner")
    assert app_channel.supports_block("form"), "jarvis-app supports form"
    assert not app_channel.supports_block("card"), "jarvis-app does not claim card"
    await app_channel.send_block("Fill it in.", bench_form())
    assert (fc.sent[0]["text"], fc.sent[0]["blocks"][0]["kind"]) == ("Fill it in.", "form"), \
        "one POST carries text and block"
    assert not Channel.supports_block(app_channel, "form"), "Channel default declines blocks"


# ── Router dispatch ────────────────────────────────────────────────────────

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


@pytest.mark.asyncio
async def test_router_submit_runs_a_turn():
    turns = []

    async def ok_turn(inbound):
        turns.append(inbound)
        return "Logged it."

    router, fc, ui = build_router(ok_turn)
    await router._handle(submit_update({"bench_reps": 8, "core": None}))
    assert turns[0].user_text == "[Submitted form push-day-a3f1] bench_reps: 8 · core: (left empty)", \
        "submit runs a turn with the rendered text"
    assert turns[0].thread_id == "owner", "submit lands on the owner thread"
    assert (fc.sent[0]["text"], fc.patches) == ("Logged it.", [(412, "logged")]), \
        "reply sent, then PATCH logged"

    router, fc, ui = build_router(ok_turn)
    await router._handle({"type": "action", "update_id": 2, "message_id": 9,
                          "action_id": "confirm", "block_kind": "confirmation",
                          "callback_id": "cb1"})
    assert ui.calls == [{"action_id": "confirm", "message_id": 9,
                         "block_kind": "confirmation", "callback_id": "cb1"}], \
        "confirmation still routes below the LLM"

    n_turns = len(turns)
    await router._handle({"type": "action", "update_id": 3, "message_id": 10,
                          "action_id": "pick", "block_kind": "buttons",
                          "callback_id": None})
    assert (len(turns), fc.patches) == (n_turns, []), "unhandled kind ignored quietly"


@pytest.mark.asyncio
async def test_router_crashed_turn_leaves_card():
    async def dead_turn(inbound):
        raise RuntimeError("model exploded")

    router, fc, ui = build_router(dead_turn)
    with pytest.raises(RuntimeError):
        await router._handle(submit_update({"a": 1}))
    assert fc.patches == [], "crashed turn leaves the card alone (no PATCH)"


# ── The send_form tool ─────────────────────────────────────────────────────

ROWS = [{"label": "Bench press", "fields": [
    {"field_id": "bench_reps", "type": "number", "unit": "reps", "default": 8},
    {"field_id": "bench_kg", "type": "number", "unit": "kg", "default": 60}]}]
ARGS = dict(message_text="Push day — fill in what you hit.", slug="push-day",
            title="Push day", summary="Push day: log it", rows=ROWS)


class Blockless:
    name = "telegram"

    def supports_block(self, kind):
        return False


class Hanging:
    name = "jarvis-app"

    def supports_block(self, kind):
        return True

    async def send_block(self, text, block):
        await asyncio.sleep(3600)


@pytest.fixture
def host(gateway_state):
    """A loop in a background thread stands in for the host loop, so the tool's
    sync body blocks on submit(...).result() exactly as it does in production.
    Registers a jarvis-app channel (default) and a blockless one."""
    factory = gateway_state
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    outbox_mod.bind_loop(loop)
    fc = FakeClient()
    app_channel = JarvisAppChannel(fc, owner_id="owner")
    factory.register_channel(app_channel, Outbox(app_channel))
    factory._registry["telegram"] = factory._Registered(Blockless(), Outbox(Blockless()))
    factory.set_default_channel("jarvis-app")
    yield factory, fc, app_channel
    loop.call_soon_threadsafe(loop.stop)


def on_channel(name):
    return turn_context.CURRENT_CHANNEL.set(name)


def test_send_form_on_origin_channel(host):
    _, fc, _ = host
    tok = on_channel("jarvis-app")
    try:
        out = send_form.func(**ARGS)
    finally:
        turn_context.CURRENT_CHANNEL.reset(tok)
    assert out.startswith("Sent form push-day-") and "bench_reps=8 reps" in out, \
        "tool sends on the origin channel"
    assert (fc.sent[0]["text"], fc.sent[0]["blocks"][0]["kind"]) == \
        ("Push day — fill in what you hit.", "form"), "hub got text + form"


def test_send_form_blockless_channel(host):
    factory, _, _ = host
    tok = on_channel("telegram")
    try:
        out = send_form.func(**ARGS)
    finally:
        turn_context.CURRENT_CHANNEL.reset(tok)
    assert out.startswith("Forms aren't available on telegram — nothing was sent") \
        and "bench_reps=8 reps" in out, "blockless origin declines with the prefills"

    # An origin-less turn (heartbeat) resolves to the default channel.
    factory.set_default_channel("telegram")
    out = send_form.func(**ARGS)
    assert out.startswith("Forms aren't available on telegram"), \
        "origin-less turn falls back to the default channel"


@pytest.mark.parametrize("rows, needles", [
    pytest.param([{"label": "Bench", "fields": [{"field_id": "reps", "type": "choice"}]}],
                 ["'text' or 'number'"], id="bad field type"),
    pytest.param([], [], id="no rows"),
    pytest.param([{"label": "Bench", "fields": [{"field_id": "reps", "type": "number", "value": 8}]}],
                 ["'value'"], id="unknown field key refused, not dropped"),
    pytest.param([{"label": "Bench", "group": "push", "fields": [{"field_id": "reps", "type": "number"}]}],
                 ["'group'"], id="unknown row key refused"),
])
def test_send_form_invalid_rows(host, rows, needles):
    out = send_form.func(**{**ARGS, "rows": rows})
    assert out.startswith("Error: invalid form"), "a correctable error"
    for n in needles:
        assert n in out


def test_send_form_timeout(host, monkeypatch):
    """A send_block that never resolves comes back as an explicit
    delivery-unknown directive, not a blank error."""
    factory, _, _ = host
    factory._registry["jarvis-app"] = factory._Registered(Hanging(), Outbox(Hanging()))
    monkeypatch.setattr(forms_mod, "_SEND_TIMEOUT_S", 0.2)
    tok = on_channel("jarvis-app")
    try:
        out = send_form.func(**ARGS)
    finally:
        turn_context.CURRENT_CHANNEL.reset(tok)
    assert out.startswith("Error: the form send was not confirmed") and "Do not send it again" in out


def test_heartbeat_form_send_is_logged(host):
    """Heartbeat-scope sends are event-tagged into the notification log for the
    mirror; user-scope sends are not."""
    factory, _, _ = host
    logged = []

    async def sink(event, text, metadata):
        logged.append((event, text))

    fc = FakeClient()
    app_channel = JarvisAppChannel(fc, owner_id="owner")
    factory._registry["jarvis-app"] = factory._Registered(app_channel, Outbox(app_channel, sink))

    scope_tok = turn_context.CURRENT_SCOPE.set("heartbeat")
    try:
        send_form.func(**ARGS)
    finally:
        turn_context.CURRENT_SCOPE.reset(scope_tok)
    assert logged == [("heartbeat", "Push day — fill in what you hit.")], \
        "heartbeat form send logged for the mirror"

    logged.clear()
    tok = on_channel("jarvis-app")
    try:
        send_form.func(**ARGS)
    finally:
        turn_context.CURRENT_CHANNEL.reset(tok)
    assert logged == [], "user form send stays unlogged"


def test_heartbeat_form_result_says_text_was_sent(host):
    """In a background turn the result tells the model the card's text already
    reached the user, so the ack doesn't send it again; a user turn's doesn't."""
    scope_tok = turn_context.CURRENT_SCOPE.set("heartbeat")
    try:
        out = send_form.func(**ARGS)
    finally:
        turn_context.CURRENT_SCOPE.reset(scope_tok)
    assert out.startswith("Sent form") and "don't repeat it in heartbeat_respond" in out

    tok = on_channel("jarvis-app")
    try:
        out = send_form.func(**ARGS)
    finally:
        turn_context.CURRENT_CHANNEL.reset(tok)
    assert out.startswith("Sent form") and "heartbeat_respond" not in out
