"""Media batch messages: which episodes a several-episode notice names."""

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
