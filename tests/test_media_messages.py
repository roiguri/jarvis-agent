"""Media batch messages: which episodes a several-episode notice names, and when
a batch is sent."""

import asyncio
import logging

import pytest

from gateway.webhook import notifier as notifier_mod
from gateway.webhook.notifier import (
    JELLYFIN_PUBLIC,
    _Batch,
    _episode_list,
    _format_batch_ready_message,
    _format_batch_upgrade_message,
)


def test_episodes_are_listed_ascending_with_runs_as_ranges():
    items = ["S06E09 - I", "S06E01 - A", "S06E02 - B", "S06E03 - C", "S06E05 - E", "S06E06 - F"]
    assert _episode_list(items) == "E01–E03, E05, E06, E09"


def test_an_episode_without_a_number_is_named_after_the_numbered_ones():
    assert _episode_list(["Sayonara", "S06E04 - Circle of Life"]) == "E04, Sayonara"


def test_a_long_list_is_cut_after_ten_entries():
    items = [f"S01E{n:02d} - x" for n in range(1, 25, 2)]   # 12 separate episodes
    assert _episode_list(items) == "E01, E03, E05, E07, E09, E11, E13, E15, E17, E19, +2 more"


def test_a_partial_batch_names_its_episodes():
    batch = _Batch(ready=[("S06E04 - Circle of Life", None), ("S06E03 - Sayonara", None)])
    assert _format_batch_ready_message("Slow Horses__S06", batch) == (
        f"**Slow Horses** — Season 6 — 2 episodes ready on Jellyfin: E03, E04.\nWatch at {JELLYFIN_PUBLIC}")
    batch = _Batch(ready=[("S06E03 - Sayonara", None)], failed=["S06E04"], expected=2)
    assert "1 of 2 episodes ready on Jellyfin: E03. (1 failed)" in _format_batch_ready_message("Slow Horses__S06", batch)


def test_a_complete_season_and_a_single_episode_are_unchanged():
    batch = _Batch(ready=[(f"S02E{n:02d} - x", None) for n in range(1, 5)], expected=4)
    assert "Season 2 is ready on Jellyfin. (4 episodes)" in _format_batch_ready_message("Silo__S02", batch)
    batch = _Batch(ready=[("S03E05 - Radio", None)])
    assert "**Silo** — S03E05 - Radio is ready on Jellyfin." in _format_batch_ready_message("Silo__S03", batch)


def test_an_upgrade_batch_names_its_episodes():
    batch = _Batch(upgraded=[("S01E02", None), ("S01E01", None)])
    assert "2 episodes upgraded to better quality on Jellyfin: E01, E02." in _format_batch_upgrade_message("Silo__S01", batch)


# ---------------------------------------------------------------------------
# Batch timing: a movie waits for Jellyfin's late ItemAdded
# ---------------------------------------------------------------------------


class _Outcome:
    ok = True


class _FakeOutbox:
    def __init__(self):
        self.sent = []

    async def notify_owner(self, text, **kw):
        self.sent.append(text)
        return _Outcome()

    async def notify_owner_media(self, kind, data, caption=None, **kw):
        self.sent.append(caption)
        return _Outcome()


async def _no_image(image_id):
    return None


@pytest.fixture
def manager(monkeypatch):
    monkeypatch.setattr(notifier_mod, "SILENCE_MOVIE", 0.05)
    monkeypatch.setattr(notifier_mod, "SILENCE_MOVIE_EXPECTED", 0.5)
    monkeypatch.setattr(notifier_mod, "_fetch_image", _no_image)
    outbox = _FakeOutbox()

    async def no_llm(prompt):
        return ""

    return notifier_mod.MediaNotificationManager(lambda: outbox, no_llm), outbox


@pytest.mark.asyncio
async def test_a_movie_jellyfin_reports_after_the_short_window_is_still_announced(manager):
    m, outbox = manager
    await m.reset_timer("Movies")                  # Grab
    await m.record_arr_download("Movies", 1)       # Radarr import
    await asyncio.sleep(0.15)                      # past the short window
    assert m.has_pending_download("Movies")
    await m.add_ready_item("Movies", "The Idea of You", image_id="abc")
    assert outbox.sent == [f"**The Idea of You** is ready on Jellyfin.\nWatch at {JELLYFIN_PUBLIC}"]


@pytest.mark.asyncio
async def test_an_expected_movie_that_never_appears_is_logged(manager, caplog, monkeypatch):
    m, outbox = manager
    monkeypatch.setattr(notifier_mod, "SILENCE_MOVIE_EXPECTED", 0.05)
    await m.record_arr_download("Movies", 1)
    with caplog.at_level(logging.WARNING, logger=notifier_mod.logger.name):
        await asyncio.sleep(0.15)
    assert outbox.sent == []
    assert not m.has_pending_download("Movies")
    assert "1 expected and nothing ready" in caplog.text


@pytest.mark.asyncio
async def test_a_movie_upgrade_still_goes_out_after_the_short_window(manager):
    m, outbox = manager
    await m.add_upgrade("Movies", "Troy (2004)")
    await asyncio.sleep(0.15)
    assert len(outbox.sent) == 1 and "upgraded to better quality" in outbox.sent[0]
