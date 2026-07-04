# Jarvis — Operational & Development Runbook

This file is the **deploy / ops / local-dev runbook**: how to run, configure,
deploy, and troubleshoot Jarvis on its host. It deliberately does **not**
re-describe the architecture or the agent runtime.

- **Architecture** (gateway planes, memory layers, agent loop) → the source of
  truth is [docs/architecture/GATEWAY.md](docs/architecture/GATEWAY.md),
  [docs/architecture/MEMORY.md](docs/architecture/MEMORY.md),
  [docs/architecture/RUNTIME.md](docs/architecture/RUNTIME.md).
- **Repo layout, placement principle, "where do I add X"** → [CLAUDE.md](CLAUDE.md).
- **Claude Code conventions / hard constraints** → [CLAUDE.md](CLAUDE.md).

---

## Configuration

All secrets live in `/app/secrets/.env` (outside the repo root, never committed —
see `.env.example`). **Never read `/app/secrets/.env`**; the variable names below
are sufficient.

```env
# Core
GOOGLE_API_KEY=...
TELEGRAM_BOT_TOKEN=...
ALLOWED_USER_ID=...            # Numeric Telegram user ID; single-user whitelist

# Media services (query tools)
SONARR_URL=http://sonarr.local:8989
SONARR_API_KEY=...
RADARR_URL=http://radarr.local:7878
RADARR_API_KEY=...
PROWLARR_URL=http://prowlarr.local:9696
PROWLARR_API_KEY=...
JELLYSEERR_URL=http://jellyseerr.local:5055
JELLYSEERR_API_KEY=...

# Web search
TAVILY_API_KEY=...                 # Free tier: 1,000 searches/month, no card required

# GitHub project management
GITHUB_TOKEN=...                   # Classic PAT, 'repo' scope (read/write issues & PRs)

# Arbox gym API
ARBOX_ACCESS_TOKEN=...             # JWT captured from the Arbox app; long-lived. Renew on 401.
ARBOX_WHITELABEL=...               # Gym's app brand identifier (request header)
ARBOX_BOX_ID=...                   # Gym internal ID
ARBOX_LOCATIONS_BOX_ID=...         # Location ID
ARBOX_MEMBERSHIP_USER_ID=...       # Required for class registration

# Google Health API (Pixel Watch)
GOOGLE_HEALTH_CLIENT_ID=...        # OAuth client (Desktop app)
GOOGLE_HEALTH_CLIENT_SECRET=...    # OAuth client secret
GOOGLE_HEALTH_REFRESH_TOKEN=...    # minted once — see tools/google_health/SETUP.md
```

Sonarr/Radarr/Prowlarr API keys: **Settings → General → API Key** in each web UI.
Jellyseerr: **Settings → General → API Key**. Tavily: sign up at
https://app.tavily.com — free "Researcher" plan, 1,000 credits/month, 1 credit per
search, no card. On quota exhaustion `web_search` returns a graceful error telling
Jarvis to fall back to training knowledge rather than failing.

GitHub: generate a **classic** Personal Access Token at GitHub → Settings →
Developer settings → Tokens (classic), with the **repo** scope. Without
`GITHUB_TOKEN` the github-skill tools return a graceful "not configured"
message; reads are autonomous, writes are Telegram-confirmation gated.

Google Health (Pixel Watch sleep/workouts/HR/HRV): full setup — GCP project,
OAuth consent (External, publish to *In production*, unverified is fine for a
single personal user), Desktop OAuth client, minting the refresh token, and
the terminal sanity-check — lives in **`tools/google_health/SETUP.md`**.
Without `GOOGLE_HEALTH_*` env vars the tools return a graceful "not
configured" message; on token revocation they return an actionable re-auth
message instead of failing silently.

### Runtime constants (defined in source, not env vars)

| Constant | File | Value | Notes |
|---|---|---|---|
| `DB_PATH` | `agent.py` | `/app/jarvis_memory/threads.sqlite` | Conversation state — LangGraph owns the path; deny-listed from memory tools |
| `MAX_MESSAGES` | `agent.py` | `50` | Sliding-window size per checkpoint (~25 exchanges) |
| `LLM_MODEL` | `agent.py` | `gemini-3-flash-preview` | Update when upgrading the model |
| `LLM_TEMPERATURE` | `agent.py` | `0.2` | Low = deterministic tool use |
| `LOG_RETENTION_DAYS` | `tools/core/history.py` | `90` | Chat + notification log retention |
| `_LOG_DIR` | `tools/core/history.py` | `/app/jarvis_data/logs` | Chat + notification JSONL location |
| `MEMORY_DIR` | `tools/core/memory.py` | `/app/jarvis_memory` | Long-term memory sandbox root |
| `JELLYFIN_INTERNAL_URL` | `gateway/webhook/notifier.py` | `http://jellyfin.local:8096` | Poster fetch endpoint (env-overridable) |
| `SILENCE_SERIES` / `SILENCE_MOVIE` | `gateway/webhook/notifier.py` | `600` / `120` (s) | Notification fallback timers |
| `HEARTBEAT_INTERVAL_HOURS` | `main.py` | `1` | Heartbeat agent-turn cadence |
| `HEARTBEAT_THREAD_ID` | `heartbeat.py` | `"heartbeat"` | Shared thread for all scheduled turns |
| `EVENTS_PATH` | `tools/core/scheduling.py` | `/app/jarvis_data/scheduling/scheduled_events.json` | Pending reminders across restarts |
| `DB_PATH` (fitness) | `tools/fitness/fitness_tools.py` | `/app/jarvis_data/fitness/fitness.sqlite` | Fitness-skill DB |
| `_HEARTBEAT_MD_PATH` | `agent.py` | `/app/jarvis_memory/HEARTBEAT.md` | Injected into heartbeat-scope prompt |
| `_AGENTS_PATH` / `_HEARTBEAT_PROMPT_PATH` | `agent.py` | `/app/jarvis_code/prompts/AGENTS.md` / `heartbeat.md` | Dev-controlled prompt content |

---

## Systemd Service

Runs as unprivileged `jarvis_user` under systemd on LXC 106.
Service file (inside the container): `/etc/systemd/system/jarvis.service`.

```ini
[Unit]
Description=Jarvis Telegram Agent
After=network.target

[Service]
Type=simple
User=jarvis_user
Group=jarvis_user
WorkingDirectory=/app/jarvis_code
ExecStart=/app/jarvis_code/venv/bin/python3 /app/jarvis_code/main.py
Restart=always
RestartSec=30
StartLimitIntervalSec=300
StartLimitBurst=5
TimeoutStopSec=30
StandardOutput=journal
StandardError=journal
Environment="PATH=/app/jarvis_code/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

[Install]
WantedBy=multi-user.target
```

Restart-tuning rationale (changes from a stock unit):
- `RestartSec=30` — 30s cooldown between restarts (was 5s).
- `StartLimitIntervalSec=300` + `StartLimitBurst=5` — systemd gives up after
  5 crashes in 5 min instead of looping forever; prevents CPU exhaustion during
  a network outage.
- `TimeoutStopSec=30` — `systemctl stop` won't hang on a stuck process.

Common commands (Proxmox host shell):

```bash
pct exec 106 -- journalctl -u jarvis.service -f      # live logs
pct exec 106 -- systemctl restart jarvis.service     # deploy a code change
pct exec 106 -- systemctl status jarvis.service      # status
pct exec 106 -- systemctl stop jarvis.service        # stop
```

---

## Firewall Posture

The container runs behind a strict **default-deny** firewall in both directions
(enforced at the host). Only the flows Jarvis needs are opened:

- **Outbound:** the media-service API ports (Sonarr/Radarr/Prowlarr/Jellyseerr),
  the Jellyfin poster endpoint, DNS, and HTTP/HTTPS (for the Gemini and Telegram
  APIs).
- **Inbound:** the webhook port (8000) from the media hosts only, plus SSH from
  the LAN.

Concrete host addresses and the exact rule syntax are environment-specific and
live in the private deployment config, not in this repo.

---

## Development Workflow

1. **Edit** code at `/app/jarvis_code` inside LXC 106 (directly or synced from the host).
2. **Test locally without Telegram** — the agent has a built-in REPL:
   ```bash
   cd /app/jarvis_code && source venv/bin/activate && python3 agent.py
   ```
   Drops into an interactive loop on thread_id `local_dev_test_01` (isolated from
   the live `telegram_<user_id>` / `heartbeat` threads).
3. **Deploy** — restart the service, then watch logs:
   ```bash
   pct exec 106 -- systemctl restart jarvis.service
   pct exec 106 -- journalctl -u jarvis.service -f
   ```
   Type-checking and the REPL don't catch LLM-behavior regressions — after any
   change, send a real Telegram message to verify.
4. **New Python deps** — install then re-freeze so the env stays reproducible:
   ```bash
   pct exec 106 -- bash -c 'source /app/jarvis_code/venv/bin/activate && pip install <pkg>'
   pct exec 106 -- bash -c 'source /app/jarvis_code/venv/bin/activate && pip freeze > /app/jarvis_code/requirements.txt'
   ```
   Rebuild from scratch: `pip install -r requirements.txt`.

> Adding a tool/skill/channel is an architecture concern, not an ops one — see
> [CLAUDE.md](CLAUDE.md) "Key Files to Know" and
> [docs/architecture/RUNTIME.md](docs/architecture/RUNTIME.md).

---

## Operational Internals & Gotchas

Non-obvious runtime behavior that isn't derivable from the architecture docs.

### Reminder persistence

`manage_reminder(action='create')` writes `scheduled_events.json` atomically and
creates an APScheduler `DateTrigger` job immediately. On service restart `main.py`
re-reads the file and re-creates every pending job; **past-due reminders fire
immediately** via `asyncio.create_task` with a staleness annotation. A fired
reminder is removed from the file.

### Notification batch aggregation (`gateway/webhook/notifier.py`)

Each download batch is keyed `"{SeriesTitle}__S{NN}"` (episodes) or `"Movies"`:
1. Arr `Download` event → `record_arr_download(key, count)` sets `expected`.
2. Jellyfin `ItemAdded` → `add_ready_item(key, name)` increments `ready`.
3. `ready >= expected > 0` → **immediate dispatch**.
4. Otherwise a **silence-timer fallback** fires: series `SILENCE_SERIES` (10 min),
   movies `SILENCE_MOVIE` (2 min).
5. Jellyfin-before-Arr is handled — `expected` is re-checked on every
   `record_arr_download` call.

### Checkpoint storage is permanently O(1) per thread

Two cooperating subclasses in `agent.py` bound `threads.sqlite`:
- **`JarvisState`** overrides the `messages` reducer with `_add_and_trim`, so a
  checkpoint never holds more than `MAX_MESSAGES` (50) messages — enforced at the
  state-schema level, no runtime code in `ask_jarvis`.
- **`PruningSqliteSaver`** overrides `put()` to delete all older rows for the
  thread (in `checkpoints` and `writes`) right after writing — exactly one
  checkpoint per thread, enforced at the storage layer.

**Media blob stripping:** `_strip_media_blobs()` runs on `existing` messages in
the reducer before re-store — base64 image blobs become `[image attached]`,
audio/video blobs are dropped (the separate text hint `ask_jarvis` appends is
preserved). New messages keep blobs for the current turn so the LLM can process
them, then are stripped on the next reducer call.

Anything outside the 50-message window is **not lost for audit** — every turn is
appended to `chat_history.jsonl` before `ask_jarvis` runs and is queryable via
`get_chat_history`. Disk-footprint hygiene (WAL high-water mark, un-VACUUMed
pages) is tracked in issue #24.

### SQLite `check_same_thread=False`

`SqliteSaver` is opened with `check_same_thread=False` so the async gateway can
hand work to a thread pool; safe for this single-writer workload. WAL mode is
enabled automatically by `SqliteSaver.setup()` — the `.sqlite-wal` / `.sqlite-shm`
sidecars are normal and expected.

### Single-user whitelist (extension point)

`ALLOWED_USER_ID` is exactly one numeric ID, read once in `main.py` and passed to
`TelegramChannel`; authorization is `TelegramChannel.authorize()`
(`gateway/channels/telegram/channel.py`), called by the router. To support
multiple users, switch to a comma-separated env var and widen `authorize()`:

```python
ALLOWED_IDS = set(os.getenv("ALLOWED_USER_IDS", "").split(","))
if ALLOWED_IDS and str(user_id) not in ALLOWED_IDS:
    ...
```

---

## Security Notes

- Secrets load from `/app/secrets/.env`, outside the repo root — never relocate
  them into `/app/jarvis_code/`, never read the file.
- Memory tool paths are validated in `_get_safe_path()` (`tools/core/memory.py`).
  The check uses `startswith(MEMORY_DIR + os.sep)`, which also blocks
  sibling-directory attacks (e.g. `/app/jarvis_memory_evil/`), and deny-lists
  `threads.sqlite*`.
- Unauthorized Telegram users are silently ignored (no reply; attempt logged).
- The service runs as unprivileged `jarvis_user`, not root.
