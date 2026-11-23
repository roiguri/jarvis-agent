"""Unit tests for eval grading helpers (precision checks)."""

from types import SimpleNamespace

from evals.grade import count_suggestions, grade


def _result(*texts: str):
    return SimpleNamespace(
        error=None,
        calls=[],
        ack=None,
        reply="",
        sends=[{"event": "notification", "text": t} for t in texts],
    )


def test_count_suggestions_counts_top_level_numbered_items():
    note = (
        "🧠 Memory suggestions — 2026-10-15\n\n"
        "**Corrections & preferences**\n\n"
        "1. `USER.md`: add rule.\n"
        "Why: owner said \"always include links\".\n\n"
        "**Facts**\n\n"
        "**2.** `projects.md`: add milestone.\n"
        "  1. indented sub-item should not count.\n"
    )
    assert count_suggestions(note) == 2


def test_count_suggestions_ignores_dates_and_empty_notes():
    assert count_suggestions("") == 0
    assert count_suggestions("Nothing to suggest.") == 0
    assert count_suggestions("🧠 Memory suggestions — 2026-10-15\nUpdated 2026-10-14.") == 0


def test_count_suggestions_falls_back_to_unnumbered_file_items():
    unnumbered = (
        "🧠 Memory suggestions — 2026-11-24\n\n"
        "Corrections & preferences\n"
        "USER.md: Add \"Always include the source URL.\"\n"
        "Owner asked for URLs.\n\n"
        "Facts\n"
        "`fitness_plan.md`: Add \"Owns a coffee grinder.\"\n"
        "Mentioned in passing.\n"
    )
    assert count_suggestions(unnumbered) == 2


def test_max_suggestions_passes_within_bound_and_fails_above():
    one = "🧠 Memory suggestions\n\n1. `USER.md`: add URL rule.\nWhy: owner asked."
    three = (
        "🧠 Memory suggestions\n\n"
        "1. `USER.md`: add URL rule.\n"
        "2. `USER.md`: reorganize bullets.\n"
        "3. `book_list.md`: archive finished books.\n"
    )
    ok_checks = grade({"sends": 1, "max_suggestions": 1, "message_contains": ["URL"]}, _result(one))
    assert all(ok for _, ok, _ in ok_checks)

    bad_checks = grade({"sends": 1, "max_suggestions": 1, "message_contains": ["URL"]}, _result(three))
    by_name = {name: ok for name, ok, _ in bad_checks}
    assert by_name["message contains 'URL'"] is True
    assert by_name["suggestions <= 1"] is False
