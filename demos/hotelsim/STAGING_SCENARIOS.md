# Hotelsim Staging Scenarios

This branch adds controlled counterparty behavior for outbound-caller evals.
Production behavior remains unchanged unless this branch is deployed.

## Endpoints

List scenarios:

```bash
curl https://<host>/api/scenarios
```

Queue the next inbound Twilio call:

```bash
curl -u "$HOTELSIM_BASIC_AUTH" \
  -H 'Content-Type: application/json' \
  -d '{"scenario":"transfer"}' \
  https://<host>/api/scenario/next
```

Direct Twilio webhook override:

```text
https://<host>/twilio/voice?scenario=ivr
```

Browser calls also expose a scenario selector in the call controls.

## Scenario IDs

- `normal`
- `curt`
- `transfer`
- `ivr`
- `hold_then_human`
- `booking_pressure`
- `partial_answer`
- `language_mismatch`

## Intended Use

Use this as the callee-side fixture for `gizmo-voice-agent` business-mode tests.
Queue a scenario, place an outbound call to the hotelsim Twilio number, then
inspect `calls.json` / dashboard eval contracts on the caller side.
