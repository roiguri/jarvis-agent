"""
The jarvis-app channel — Jarvis's second channel, beside Telegram.

Same Channel contract, but the two differ in ways worth knowing when reading this
package next to gateway/channels/telegram/:

- Who drives the connection. Telegram is push: python-telegram-bot owns an inbound
  Application and Telegram delivers updates to it (host.py wraps the PTB lifecycle).
  jarvis-app is pull — *we* are the client: router.py long-polls the hub's
  GET /bot/v1/updates ourselves, with no third-party library owning the loop.

- Identity. Telegram is a multi-user surface: every update carries from_user.id and
  the channel authorizes against ALLOWED_USER_ID. The hub is one-bot-one-owner: the
  bot token scopes the single owner, updates carry no per-message user id, and
  authorization is already done upstream — so there is one conversation and chat_id
  is not a routing key (every send addresses the owner).

- Availability. Telegram's servers are assumed up and PTB handles reconnection. The
  hub is a home-grown service that may be down or still in development, so the router
  has explicit degraded mode (log once, back off 1->60s) and never crashes the agent
  — and main.py builds this channel only when APP_HUB_URL is set.

- Contract. Telegram is a stable public API; the hub is a home-grown wire contract
  pinned by contract_version (client.py), against which the client warns on mismatch.

- Rendering. Telegram renders Markdown to HTML and uploads media as multipart photos.
  jarvis-app sends plain text for now; media (upload-then-reference attachments) and
  rich blocks are later steps, gated on what the phone renders.

Everything else — slash commands, chat-history logging, the shared on_message path —
is channel-agnostic and reused unchanged.
"""
