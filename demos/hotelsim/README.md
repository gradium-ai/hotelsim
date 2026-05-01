# Hotelsim — Four Seasons Rive Gauche concierge

Bilingual (FR/EN) voice agent for a fictitious upscale Paris hotel. Provides room
information, takes reservations, and bridges to a Twilio Media Streams phone
line. Modeled on the `ticatag` demo (same Twilio bridge, same realtime transcript
UI).

## What it does

- Greets the caller bilingually, then switches to either French (Constance voice)
  or English (Arthur voice) based on the caller's first reply, via a
  `set_language` tool that calls `input_handle.send_config()`.
- Describes rooms, rates (EUR & USD), amenities, restaurants, and policies from
  the data hard-coded in `hotel.py` — every room is always available.
- Takes reservations (`make_reservation` tool). Reservations live in memory only
  and are wiped on process restart. A confirmation code is generated and read
  back to the caller.
- Silently corrects STT errors (`correct_transcript` tool, same pattern as
  ticatag).
- Streams transcripts of inbound phone calls to the browser UI via SSE.

## Files

| File                  | What                                                |
|-----------------------|-----------------------------------------------------|
| `main.py`             | FastAPI app, browser WS + Twilio bridge + SSE       |
| `hotel.py`            | Room data, amenities, policies, reservation state   |
| `prompts/main.txt`    | Bilingual concierge system prompt                   |
| `static/index.html`   | Frontend (transcript + rooms + reservations)        |
| `pyproject.toml`      | uv project; depends on the workspace `gradbot`      |

## Local dev

```bash
cd /srv/hotelsim/demos/hotelsim
uv sync
GRADIUM_API_KEY=... LLM_API_KEY=... uvicorn main:app --reload --port 8102
```

## Production deployment on this host

The systemd unit `hotelsim.service` runs this demo as the `ubuntu` user via
`/usr/local/bin/hotelsim-run`. The run script pulls secrets from Infisical
(project ID set in `/etc/hotelsim/env`) and execs `uvicorn` on port `8102`.

Caddy reverse-proxies `hotelsim.gradgtm.com` → `127.0.0.1:8102` with HTTP basic
auth on everything except `/twilio/*` (Twilio webhooks cannot authenticate).

### One-time setup (operator)

1. **DNS** — point `hotelsim.gradgtm.com` (A record) at the host's IP
   (`163.172.166.25`). Caddy will fetch a Let's Encrypt cert automatically once
   the DNS resolves.
2. **Infisical project** — log in to Infisical, create (or duplicate) a project
   for hotelsim. In env `dev`, add the secret:
   - `GRADIUM_HOTELSIM_API_KEY` — the Gradium API key (used for both voice and
     LLM routing). Optionally also `LLM_API_KEY` and `LLM_BASE_URL` if pointing
     at an external LLM.
   Copy the project ID and put it in `/etc/hotelsim/env` as
   `HOTELSIM_INFISICAL_PROJECT_ID`.
3. **Twilio** — on the active number `+1 573 679 3638`, set:
   - **A Call Comes In** → Webhook → `https://hotelsim.gradgtm.com/twilio/voice`
     — HTTP **POST**.
   That's it. The TwiML response contains a `<Connect><Stream
   url="wss://hotelsim.gradgtm.com/twilio/stream"/>` and Twilio bridges audio
   over the WebSocket.
4. **Start the service** —
   ```bash
   sudo systemctl enable --now hotelsim.service
   sudo systemctl status hotelsim.service
   ```

### Web UI auth

`hotelsim.gradgtm.com/` is gated by basic auth. Credentials:

| user      | password           |
|-----------|--------------------|
| `hotelsim`| `marble-concierge` |
| `gradium` | (shared with other Gradium-branded sites) |

`/twilio/voice` and `/twilio/stream` are exempt from auth so Twilio can post.

### Voice IDs

Constance (French) and Arthur (English) are looked up by display name from the
Gradium voice catalog at startup. If either is not found, the agent falls back
to Elise (the same voice ticatag uses). Logs print the resolved IDs on boot.

## Updating

This repo is a fork of `gradium-ai/gradbot`. To pull framework updates:

```bash
cd /srv/hotelsim
git fetch upstream
git merge upstream/main
sudo -u ubuntu /home/ubuntu/.local/bin/uv --directory demos/hotelsim sync --reinstall-package gradbot
sudo systemctl restart hotelsim.service
```
