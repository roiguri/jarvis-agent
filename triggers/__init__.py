"""Triggers — the one scheduler for timed work.

A trigger is *when* + an *action*: today, a one-shot instant that sends fixed
text (a reminder). The store is code-owned and outside the memory sandbox;
the scheduler arms each trigger as an APScheduler job; the runner executes it.
See docs/architecture/TRIGGERS.md.
"""
