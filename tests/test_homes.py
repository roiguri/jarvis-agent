"""Tests for homes.py: SKILL.md stores discovery, live providers, fail-open, and caps."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import heartbeat_state
import homes
from tools import registry
from tools.collections import _db as collections_db
from tools.travel import _db as travel_db
from triggers import store as trigger_store
from triggers.model import At, Send, Trigger


def test_skill_stores_discovered_and_hidden_from_chat_skill_block():
    summary = homes.summarize()
    for name in ("collections", "cookbook", "files", "fitness", "heartbeat", "media", "travel", "triggers"):
        assert f"- {name}:" in summary
    for stateless in ("web", "google_health", "github"):
        assert f"- {stateless}:" not in summary

    block = registry.compact_skill_list("user", registry.skill_namespaces())
    assert "stores:" not in block


def test_live_providers_render_collections_travel_heartbeat_and_triggers(monkeypatch, tmp_path):
    monkeypatch.setattr(collections_db, "DB_PATH", str(tmp_path / "collections.sqlite"))
    collections_db._init_db()
    monkeypatch.setattr(travel_db, "DB_PATH", str(tmp_path / "travel.sqlite"))
    travel_db._init_db()
    monkeypatch.setattr(trigger_store, "STORE_PATH", str(tmp_path / "triggers.json"))

    # Collections: 1 active, 1 archived
    c_conn = collections_db._get_db()
    try:
        c_conn.execute(
            "INSERT INTO collections (name, schema, archived_at) VALUES (?, ?, NULL)",
            ("Reading List", '{"status": null, "sections": false, "fields": []}'),
        )
        c_conn.execute(
            "INSERT INTO collections (name, schema, archived_at) VALUES (?, ?, datetime('now'))",
            ("Old Archive", '{"status": null, "sections": false, "fields": []}'),
        )
        c_conn.commit()
    finally:
        c_conn.close()

    # Travel: 1 draft trip, 1 archived trip, 1 destination
    t_conn = travel_db._get_db()
    try:
        cur = t_conn.execute(
            "INSERT INTO destinations (name, kind, timezone) VALUES (?, ?, ?)",
            ("Portugal", "country", "Europe/Lisbon"),
        )
        dest_id = cur.lastrowid
        t_conn.execute(
            "INSERT INTO trips (trip_id, title, destination_id, start_date, end_date, status, is_current) "
            "VALUES (?, ?, ?, ?, ?, 'draft', 0)",
            ("lisbon-2026", "Lisbon", dest_id, "2026-12-01", "2026-12-08"),
        )
        t_conn.execute(
            "INSERT INTO trips (trip_id, title, destination_id, start_date, end_date, status, is_current) "
            "VALUES (?, ?, ?, ?, ?, 'archived', 0)",
            ("old-trip", "Old Trip", dest_id, "2025-01-01", "2025-01-05"),
        )
        t_conn.commit()
    finally:
        t_conn.close()

    # Heartbeat tasks
    hb = tmp_path / "HEARTBEAT.md"
    hb.write_text(
        "# Heartbeat\n"
        "- **daily-readiness** | every 24h | due: 07:00-10:00\n"
        "- **gym-booking** | every 24h | paused\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(heartbeat_state, "HEARTBEAT_PATH", str(hb))

    # Pending trigger
    trigger_store.add(Trigger(
        id="t-home-1",
        when=At(instant=datetime.now(timezone.utc) + timedelta(hours=2)),
        action=Send(text="call dentist"),
    ))

    summary = homes.summarize()
    assert "Reading List" in summary and "Old Archive" not in summary
    assert "Lisbon (2026-12-01..2026-12-08)" in summary and "Old Trip" not in summary
    assert "destinations: Portugal" in summary
    assert "daily-readiness" in summary and "gym-booking (paused)" in summary
    assert "1 pending" in summary


def test_provider_failure_fails_open_to_static_description(monkeypatch):
    def broken():
        raise RuntimeError("db locked")

    monkeypatch.setitem(
        homes._PROVIDERS,
        "broken_home",
        homes.HomeProvider("broken_home", broken, fallback="static fallback text"),
    )
    summary = homes.summarize(load_defaults=False)
    assert "- broken_home: static fallback text" in summary


def test_cap_labels_and_line_truncation(monkeypatch):
    labels = [f"item-{i}" for i in range(25)]
    capped = homes.cap_labels(labels)
    assert "item-0" in capped and "item-19" in capped
    assert "item-20" not in capped and "+5 more" in capped

    monkeypatch.setitem(
        homes._PROVIDERS,
        "verbose_home",
        homes.HomeProvider("verbose_home", lambda: "x" * 500, fallback="desc"),
    )
    summary = homes.summarize(load_defaults=False)
    line = next(l for l in summary.splitlines() if l.startswith("- verbose_home:"))
    body = line.split(": ", 1)[1]
    assert len(body) <= homes.MAX_LINE_CHARS and body.endswith("…")
