#!/usr/bin/env python3
"""
Webhook test script — fires all event types in one sequential run.

Usage:
    python test_webhooks.py [--host HOST] [--port PORT] [--delay SECS]

    python test_webhooks.py                          # run everything
    python test_webhooks.py --host jarvis.local      # against the deployed host
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# Jellyfin image IDs
# Find these in the Jellyfin web UI: browse to the item and copy the ID from
# the URL  (e.g. /web/#/details?id=<guid>)
# ---------------------------------------------------------------------------

SERIES_IMAGE_ID = "14f6bb82-b2fb-e404-a7e7-93556867a9f9"  # Silo — confirmed working
MOVIE_IMAGE_ID  = "b66d2a1c-46bd-6f97-d9ed-68f5fdc6a141"  # My Policeman — confirmed working

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def post(url: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read())
            return resp.status, body
    except urllib.error.HTTPError as e:
        raw = e.read() if e.fp else b""
        try:
            body = json.loads(raw) if raw.strip() else {"error": f"HTTP {e.code} (empty body)"}
        except json.JSONDecodeError:
            body = {"error": f"HTTP {e.code}", "raw": raw.decode(errors="replace")[:300]}
        return e.code, body
    except Exception as e:
        return 0, {"error": str(e)}


def send(label: str, endpoint: str, payload: dict, host: str, port: int) -> None:
    url = f"http://{host}:{port}{endpoint}"
    status, body = post(url, payload)
    ok = "OK " if 200 <= status < 300 else "ERR"
    print(f"  [{ok} {status}] {label}")
    print(f"            → {json.dumps(body)}")


def note(msg: str) -> None:
    print(f"  ℹ  {msg}")


# ---------------------------------------------------------------------------
# Payload factories
# ---------------------------------------------------------------------------

def sonarr_download(series: str, season: int, episodes: list[tuple[int, str]],
                    is_upgrade: bool = False) -> dict:
    return {
        "eventType": "Download",
        "isUpgrade": is_upgrade,
        "series": {"title": series},
        "episodes": [
            {"seasonNumber": season, "episodeNumber": n, "title": t}
            for n, t in episodes
        ],
    }


def sonarr_grab(series: str, season: int, episodes: list[tuple[int, str]]) -> dict:
    return {
        "eventType": "Grab",
        "series": {"title": series},
        "episodes": [
            {"seasonNumber": season, "episodeNumber": n, "title": t}
            for n, t in episodes
        ],
    }


def sonarr_failure(series: str, season: int, episodes: list[tuple[int, str]]) -> dict:
    return {
        "eventType": "DownloadFailed",
        "series": {"title": series},
        "episodes": [
            {"seasonNumber": season, "episodeNumber": n, "title": t}
            for n, t in episodes
        ],
    }


def radarr_download(title: str, year: int, is_upgrade: bool = False) -> dict:
    return {
        "eventType": "Download",
        "isUpgrade": is_upgrade,
        "movie": {"title": title, "year": year},
    }


def radarr_failure(title: str, year: int) -> dict:
    return {"eventType": "DownloadFailed", "movie": {"title": title, "year": year}}


def jellyfin_episode(series: str, name: str, season: int,
                     episode_num: int | None = None,
                     series_id: str = "placeholder-series-id") -> dict:
    p = {
        "NotificationType": "ItemAdded",
        "Name": name,
        "SeriesName": series,
        "SeasonNumber": season,
        "SeriesId": series_id,
        "ItemId": f"item-{name.replace(' ', '-').lower()}",
    }
    if episode_num is not None:
        p["IndexNumber"] = episode_num
    return p


def jellyfin_movie(title: str, item_id: str = "placeholder-movie-id") -> dict:
    return {
        "NotificationType": "ItemAdded",
        "Name": title,
        "ItemId": item_id,
    }


def jellyfin_season_container(series: str, season: int) -> dict:
    return {
        "NotificationType": "ItemAdded",
        "Name": f"Season {season}",
        "SeriesName": series,
        "SeasonNumber": season,
        "SeriesId": "placeholder-series-id",
    }


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run_all(host: str, port: int, delay: float) -> None:
    arr       = "/webhook/arr"
    jf        = "/webhook/jellyfin"
    series_id = SERIES_IMAGE_ID
    movie_id  = MOVIE_IMAGE_ID

    def section(title: str) -> None:
        bar = "─" * 60
        print(f"\n{bar}\n  {title}\n{bar}")

    using_real_images = (
        series_id != "placeholder-series-id" or
        movie_id  != "placeholder-movie-id"
    )
    if using_real_images:
        note(f"Image IDs: series={series_id!r}  movie={movie_id!r}")
    else:
        note("Image IDs are placeholders — fetch will 400 gracefully, notifications send as text.")

    # ------------------------------------------------------------------
    # 1. Routing sanity
    # ------------------------------------------------------------------
    section("1. Routing — Test + skip events (no Telegram output expected)")
    send("Arr Test event", arr, {"eventType": "Test"}, host, port)
    for ev in ["Rename", "MovieAdded", "MovieDelete", "MovieFileDelete",
               "MovieFileDeletedForUpgrade", "SeriesDelete",
               "EpisodeFileDelete", "EpisodeFileDeletedForUpgrade"]:
        send(f"Skip: {ev}", arr, {"eventType": ev}, host, port)

    # ------------------------------------------------------------------
    # 2. Regression: series title with __
    # ------------------------------------------------------------------
    section("2. Regression — Series title contains __ (rsplit fix)")
    note("Expected: one Telegram message for Dark__Matter S01E01")
    send("Sonarr Download Dark__Matter S01E01", arr,
         sonarr_download("Dark__Matter", 1, [(1, "Pilot")]), host, port)
    time.sleep(delay)
    send("Jellyfin ItemAdded Dark__Matter E01 → dispatch", jf,
         jellyfin_episode("Dark__Matter", "Pilot", 1,
                          episode_num=1, series_id=series_id), host, port)

    time.sleep(delay * 2)

    # ------------------------------------------------------------------
    # 3. Regression: Season 0
    # ------------------------------------------------------------------
    section("3. Regression — SeasonNumber=0 (must key to 'Silo__S00', not 'Movies')")
    note("Expected in response body: \"key\": \"Silo__S00\"  (no Telegram — timer-based)")
    send("Jellyfin ItemAdded Season 0", jf, {
        "NotificationType": "ItemAdded",
        "Name": "Silo Special",
        "SeriesName": "Silo",
        "SeasonNumber": 0,
        "SeriesId": series_id,
        "ItemId": "item-special-001",
    }, host, port)

    time.sleep(delay)

    # ------------------------------------------------------------------
    # 4. Single episode with image
    # ------------------------------------------------------------------
    section("4. Single episode — Arr → Jellyfin → immediate dispatch (with image)")
    note("Expected: one Telegram message naming S02E05 and the episode title")
    send("Sonarr Download Silo S02E05 (expected=1)", arr,
         sonarr_download("Silo", 2, [(5, "The Janitor's Boy")]), host, port)
    time.sleep(delay)
    send("Jellyfin ItemAdded S02E05 → dispatch", jf,
         jellyfin_episode("Silo", "The Janitor's Boy", 2,
                          episode_num=5, series_id=series_id), host, port)

    time.sleep(delay * 2)

    # ------------------------------------------------------------------
    # 5. Season pack with image
    # ------------------------------------------------------------------
    section("5. Season pack — 4 episodes, Arr → Jellyfin × 4 → immediate dispatch (with image)")
    note("Expected: one Telegram message — 'Silo Season 2 — 4 episodes ready'")
    send("Sonarr Download S02E01-E04 (expected=4)", arr,
         sonarr_download("Silo", 2, [
             (1, "Rotten Copper"), (2, "Painted Walls"),
             (3, "The Conspiracy"), (4, "Truth"),
         ]), host, port)
    time.sleep(delay)
    for ep_num, ep_title in [(1, "Rotten Copper"), (2, "Painted Walls"),
                              (3, "The Conspiracy"), (4, "Truth")]:
        send(f"Jellyfin ItemAdded S02E0{ep_num}", jf,
             jellyfin_episode("Silo", ep_title, 2,
                              episode_num=ep_num, series_id=series_id), host, port)
        time.sleep(delay)

    time.sleep(delay * 2)

    # ------------------------------------------------------------------
    # 6. Jellyfin fires before Arr
    # ------------------------------------------------------------------
    section("6. Jellyfin-first — Jellyfin arrives before Arr Download")
    note("Expected: one Telegram message for Dark S01E01 after the second request")
    send("Jellyfin ItemAdded first (expected=0, timer starts)", jf,
         jellyfin_episode("Dark", "The Passage", 1,
                          episode_num=1, series_id=series_id), host, port)
    time.sleep(delay)
    send("Sonarr Download arrives → expected=1, ready=1 → dispatch", arr,
         sonarr_download("Dark", 1, [(1, "The Passage")]), host, port)

    time.sleep(delay * 2)

    # ------------------------------------------------------------------
    # 7. Movie with image
    # ------------------------------------------------------------------
    section("7. Movie — Radarr → Jellyfin → immediate dispatch (with image)")
    note("Expected: one Telegram message for Dune: Part Two")
    send("Radarr Download Dune: Part Two (expected=1)", arr,
         radarr_download("Dune: Part Two", 2024), host, port)
    time.sleep(delay)
    send("Jellyfin ItemAdded movie → dispatch", jf,
         jellyfin_movie("Dune: Part Two", item_id=movie_id), host, port)

    time.sleep(delay * 2)

    # ------------------------------------------------------------------
    # 8. Quality upgrade
    # ------------------------------------------------------------------
    section("8. Quality upgrade — timer-based (fires after 10 min)")
    note("Expected: Telegram message for Severance after 10 min silence timer")
    send("Sonarr Download isUpgrade=True", arr,
         sonarr_download("Severance", 2, [(3, "Goodbye, Mrs. Selvig")],
                         is_upgrade=True), host, port)

    time.sleep(delay)

    # ------------------------------------------------------------------
    # 9. Download failures
    # ------------------------------------------------------------------
    section("9. Failures — episode + movie (timer-based)")
    note("Expected: failure notifications after silence timers")
    send("Sonarr DownloadFailed S01E02", arr,
         sonarr_failure("Silo", 1, [(2, "Holston's Pick")]), host, port)
    time.sleep(delay)
    send("Radarr DownloadFailed", arr,
         radarr_failure("Alien: Romulus", 2024), host, port)

    time.sleep(delay)

    # ------------------------------------------------------------------
    # 10. Season container skip
    # ------------------------------------------------------------------
    section("10. Season container — must be silently ignored")
    note("Expected: status=ignored in response, no Telegram message")
    send("Jellyfin 'Season 2' container (skip)", jf,
         jellyfin_season_container("Silo", 2), host, port)
    time.sleep(delay)
    send("Jellyfin 'Season 0' container (skip)", jf,
         jellyfin_season_container("Silo", 0), host, port)

    time.sleep(delay)

    # ------------------------------------------------------------------
    # 11. Concurrent series — independent batches
    # ------------------------------------------------------------------
    section("11. Concurrent series — two shows downloading at the same time")
    note("Expected: two separate Telegram messages, one per series")
    send("Sonarr Download Silo S02E01 (expected=1)", arr,
         sonarr_download("Silo", 2, [(1, "Rotten Copper")]), host, port)
    send("Sonarr Download The Bear S03E01 (expected=1)", arr,
         sonarr_download("The Bear", 3, [(1, "Premiere")]), host, port)
    time.sleep(delay)
    send("Jellyfin Silo E01 → Silo batch dispatches", jf,
         jellyfin_episode("Silo", "Rotten Copper", 2,
                          episode_num=1, series_id=series_id), host, port)
    time.sleep(delay)
    send("Jellyfin The Bear E01 → The Bear batch dispatches", jf,
         jellyfin_episode("The Bear", "Premiere", 3,
                          episode_num=1, series_id=series_id), host, port)

    time.sleep(delay * 2)

    # ------------------------------------------------------------------
    # 12. System alerts — LLM path
    # ------------------------------------------------------------------
    section("12. System alerts — dispatched immediately via LLM")
    note("Expected: two Telegram messages (health issue, then restored)")
    send("HealthIssue", arr,
         {"eventType": "HealthIssue",
          "message": "Indexer unavailable: NZBgeek",
          "wikiUrl": "https://wiki.servarr.com/sonarr"}, host, port)
    time.sleep(delay)
    send("HealthRestored", arr,
         {"eventType": "HealthRestored",
          "message": "Indexer NZBgeek is available again"}, host, port)
    time.sleep(delay)
    send("ApplicationUpdate", arr,
         {"eventType": "ApplicationUpdate",
          "message": "Sonarr updated to v4.0.1"}, host, port)
    time.sleep(delay)
    send("ManualInteractionRequired", arr,
         {"eventType": "ManualInteractionRequired",
          "message": "Manual import required for Silo S03E01"}, host, port)

    time.sleep(delay)

    # ------------------------------------------------------------------
    # 13. Unknown events — LLM path
    # ------------------------------------------------------------------
    section("13. Unknown events — forwarded via LLM")
    note("Expected: two Telegram messages noting unexpected events")
    send("Unknown Arr event", arr,
         {"eventType": "SomeNewFutureEvent", "details": "future payload"}, host, port)
    time.sleep(delay)
    send("Unknown Jellyfin event (PlaybackStart)", jf,
         {"NotificationType": "PlaybackStart", "Name": "Silo", "UserId": "user-001"},
         host, port)

    # ------------------------------------------------------------------
    # 14. Mixed ready + failed — immediate dispatch when all accounted for
    # ------------------------------------------------------------------
    section("14. Mixed ready+failed — immediate dispatch when ready+failed == expected")
    note("Expected: ONE Telegram message — 'Stranger Things S04 — 3 of 4 episodes ready (1 failed)'")
    note("Dispatch triggered by the DownloadFailed event, not a timer")
    send("Sonarr Download S04E01-E04 (expected=4)", arr,
         sonarr_download("Stranger Things", 4, [
             (1, "Chapter One: The Hellfire Club"), (2, "Chapter Two: Vecna's Curse"),
             (3, "Chapter Three: The Monster and the Superhero"), (4, "Chapter Four: Dear Billy"),
         ]), host, port)
    time.sleep(delay)
    for ep_num, ep_title in [
        (1, "Chapter One: The Hellfire Club"),
        (2, "Chapter Two: Vecna's Curse"),
        (3, "Chapter Three: The Monster and the Superhero"),
    ]:
        send(f"Jellyfin ItemAdded S04E0{ep_num}", jf,
             jellyfin_episode("Stranger Things", ep_title, 4,
                              episode_num=ep_num, series_id=series_id), host, port)
        time.sleep(delay)
    send("Sonarr DownloadFailed S04E04 → ready=3 + failed=1 = 4 → dispatch", arr,
         sonarr_failure("Stranger Things", 4, [(4, "Chapter Four: Dear Billy")]), host, port)

    time.sleep(delay * 2)

    # ------------------------------------------------------------------
    # 15. Multi-episode upgrade summary
    # ------------------------------------------------------------------
    section("15. Multi-episode upgrade — 2 upgrades same season (timer-based, fires after 10 min)")
    note("Expected after 10 min: ONE Telegram message — 'The Wire S01 — 2 episodes upgraded'")
    send("Sonarr Download isUpgrade=True S01E01", arr,
         sonarr_download("The Wire", 1, [(1, "The Target")], is_upgrade=True), host, port)
    time.sleep(delay)
    send("Sonarr Download isUpgrade=True S01E02", arr,
         sonarr_download("The Wire", 1, [(2, "The Detail")], is_upgrade=True), host, port)

    time.sleep(delay)

    print("\n" + "─" * 60)
    print("  All sections complete.")
    if not using_real_images:
        print("  Re-run with --series-id and --movie-id to test image delivery.")
    print("─" * 60 + "\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host",  default="localhost")
    parser.add_argument("--port",  type=int,   default=8000)
    parser.add_argument("--delay", type=float, default=1.5,
                        help="Seconds between steps (default: 1.5)")
    args = parser.parse_args()

    print(f"\nTarget: http://{args.host}:{args.port}  delay={args.delay}s")
    run_all(args.host, args.port, args.delay)


if __name__ == "__main__":
    main()
