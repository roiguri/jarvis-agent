"""Gates: cheap code checks that decide whether a gated task has work, and
the handlers that do that work without a model.

A HEARTBEAT.md task with ``| gate: <name>`` never runs in a tick's model turn.
When it is due, its check runs here. Unchanged → done, no model. Fired → its
handler runs deterministic work (a DB sync) and returns follow-ups: keyed
one-shot triggers to create or cancel. Any model work happens in the wakes it
creates. The check's state commits only after the handler and its follow-ups
succeed, so a failure leaves the change visible to the next tick.

Checks and handlers live with the skill that owns their data and register
here, the way tools register into tools.registry.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from triggers import scheduler, store
from triggers.model import ORIGIN_CODE, At, Send, Trigger, Turn

logger = logging.getLogger(__name__)

# A check that can't answer within this is a failure for this tick.
_CHECK_TIMEOUT_S = 60
# Consecutive failures before the owner hears about it (once per streak).
_FAILURE_NOTICE_AFTER = 3


@dataclass(frozen=True)
class GateResult:
    fire: bool
    state: Any            # committed only after the handler succeeds
    message: str = ""     # what changed, for logs and for the handler
    data: Any = None      # handed to the handler, never stored


@dataclass(frozen=True)
class Upsert:
    """Make the trigger with this key exist at ``at`` with ``action``."""
    key: str
    at: datetime
    action: Send | Turn


@dataclass(frozen=True)
class Cancel:
    """Remove every trigger whose key starts with ``key_prefix``."""
    key_prefix: str


FollowUp = Upsert | Cancel

_CHECKS: dict[str, Callable[[Any], GateResult]] = {}
_HANDLERS: dict[str, Callable[[GateResult], list[FollowUp]]] = {}
_failures: dict[str, int] = {}


def gate(name: str):
    """Register a check: ``fn(state) -> GateResult``. Must only read."""
    def register(fn):
        _CHECKS[name] = fn
        return fn
    return register


def gate_handler(name: str):
    """Register the handler for a fired check: ``fn(result) -> [FollowUp]``."""
    def register(fn):
        _HANDLERS[name] = fn
        return fn
    return register


def is_registered(name: str) -> bool:
    """Whether a gate name has both its check and its handler."""
    return name in _CHECKS and name in _HANDLERS


def _plan(followups: list[FollowUp], task: str) -> tuple[list[Trigger], list[Trigger], list[Trigger]]:
    """Resolve follow-ups against the store: (to add, to remove, unchanged).
    An unchanged upsert adds nothing, so applying the same follow-ups twice
    changes nothing."""
    add: list[Trigger] = []
    remove: dict[str, Trigger] = {}
    keep: list[Trigger] = []
    for f in followups:
        if isinstance(f, Cancel):
            for t in store.with_key_prefix(f.key_prefix):
                remove[t.id] = t
            add = [t for t in add if not t.key.startswith(f.key_prefix)]
            continue
        action = f.action
        # A gate's wakes belong to its task: the turn is shown the task's block.
        if isinstance(action, Turn) and action.task is None:
            action = Turn(action.instruction, task)
        existing = store.get_by_key(f.key)
        if existing is not None and existing.id not in remove:
            if existing.when.instant == f.at and existing.action == action:
                keep.append(existing)
                continue
            remove[existing.id] = existing
        add.append(Trigger(
            id=str(uuid.uuid4())[:8], when=At(f.at), action=action,
            origin=ORIGIN_CODE, parent=task, key=f.key,
        ))
    return add, list(remove.values()), keep


def _commit(task: str, state, followups: list[FollowUp]) -> None:
    """Write the gate's state and its trigger changes in one store write, then
    arm and disarm. Unchanged triggers are re-armed too, so a trigger stored
    by a run whose arming failed can't stay unarmed."""
    add, remove, keep = _plan(followups, task)
    store.commit_gate(task, state, add, [t.id for t in remove])
    for t in remove:
        scheduler.disarm(t.id)
        logger.info("Gate %s: cancelled %s (%s)", task, t.id, t.key)
    for t in add + keep:
        scheduler.arm(t)
    for t in add:
        logger.info("Gate %s: scheduled %s (%s) at %s", task, t.id, t.key, t.when.instant.isoformat())


async def evaluate(task: str, gate_name: str) -> bool:
    """Run a gated task's check, and its handler if it fires. True when the
    task is done for this tick (stamp it); False to retry next tick."""
    try:
        check, handler = _CHECKS[gate_name], _HANDLERS[gate_name]
        state = await asyncio.to_thread(store.gate_state, task)
        result = await asyncio.wait_for(asyncio.to_thread(check, state), _CHECK_TIMEOUT_S)
        followups: list[FollowUp] = []
        if result.fire:
            logger.info("Gate %s (%s) fired: %s", task, gate_name, result.message)
            followups = await asyncio.to_thread(handler, result)
        else:
            logger.info("Gate %s (%s): no change", task, gate_name)
        await asyncio.to_thread(_commit, task, result.state, followups)
    except Exception as e:
        count = _failures[task] = _failures.get(task, 0) + 1
        logger.exception("Gate %s (%s) failed (%d in a row)", task, gate_name, count)
        if count == _FAILURE_NOTICE_AFTER:
            await _notify_failing(task, count, e)
        return False
    _failures.pop(task, None)
    return True


async def _notify_failing(task: str, count: int, error: Exception) -> None:
    from gateway.factory import default_outbox
    from gateway.outbox import EVENT_HEARTBEAT

    reason = f"{type(error).__name__}: {str(error)[:200]}" if str(error) else type(error).__name__
    text = (
        f"The automatic check for '{task}' has failed {count} times in a row ({reason}). "
        "It keeps retrying every tick. Until it recovers, new changes won't be picked up; "
        "anything already scheduled still runs."
    )
    result = await default_outbox().notify_owner(
        text, event=EVENT_HEARTBEAT, metadata={"gate_failed": task}
    )
    if not result.ok:
        logger.error("Gate %s: failed to send the failure notice: %s", task, result.error)
