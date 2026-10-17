"""Grades one attempt: every line under a case's ``expect`` is a yes/no check
against what the run did. An attempt passes only if every check passes.

Checks:
    ack:          {notify: bool, acted_tasks: [names]}  (acted_tasks as a set)
    called:       [tool names] — each called at least once
    not_called:   [tool names] — none of them called
    called_with:  [{tool, args}] — some call of that tool whose args contain these
                  (strings match case-insensitively as substrings; others exactly)
    not_called_with: [{tool, args}] — no call of that tool with args containing these
    sends:        int — how many messages reached the owner through the Outbox
    message_contains / message_not_contains: [strings] — over the text of every
                  message sent to the owner, case-insensitive
    message_matches / message_not_matches: [regexes] — the same text, case-insensitive
    reply_contains / reply_not_contains: [strings] — a chat case's reply (a daily_log
                  case's log), case-insensitive
    reply_matches / reply_not_matches: [regexes] — the same text
"""

from __future__ import annotations

import re


def _args_match(want: dict, got: dict) -> bool:
    for key, value in want.items():
        if key not in got:
            return False
        if isinstance(value, str):
            if value.lower() not in str(got[key]).lower():
                return False
        elif got[key] != value:
            return False
    return True


def grade(expect: dict, result) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []
    if result.error:
        checks.append(("ran without crashing", False, result.error))
    called = [c["tool"] for c in result.calls]

    if "ack" in expect:
        ack = result.ack
        if ack is None:
            checks.append(("ack present", False, "no heartbeat_respond call"))
        else:
            want = expect["ack"]
            if "notify" in want:
                checks.append((f"ack.notify == {want['notify']}", bool(ack.get("notify")) == want["notify"],
                               f"got {ack.get('notify')!r}"))
            if "acted_tasks" in want:
                got = sorted(ack.get("acted_tasks") or [])
                checks.append((f"ack.acted_tasks == {sorted(want['acted_tasks'])}",
                               got == sorted(want["acted_tasks"]), f"got {got}"))
    for name in expect.get("called") or []:
        checks.append((f"called {name}", name in called, f"calls: {called}"))
    for name in expect.get("not_called") or []:
        checks.append((f"did not call {name}", name not in called, f"calls: {called}"))
    for spec in expect.get("called_with") or []:
        hits = [c for c in result.calls if c["tool"] == spec["tool"] and _args_match(spec["args"], c["args"])]
        seen = [c["args"] for c in result.calls if c["tool"] == spec["tool"]]
        checks.append((f"called {spec['tool']} with {spec['args']}", bool(hits), f"got {seen}"))
    for spec in expect.get("not_called_with") or []:
        hits = [c["args"] for c in result.calls if c["tool"] == spec["tool"] and _args_match(spec["args"], c["args"])]
        checks.append((f"did not call {spec['tool']} with {spec['args']}", not hits, f"got {hits}"))
    if "sends" in expect:
        checks.append((f"sends == {expect['sends']}", len(result.sends) == expect["sends"],
                       f"got {len(result.sends)}: {[s['text'][:80] for s in result.sends]}"))
    text = "\n".join(s["text"] for s in result.sends).lower()
    for s in expect.get("message_contains") or []:
        checks.append((f"message contains {s!r}", s.lower() in text, text[:200]))
    for s in expect.get("message_not_contains") or []:
        checks.append((f"message does not contain {s!r}", s.lower() not in text, text[:200]))
    for p in expect.get("message_matches") or []:
        checks.append((f"message matches /{p}/", bool(re.search(p, text, re.I)), text[:300]))
    for p in expect.get("message_not_matches") or []:
        checks.append((f"message does not match /{p}/", not re.search(p, text, re.I), text[:300]))
    reply = (result.reply or "")
    for s in expect.get("reply_contains") or []:
        checks.append((f"reply contains {s!r}", s.lower() in reply.lower(), reply[:200]))
    for s in expect.get("reply_not_contains") or []:
        checks.append((f"reply does not contain {s!r}", s.lower() not in reply.lower(), reply[:200]))
    for p in expect.get("reply_matches") or []:
        checks.append((f"reply matches /{p}/", bool(re.search(p, reply)), reply[:200]))
    for p in expect.get("reply_not_matches") or []:
        checks.append((f"reply does not match /{p}/", not re.search(p, reply), reply[:200]))
    return checks
