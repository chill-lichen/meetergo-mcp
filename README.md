# meetergo-mcp

A community [Model Context Protocol](https://modelcontextprotocol.io) server that turns the
**[meetergo](https://www.meetergo.com) scheduling + CRM API** into tools an AI assistant
(Claude Desktop, or any MCP client) can call.

meetergo is a privacy-first, GDPR-friendly scheduler that — unlike most Calendly-style tools —
**syncs natively with Proton Calendar** (and iCloud, CalDAV, Outlook, Google). This server lets
you drive a Proton-backed scheduling + light-CRM stack entirely through an AI assistant, with no
Google Workspace required.

> **⚠️ Disclaimer.** Independent community project — **not affiliated with, endorsed by, or
> supported by meetergo**. It was largely "vibe-coded" with an AI assistant against meetergo's
> public API docs. Provided **as is**, no warranty (see [LICENSE](LICENSE)). Review the code,
> test against your own account, and use at your own risk. Every booking created via the API is
> **metered/billed** by meetergo.

## What it can do (37 tools)

| Area | Tools |
|---|---|
| Account | `get_me` |
| Booking pages & links | `list_meeting_types`, `get_meeting_type`, `create_meeting_type`, `update_meeting_type`, `create_one_time_booking_link` |
| Page branding (colors/graphics) | `get_personal_page`, `update_personal_page` |
| Availability | `get_availability` |
| Bookings | `create_booking`, `list_appointments`, `get_appointment`, `cancel_appointment`, `reschedule_appointment`, `update_appointment_notes` |
| Meeting records | `update_meeting_transcription` |
| Follow-ups | `send_quick_email` |
| Forms + branching logic | `create_routing_form`, `list_routing_forms`, `get_routing_form`, `update_routing_form`, `delete_routing_form`, `send_routing_form`, `list_form_recipients`, `create_data_field`, `list_data_fields` |
| CRM (contacts) | `create_contact`, `search_contacts`, `get_contact`, `update_contact`, `delete_contact`, `bulk_create_contacts` |
| Automation (webhooks) | `list_webhooks`, `create_webhook`, `update_webhook`, `delete_webhook` |
| Calendar | `list_calendar_connections` |

### Scope notes
- **Transcription** — meetergo stores a transcript/summary per appointment but does **not** record meetings itself. Feed `update_meeting_transcription` from a notetaker.
- **Branding** — the API sets brand colors, header image, logo, and per-meeting-type CSS *modes*; arbitrary raw CSS is a dashboard-only setting.
- **CRM sync** — meetergo has native sync to HubSpot/Pipedrive/Salesforce/etc. (set via `update_meeting_type`), but not every CRM; route others via a webhook.
- **Payments** — not exposed in the v4 API as of this writing; handle via a separate processor triggered off the booking webhook.

## Prerequisites

1. **A meetergo plan with API access enabled.** The Platform API is on eligible plans — you may need to contact meetergo to switch it on.
2. **An API token** — in your meetergo dashboard, create a **Personal Access Token** (`rgo-…`, recommended for automating your own workspace) or a **Platform API Key** (`ak_live:…`). Tokens are shown once; store securely.
3. **Python 3.10+**.

## Quick start (guided setup)

```bash
git clone https://github.com/chill-lichen/meetergo-mcp.git
cd meetergo-mcp
python3 configure.py
```

`configure.py` interactively prompts you to paste your token (and optional settings), writes a
local `.env`, and prints a ready-to-paste **Claude Desktop** config block with the correct paths
for your machine — it can even merge it into your Claude config for you. Your token goes only into
the gitignored `.env` / your local Claude config, **never into the source code**.

Prefer to do it by hand? See the manual steps below.

<details>
<summary>Manual install</summary>

### Option A — [uv](https://docs.astral.sh/uv/) (no manual install)

```bash
uvx --from git+https://github.com/chill-lichen/meetergo-mcp meetergo-mcp
```

### Option B — pip + virtualenv

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install .        # or: pip install -r requirements.txt
cp .env.example .env  # then edit .env and add your token
```

</details>

## Quick test

```bash
export MEETERGO_API_KEY="rgo-...your-token..."
mcp dev meetergo_mcp.py     # opens the MCP Inspector UI
```

Call `get_me` first — a 200 with your user object means auth works.

## Connect to Claude Desktop

`configure.py` will print and (optionally) install this for you. To do it manually, edit:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

**uv (Option A):**

```json
{
  "mcpServers": {
    "meetergo": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/chill-lichen/meetergo-mcp", "meetergo-mcp"],
      "env": { "MEETERGO_API_KEY": "rgo-...your-token..." }
    }
  }
}
```

**venv (Option B)** — use absolute paths to the venv's Python and the script:

```json
{
  "mcpServers": {
    "meetergo": {
      "command": "/absolute/path/to/meetergo-mcp/.venv/bin/python",
      "args": ["/absolute/path/to/meetergo-mcp/meetergo_mcp.py"],
      "env": { "MEETERGO_API_KEY": "rgo-...your-token..." }
    }
  }
}
```

Restart Claude Desktop fully. Then try: *"Use meetergo to list my meeting types,"* then *"show my openings next week."*

> Only set `MEETERGO_USER_ID` if you use a Platform API Key (`ak_live:…`) and need to act on behalf of a specific user. With a Personal Access Token, leave it unset.

## Configuration

| Env var | Required | Default | Description |
|---|---|---|---|
| `MEETERGO_API_KEY` | yes | — | Bearer token (`rgo-…` or `ak_live:…`) |
| `MEETERGO_USER_ID` | no | — | `x-meetergo-api-user-id` (Platform API Key only) |
| `MEETERGO_API_BASE` | no | `https://api.meetergo.com/v4` | Override base URL |
| `MEETERGO_TIMEOUT` | no | `30` | Per-request timeout (seconds) |

## Security

- Keep tokens in `env` / `.env` — never in code or committed files. `.env` is gitignored.
- meetergo keys expire (1–90 days) and must be rotated; update your config on rotation.
- Rate limit: ~100 requests/minute per key (bursts to ~200). The server retries 429/5xx with backoff.

## Extending

Adding an endpoint is a few lines — copy any `@mcp.tool()` function and change the path/body.
Full API reference: <https://developer.meetergo.com>. The `_request(..., root=True)` flag targets
host-level endpoints (`/crm`, `/webhooks`) that live outside `/v4`.

## Contributing

Issues and PRs welcome. This is a small, dependency-light tool by design — please keep new tools
consistent with the existing style (typed args for simple calls, a passthrough `dict`/`payload`
for complex config objects).

## License

[BSD-3-Clause](LICENSE).
