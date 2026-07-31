"""A stand-in agent that long-polls the hub, so the app can be built and demoed
before the real agent's app channel exists.

It is a *client*: it connects out to the hub and polls `GET /bot/v1/updates`,
exactly as the real agent will. The poll loop is deliberately two tasks over a
queue — a fetcher that advances the offset the moment a batch is in hand and
re-polls, and a single consumer that runs turns one at a time. That shape is
load-bearing, not tidiness: the re-poll is what acks the previous batch (the
hub's ✓✓), and it must go out *while* a turn is still running, or the tick
collapses into the reply and a whole class of concurrency bug becomes invisible.
A serial poll→reply→poll would do exactly that. One consumer, not a task per
update, so two messages in a row never run their turns at once.

The turn is deliberately dumb — it echoes, and on a keyword it sends a demo
`card` or `confirmation` block, emits demo tool chips, or renders a markdown
torture-test message. One small fixture, several paths, no branching sprawl.
A "think delay" makes each turn take a beat: against an instant echo the ✓✓ and
the reply arrive together whether or not the hub is correct, so the gap a real
turn creates is the only thing that makes the tick observable — raise it with
`--think-delay` for a slow-turn demo.

Confirmations here follow the hub's own block contract (a `confirmation` block
whose question lives in the message text), not any channel-native widget.

It is dev tooling, not shipped with the hub, so it needs the backend's dev
extras (which carry `httpx`): `pip install -e ./backend[dev]`.

Run it against a running hub:
    python scripts/fake_agent.py --base-url http://127.0.0.1:8000 --token <AGENT_TOKEN>
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import signal
import sys

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000"

# The hanging poll's wait, matching the hub's own default. A module attribute so
# a test can shorten it; a live run takes the full value.
POLL_TIMEOUT_S = 25.0

# How long to wait after a failed poll before retrying, so a persistent error
# does not spin. A module attribute for the same reason as the timeout above.
POLL_ERROR_BACKOFF_S = 1.0

# `seed N` emits this many messages so a thread runs several pages deep on demand.
# The default is past the hub's 50-row page, so a bare `seed` already spans pages;
# the ceiling bounds an explicit count, so a fat-fingered number cannot flood the
# hub's single event loop.
SEED_DEFAULT = 120
SEED_MAX = 500

# Declared once at startup so the client's slash menu (GET /v1/commands) has
# something real to show. The names line up with the keywords the turn reacts
# to, so a tapped command actually does the thing it advertises.
COMMANDS = [
    {"name": "card", "description": "Show a demo card"},
    {"name": "confirm", "description": "Ask a demo confirmation"},
    {"name": "markdown", "description": "Render the markdown torture test"},
    {"name": "depth", "description": "Render a list nested past the parser's depth guard"},
]

CARD_BLOCK = {
    "kind": "card",
    "title": "Signal Lost",
    "subtitle": "Sci-fi thriller · 2h 04m",
    "body": "A deep-space relay goes quiet.",
    "actions": [
        {"action_id": "add", "label": "Add to watchlist"},
        {"action_id": "skip", "label": "Not tonight"},
    ],
}

CONFIRM_BLOCK = {"kind": "confirmation", "callback_id": "demo-confirm-1"}

# Every renderer-stressing case in one message, so bidi and rich-text rendering
# has real content to exercise: a heading ladder, all six levels, so the type scale
# is visibly distinct level to level; bold, italic, strikethrough and their
# nesting; emphasis inside an RTL (Hebrew) sentence; Hebrew/English mixups both ways,
# with numbers; flat and nested bullet lists (LTR, RTL, and mixed-direction), a
# numbered list, a list mixing bullet and number markers across nesting levels, and
# a list nested past RichText.kt's own depth guard; a nested blockquote (English
# outer, Hebrew inner, so nesting and per-block direction both show); GFM tables
# with a bold cell, a wide table and a mixed Hebrew/English table, both wide or
# long enough to force horizontal scroll; fenced code across several languages
# with a language hint, including one line long enough to force horizontal
# scroll; inline code, including one inside a Hebrew sentence; a link, plus a
# scheme-rejected one shown as inert text and one whose text is its own target
# (dedup); and a Hebrew paragraph. Later slices light up the structure (real
# table cells); the styling and bidi already do.
#
# Headings divide it into sections so a device capture reads top to bottom, one
# feature area at a time - not a rendering requirement, just this fixture's own
# organization.
#
# The "mixed bullets and numbers" case: commonmark has no single-level list that
# mixes marker types - a marker switch starts a new list, not a mixed one - so this
# nests a bullet sub-list one level under a numbered item instead, the representation
# markdown actually allows.
#
# The "max nesting depth" case stays well under MAX_NODE_DEPTH on purpose: crossing
# it degrades the WHOLE parsed document (see RichText.kt's guard KDoc), which would
# blank out every other section in this same message. What crossing the guard looks
# like is [MARKDOWN_LIST_DEPTH_GUARD]'s own, separate message instead.
MARKDOWN_TORTURE = """\
## Headings demo

# Heading level 1
## Heading level 2
### Heading level 3
#### Heading level 4
##### Heading level 5
###### Heading level 6

## Emphasis

**Bold**, *italic*, and ***both at once***, then **bold that turns *italic* midway**:

This part is ~~struck through~~ and this part is not.

## Inline code

Run `git status` before you commit anything.

הרץ `git commit` כדי לשמור.

## Links

A [link](https://example.com), a [rejected one](javascript:alert(1)), and a bare [mailto:me@example.com](mailto:me@example.com).

## Blockquotes

> A quoted aside, first level.
> > תגובה מקוננת בעברית, ברמה שנייה.

## Lists

### All-English bullet list

- Coffee
- Tea
- Water

### All-Hebrew bullet list

- קפה
- תה
- מים

### Mixed-language bullet list

- Mixed list, first in English
- פריט בעברית באמצע הרשימה
- Back to English

### Numbered list

1. First
2. Second
3. Third

### Nested list

- one
  - one-a
  - one-b
- two

### Hebrew list with nesting

- פריט ראשון בעברית
- פריט שני
  - תת-פריט מקונן

### Mixed bullets and numbers

1. First numbered item
2. Second numbered item
   - a bullet sub-item
   - another bullet sub-item
3. Third numbered item

### Max nesting depth

- depth 1
  - depth 2
    - depth 3
      - depth 4
        - depth 5
          - depth 6
            - depth 7
              - depth 8

8 levels deep, comfortably under the parser's nesting guard. Send "depth" to this bot to see the guard trip on a much deeper list.

## Tables

| **Feature** | Status |
|-------------|--------|
| **tables**  | yes    |
| code        | yes    |

| **שם** | תפקיד |
|--------|-------|
| רועי | מפתח |
| ג'רוויס | **עוזר** |

| ID | Name | Status | Assigned To | Notes |
|----|------|--------|-------------|-------|
| 1042 | Migrate SQLite schema to add per-message delivery receipts | In Progress | Roi | Blocked on the contract review scheduled for next sprint, needs another pass |
| 1043 | Fix Android notification channel priority for background sync | Done | Jarvis | Verified on Pixel 7, landscape and portrait both pass |
| 1044 | Add horizontal scroll support for wide code blocks and tables | In Review | Roi | Needs on-device confirmation before merging to main |

| Task | Owner | Priority |
|------|-------|----------|
| Review pull request | Roi | High |
| בדיקת קוד לפני מיזוג | רועי | גבוהה |
| Deploy to production | Jarvis | Medium |
| פריסה לסביבת הייצור | ג'רוויס | בינונית |

## Code block

```python
def hello() -> str:
    return "world"
```

```javascript
function hello(name) {
  const API_ENDPOINT = "https://api.internal.example.com/v2/workspaces/jarvis-hub/notifications/preferences";
  return `Hello, ${name}!`;
}
```

```rust
fn hello() -> &'static str {
    "world"
}
```

```bash
#!/usr/bin/env bash
find . -type f -name "*.kt" -not -path "*/build/*" -not -path "*/.gradle/*" -exec grep -l "TODO" {} \\;
```

## Hebrew paragraphs

שלום עולם, זה טקסט בעברית שאמור להיות מיושר לימין.

משפט בעברית עם **מילה מודגשת**, עוד *מילה נטויה*, וגם ~~מחיקה~~.

עברית עם המילה English ומספר 42 באמצע המשפט.

## Mixed-direction sentence

A mixed sentence: the agent said שלום and then carried on in English.
"""

# A separate message, not folded into MARKDOWN_TORTURE: RichText.kt's node-depth
# guard (MAX_NODE_DEPTH = 64) is computed once over the WHOLE parsed document, so
# tripping it degrades everything in the message to one literal block, not just the
# list that crossed the limit - see the guard's own KDoc in RichText.kt. Embedding a
# guard-crossing list inside the combined torture message would blank out every
# other section's demo in the same capture, so this ships as its own trigger
# instead. 35 nesting levels of a flat bullet chain reaches a parse-tree depth of
# 2*35+2 = 72, past the 64-level guard (empirically: 30 levels parses fine at
# depth 62; 31 crosses at depth 64) - every "- depth N" line below should render as
# raw, unstyled text, dashes and indentation intact, not as real list items.
MARKDOWN_LIST_DEPTH_GUARD = (
    "List nesting depth guard demo: the flat chain below nests 35 levels deep, past "
    "RichText.kt's MAX_NODE_DEPTH. The guard degrades the WHOLE message to one literal "
    "block, so every line - including this one - should render as raw text, not as a "
    "styled list.\n\n"
    + "\n".join("  " * level + f"- depth {level + 1}" for level in range(35))
)


async def _get_updates(client: httpx.AsyncClient, offset: int) -> list[dict]:
    r = await client.get("/bot/v1/updates", params={"offset": offset, "timeout": POLL_TIMEOUT_S})
    r.raise_for_status()
    return r.json()


async def _bot_send(client: httpx.AsyncClient, body: dict) -> None:
    r = await client.post("/bot/v1/messages", json=body)
    r.raise_for_status()


async def _emit_chip(client: httpx.AsyncClient, chip_type: str, data: dict) -> None:
    r = await client.post("/bot/v1/events", json={"type": chip_type, "data": data})
    r.raise_for_status()


async def _declare_commands(client: httpx.AsyncClient) -> None:
    r = await client.post("/bot/v1/commands", json=COMMANDS)
    r.raise_for_status()


def _seed_count(text: str) -> int | None:
    """If `text` is the `seed [N]` command, the number of messages to emit; else None.

    `seed` counts only as the leading word — `seed the lawn later` is a sentence, not
    the command, so a non-numeric argument falls through to the ordinary echo rather
    than seeding a default flood. A bare `seed` uses [SEED_DEFAULT]; an explicit count
    is clamped to `[1, SEED_MAX]`.
    """
    parts = text.split()
    if not parts or parts[0].lower() != "seed":
        return None
    if len(parts) == 1:
        return SEED_DEFAULT
    try:
        n = int(parts[1])
    except ValueError:
        return None
    return max(1, min(n, SEED_MAX))


async def _run_turn(client: httpx.AsyncClient, update: dict, think_delay: float) -> None:
    text = update["message"].get("text") or ""
    lowered = text.lower()

    seed_n = _seed_count(text)
    if seed_n is not None:
        # A deep thread on demand: N separate assistant messages. Each is its own
        # send, so each becomes its own hub row with its own id — the length is real
        # history, not one long message.
        if think_delay:
            await asyncio.sleep(think_delay)
        for i in range(1, seed_n + 1):
            await _bot_send(client, {"text": f"Seeded message {i} of {seed_n}"})
        return

    # Tool chips go out during the "thinking" gap, before the reply — the demo
    # of the ephemeral stream, and what a streaming client renders as activity.
    if "card" in lowered:
        await _emit_chip(client, "tool_call_started", {"name": "search_library", "query": text})
        await _emit_chip(
            client, "tool_call_result", {"name": "search_library", "ok": True, "summary": "1 hit"}
        )

    if think_delay:
        await asyncio.sleep(think_delay)

    if "card" in lowered:
        body = {"text": "Here's one for tonight:", "blocks": [CARD_BLOCK]}
    elif "confirm" in lowered:
        body = {"text": "Delete all your reminders?", "blocks": [CONFIRM_BLOCK]}
    elif "markdown" in lowered:
        body = {"text": MARKDOWN_TORTURE}
    elif "depth" in lowered:
        body = {"text": MARKDOWN_LIST_DEPTH_GUARD}
    else:
        body = {"text": f"You said: {text}" if text else "You said nothing at all."}

    await _bot_send(client, body)


async def _fetch_loop(client: httpx.AsyncClient, queue: asyncio.Queue, stop: asyncio.Event) -> None:
    offset = 0
    while not stop.is_set():
        try:
            updates = await _get_updates(client, offset)
        except Exception as exc:
            # A dropped connection or a non-2xx on the poll must not silently
            # kill this task — the consumer would then sit idle forever with no
            # symptom. Log, back off, and keep polling. (CancelledError is a
            # BaseException, so a real shutdown still unwinds cleanly.)
            print(f"fake_agent: poll failed, retrying: {exc}", file=sys.stderr)
            await asyncio.sleep(POLL_ERROR_BACKOFF_S)
            continue
        for update in updates:
            # Advance and hand off before doing any work: the next poll (which
            # acks this batch) must not wait behind the turn.
            offset = update["update_id"] + 1
            await queue.put(update)


async def _consume_loop(client: httpx.AsyncClient, queue: asyncio.Queue, think_delay: float) -> None:
    while True:
        update = await queue.get()
        try:
            await _run_turn(client, update, think_delay)
        except Exception as exc:  # one bad turn must not take the agent down
            print(f"fake_agent: turn failed: {exc}", file=sys.stderr)
        finally:
            queue.task_done()


async def run(client: httpx.AsyncClient, *, think_delay: float, stop: asyncio.Event) -> None:
    """Declare commands, then poll and answer until `stop` is set.

    On stop it drains: the fetcher is cancelled (stop fetching, drop any hanging
    poll — nothing is lost, since anything fetched is already on the queue), the
    consumer finishes what is queued, and only then does this return.
    """
    await _declare_commands(client)
    queue: asyncio.Queue = asyncio.Queue()
    fetcher = asyncio.create_task(_fetch_loop(client, queue, stop))
    consumer = asyncio.create_task(_consume_loop(client, queue, think_delay))

    await stop.wait()

    fetcher.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await fetcher
    await queue.join()
    consumer.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await consumer


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="fake_agent")
    parser.add_argument("--base-url", default=os.environ.get("FAKE_AGENT_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--token", default=os.environ.get("FAKE_AGENT_TOKEN"))
    parser.add_argument(
        "--think-delay",
        type=float,
        default=float(os.environ.get("FAKE_AGENT_THINK_DELAY", "0.3")),
        help="seconds each turn spends 'thinking'; raise it for a slow-turn demo",
    )
    return parser.parse_args(argv)


async def _main_async(args: argparse.Namespace) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    headers = {"Authorization": f"Bearer {args.token}"}
    async with httpx.AsyncClient(base_url=args.base_url, headers=headers, timeout=None) as client:
        print(f"fake_agent: polling {args.base_url} (think-delay {args.think_delay}s)")
        await run(client, think_delay=args.think_delay, stop=stop)
    print("fake_agent: drained and stopped")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.token:
        sys.exit("error: no agent token (pass --token or set FAKE_AGENT_TOKEN)")
    asyncio.run(_main_async(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
