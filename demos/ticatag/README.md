# Ticatag — Voice IT Support Agent

French-speaking voice agent that answers IT Support Desk calls, captures the caller's name / email / phone / issue, and creates a Jira ticket in `gradium.atlassian.net` project `KAN`.

Built on the [gradbot](../../README.md) framework — same pattern as `demos/hotel`.

## Functionality

- **Channels:** web (browser call) — Twilio phone-number scaffolding present but not wired
- **Language:** French (voice: Elise)
- **Data captured:** name, email, phone, issue description
- **Action:** creates a Jira ticket, returns the key (e.g. `KAN-123`) to the caller and to the UI
- **UI:** single page — left panel = call + live transcript, right panel = live Jira ticket list

## Run locally

One-time:

```bash
infisical login                              # user-account login is fine for dev
chmod +x ../../scripts/run-with-secrets.sh
```

Each session:

```bash
cd demos/ticatag
uv sync
../../scripts/run-with-secrets.sh uv run uvicorn main:app --reload --port 8000
```

Open <http://localhost:8000>.

## Wiring Twilio later

`/twilio/voice` returns a TwiML stub. To enable real phone calls:

1. Provision a French Twilio number.
2. Set its Voice webhook to `POST https://<public-host>/twilio/voice`.
3. Replace the stub `<Say>` with `<Connect><Stream url="wss://<host>/twilio/stream"/></Connect>`.
4. Add a `/twilio/stream` WebSocket that bridges Twilio Media Streams (μ-law 8 kHz) into `gradbot.websocket.handle_session`.

## Deploying to the VPS

Same pattern as medbot:

- New systemd unit `ticatag.service` with a `ticatag-run` wrapper that calls `infisical run` (machine identity `mach1`).
- Caddy entry for `ticatag.gradium.ai` with `basicauth` + `tls internal` (or real cert).
- Bind 127.0.0.1:8000.
