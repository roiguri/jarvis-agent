# Outbound Messaging Unification — Outbox + Full Channel Decoupling

Tracking: [issue #29](https://github.com/roiguri/jarvis-agent/issues/29)
Branch: `feat/outbox-unification` (based on `feat/heartbeat-gating`; PR targets `main` after gating merges).

## Checklist

- [x] Slice 0 — this plan doc
- [x] Slice 1 — Outbox core + reminders migrated (+ delete-on-success fix)
- [x] Slice 2 — heartbeat notifications (+ stamp-after-delivery fix)
- [x] Slice 3 — media webhook notifier
- [ ] Slice 4 — confirmation outcomes + shared loop bridge
- [ ] Slice 5 — host decoupling (main.py loses all Telegram knowledge)
- [ ] Slice 6 — docs alignment + archive this plan

Workflow per slice: implement → user restarts `jarvis.service` → verify per the slice's list → user approves → commit. One commit per slice.

---

## Problem (audit of current code)

Outbound messages to the user go through one `Channel` ABC, but every caller hand-rolls its own send — combining `send_to_owner` + notification logging + a bare exception-swallowing `try/except` differently at each site:

| Site | Trigger | notifications.jsonl | Failure handling |
|---|---|---|---|
| `heartbeat.py:156` | heartbeat notify | hand-rolled, `event="heartbeat"` | swallowed |
| `heartbeat.py:185` | reminder | hand-rolled, `event="reminder"` | swallowed |
| `main.py:108` | confirmation outcome (conversational) | not logged | swallowed |
| `gateway/confirmation/store.py:178` | confirmation outcome fallback | not logged | swallowed |
| `gateway/webhook/notifier.py:295,299` | media webhook | injected sink, `"notification"`/`"llm_notification"` | swallowed |

- Event strings are ad-hoc literals — and load-bearing: `agent.py` filters `event == "heartbeat"` to build the user-scope prompt's awareness slice.
- No caller can react to a failed delivery. Two real bugs:
  1. `run_heartbeat` stamps `state.json` **before** the send — a failed send permanently drops the notification with no retry.
  2. `fire_reminder` calls `_remove_event` unconditionally, outside the send's `try/except` — a failed send still deletes the reminder.
- The thread→loop bridge (`bind_loop` + `run_coroutine_threadsafe`) exists only inside `InMemoryConfirmationStore`; nothing else can safely reach the channel from a worker thread.
- `main.py` is Telegram-shaped: PTB imports, handler wiring, `TELEGRAM_BOT_TOKEN`/`ALLOWED_USER_ID` reads, and a hardcoded `telegram_<id>` thread id — defeating the gateway's "add a channel without touching the host" goal.

## Decisions

- Fix both delivery-failure bookkeeping bugs here (send first, bookkeep on success).
- Full host decoupling: PTB lifecycle moves into `gateway/channels/telegram/`; `main.py` ends with zero telegram imports.
- Confirmation outcomes stay chat-log-only — `notifications.jsonl` remains "proactive pushes only" (a second copy would also double-inject into the prompt-awareness slices).
- Reply-context sends inside the channel package (`TelegramInboundRouter._dispatch`, `TelegramConfirmationUI`) are **not** migrated: the Outbox is the *domain → channel* seam for owner-addressed sends; the channel package talking to its own channel is not a leak.

---

## Slice 1 — Outbox core + first consumer: reminders

New `gateway/outbox.py`:

```python
EVENT_HEARTBEAT = "heartbeat"; EVENT_REMINDER = "reminder"
EVENT_MEDIA = "notification"; EVENT_LLM_MEDIA = "llm_notification"  # values FROZEN (agent.py filter, existing log rows)

@dataclass
class SendOutcome: ok: bool; error: str | None = None

def bind_loop(loop) -> None          # shared thread→loop bridge (module-level)
def submit(coro) -> concurrent.futures.Future

class Outbox:
    def __init__(self, channel: Channel, log_sink: LogSink | None): ...
    async def notify_owner(text, *, event: str | None = None, metadata: dict | None = None) -> SendOutcome
    async def notify_owner_media(kind, payload, caption=None, *, event=None, metadata=None) -> SendOutcome
```

Semantics: send via `channel.send_to_owner[_media]`; on success and `event is not None`, log via the injected `log_sink` (same `Callable[[str, str, dict], Awaitable[None]]` shape the notifier already uses); on exception, `logger.exception` inside the Outbox and return `SendOutcome(ok=False, ...)` — never raise. Gateway keeps zero tools-layer imports; `main.py` injects `async_append_notification_log`.

Wiring: `factory.build_telegram_stack` gains a `log_sink` param, constructs the Outbox, adds it to `TelegramStack`, registers `set_default_outbox` / `default_outbox()` (mirrors `default_user_channel`).

First consumer — `fire_reminder`: send via `default_outbox().notify_owner(text, event=EVENT_REMINDER)`; `_remove_event` **only when `outcome.ok`**; on failure increment a `retries` counter in the event dict (cap 3), reschedule the same job id via `get_scheduler().add_job(DateTrigger(now+5min))`; past the cap, remove + log error. Keeping the event preserves restart recovery in `main.py`.

**Files:** `gateway/outbox.py` (new), `gateway/factory.py`, `main.py`, `heartbeat.py` (fire_reminder only).

**Verify:**
- Restart clean (journalctl: stack built, polling, webhook up).
- Reminder 2 min out → arrives on Telegram, `event="reminder"` row in notifications.jsonl, event removed from `scheduled_events.json`.
- Chat reply regression: send a normal message.

## Slice 2 — Heartbeat notifications (+ stamp-after-delivery)

`run_heartbeat`: compute `deliver`/`text` from the ack as today, **deliver first** via `default_outbox().notify_owner(text, event=EVENT_HEARTBEAT)`, then stamp `acted` tasks only when `not deliver` or `outcome.ok`; on a failed send, warn + skip stamping so the tasks re-run next tick. Remove the hand-rolled `append_notification_log` import.

**Files:** `heartbeat.py`.

**Verify:**
- Next due tick: log ordering shows send before "stamped last_run"; `event="heartbeat"` row lands.
- A notify=False tick still stamps.
- After a push, chat Jarvis and confirm it references the notification (proves the `event=="heartbeat"` prompt filter still matches).

## Slice 3 — Media webhook notifier

`MediaNotificationManager(outbox, llm_format)` — drops `channel` and `log_notification`. `_send_direct` → `outbox.notify_owner[_media](..., event=EVENT_MEDIA)`; `_send_via_llm` passes `EVENT_LLM_MEDIA`. Log-on-success + `has_image` metadata semantics unchanged. `main.py` constructs from `stack.outbox`.

**Files:** `gateway/webhook/notifier.py`, `main.py`.

**Verify:** `python scripts/test_webhooks.py` against :8000 → batched notification with poster arrives; `event="notification"` row with `has_image`.

## Slice 4 — Confirmation outcomes + shared loop bridge

`InMemoryConfirmationStore` takes `outbox` instead of `channel`; `_deliver_outcome` fallback → `outbox.notify_owner(system_text)` (no event → not notification-logged). Drop the store's private `_loop`/`bind_loop`; `send_prompt` scheduling uses `gateway.outbox.submit()` (the `add_done_callback` backstop unchanged). `main.py on_confirmation_outcome` reply → `default_outbox().notify_owner(reply)` (chat-logged already). Telegram `ConfirmationUI` stays on the channel.

**Files:** `gateway/confirmation/store.py`, `gateway/factory.py`, `main.py`.

**Verify:**
- Delete a scratch memory file via Jarvis → inline keyboard → Confirm → conversational acknowledgement arrives; row in `chat_history.jsonl`, **not** in `notifications.jsonl`.
- Let one prompt expire (5 min TTL) → expiry outcome delivered.

## Slice 5 — Host decoupling

New `gateway/channels/telegram/host.py` owning everything PTB currently in `main.py`: builds the `Application` from `TELEGRAM_BOT_TOKEN`; registers the four `MessageHandler`s → `router.handle_*` and the `CallbackQueryHandler` → `confirmation_ui.handle_callback`; `async start()` = initialize → `channel.attach(bot)` → `outbox.bind_loop` → `store.start_sweeper()` → `register_command_menu()` → start + `start_polling`; `async stop()` = updater/application stop + shutdown (the same sequence PTB's `async with` performs, spelled out so the host can interleave scheduler/webhook startup).

Factory: `build_telegram_stack(on_message, on_confirmation_outcome, log_sink)` reads `ALLOWED_USER_ID` itself (owner-config belongs to the channel per GATEWAY.md); stack gains `start()`/`stop()`. New `default_owner_thread_id()` accessor (delegates to the channel, matches `router.py:_thread_id`) replaces the hardcoded thread id in `main.py`.

`main.py` after: no telegram imports, no token/owner env reads. Sequence: trim logs → build stack → notifier + webhook app → `await stack.start()` → scheduler init + jobs (unchanged) → `await webhook_server.serve()` → `scheduler.shutdown()` → `await stack.stop()`.

**Files:** `gateway/channels/telegram/host.py` (new), `gateway/factory.py`, `main.py`.

**Verify:** full regression — restart clean; chat message → reply; `/status`; command autocomplete still registered; reminder fires; webhook script; confirmation flow; `grep -n "telegram" main.py` → nothing.

## Slice 6 — Docs alignment

- `docs/architecture/GATEWAY.md`: Plane 2 rewritten around the Outbox (caller → Outbox → Channel; logging + `SendOutcome`); Plane 3 diagram updated; "Adding a New Channel" checklist gains the host-lifecycle step.
- `docs/architecture/HEARTBEAT.md`: stamp-after-delivery semantics.
- `CLAUDE.md`: repo-layout tree (+`gateway/outbox.py`, +`channels/telegram/host.py`), Confirmation Pattern paragraph, heartbeat short version.
- Tick all checkboxes; move this file → `docs/plans/archive/`.

**Verify:** docs consistent with code (grep for stale `default_user_channel` claims).
