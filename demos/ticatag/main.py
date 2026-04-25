"""Ticatag IT Support — French voice agent that creates Jira tickets.

Run locally with:
    cd demos/ticatag
    ../../scripts/run-with-secrets.sh uvicorn main:app --reload --port 8000
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import fastapi
from fastapi.responses import HTMLResponse, PlainTextResponse

import gradbot

from jira_client import JiraClient

gradbot.init_logging()
logger = logging.getLogger(__name__)

_cfg = gradbot.config.load(Path(__file__).parent)

# French voice (Elise) — same one used in the hotel demo.
VOICE_ID = "b35yykvVppLXyw_l"
LANG = gradbot.Lang.Fr

PROMPT = (Path(__file__).parent / "prompts" / "main.txt").read_text()


@dataclass
class CallState:
    caller_name: str = ""
    caller_email: str = ""
    caller_phone: str = ""
    issue: str = ""
    ticket_key: str | None = None
    tickets_created: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
TOOLS = [
    gradbot.ToolDef(
        name="create_jira_ticket",
        description=(
            "Create a Jira ticket in the Ticatag IT Support project. "
            "Call this ONCE at the end of the call, after you have collected the caller's "
            "name, email, phone, and a description of the technical issue."
        ),
        parameters_json=json.dumps({
            "type": "object",
            "properties": {
                "caller_name": {"type": "string", "description": "Caller's full name"},
                "caller_email": {"type": "string", "description": "Caller's email address"},
                "caller_phone": {"type": "string", "description": "Caller's phone number"},
                "issue_summary": {
                    "type": "string",
                    "description": "Short title of the issue (max ~10 words), e.g. 'Outlook ne s'ouvre plus'",
                },
                "issue_description": {
                    "type": "string",
                    "description": "Full description of the technical problem in the caller's own words, in French",
                },
            },
            "required": ["caller_name", "issue_summary", "issue_description"],
        }),
    ),
]


# ---------------------------------------------------------------------------
# Lifespan: shared Jira client
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: fastapi.FastAPI):
    app.state.jira = JiraClient()
    logger.info("Ticatag voice agent ready — Jira project=%s", "KAN")
    try:
        yield
    finally:
        await app.state.jira.aclose()


app = fastapi.FastAPI(title="Ticatag IT Support", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Voice session
# ---------------------------------------------------------------------------
def make_session_config() -> gradbot.SessionConfig:
    kwargs = {
        "rewrite_rules": LANG.rewrite_rules,
        "assistant_speaks_first": True,
    } | _cfg.session_kwargs
    kwargs["silence_timeout_s"] = 0.0
    return gradbot.SessionConfig(
        voice_id=VOICE_ID,
        instructions=PROMPT,
        language=LANG,
        tools=TOOLS,
        **kwargs,
    )


@app.websocket("/ws/chat")
async def ws_chat(websocket: fastapi.WebSocket):
    state = CallState()
    jira: JiraClient = app.state.jira

    async def on_start(msg: dict) -> gradbot.SessionConfig:
        del msg
        return make_session_config()

    async def on_tool_call(handle, input_handle, ws):
        del input_handle
        try:
            if handle.name != "create_jira_ticket":
                await handle.send_error(f"Unknown tool: {handle.name}")
                return

            args = handle.args
            state.caller_name = args.get("caller_name", "") or state.caller_name
            state.caller_email = args.get("caller_email", "") or state.caller_email
            state.caller_phone = args.get("caller_phone", "") or state.caller_phone
            state.issue = args.get("issue_description", "") or state.issue

            ticket = await jira.create_ticket(
                caller_name=state.caller_name,
                caller_email=state.caller_email,
                caller_phone=state.caller_phone,
                issue_summary=args.get("issue_summary", ""),
                issue_description=state.issue,
            )
            state.ticket_key = ticket["key"]
            state.tickets_created.append(ticket)

            await ws.send_json({"type": "ticket_created", "ticket": ticket})
            await handle.send_json({
                "success": True,
                "ticket_key": ticket["key"],
                "message": (
                    f"Ticket {ticket['key']} créé avec succès. "
                    "Annoncez ce numéro de ticket au client puis terminez l'appel poliment."
                ),
            })
        except Exception as exc:
            logger.exception("create_jira_ticket failed")
            await handle.send_error(f"Jira error: {exc}")

    await gradbot.websocket.handle_session(
        websocket,
        config=_cfg,
        on_start=on_start,
        on_tool_call=on_tool_call,
    )


# ---------------------------------------------------------------------------
# REST: ticket list (polled by the frontend)
# ---------------------------------------------------------------------------
@app.get("/api/tickets")
async def list_tickets():
    return await app.state.jira.list_recent_tickets()


# ---------------------------------------------------------------------------
# Twilio scaffold (NOT wired — placeholder for later phone integration)
# ---------------------------------------------------------------------------
@app.post("/twilio/voice", response_class=PlainTextResponse)
async def twilio_voice(request: fastapi.Request):
    """TwiML stub. Wire up once a Twilio number is provisioned.

    Steps to wire later:
      1. Provision a French Twilio number.
      2. Set its Voice webhook to POST https://<host>/twilio/voice
      3. Replace this stub to <Connect><Stream url="wss://<host>/twilio/stream"/></Connect>
      4. Add a /twilio/stream WebSocket that bridges Twilio Media Streams (μ-law 8kHz)
         to gradbot.websocket.handle_session — see gradbot docs for the bridge pattern.
    """
    public_host = os.environ.get("PUBLIC_HOST", request.url.hostname)
    twiml = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<Response>\n'
        f'  <!-- TODO: replace with <Connect><Stream url="wss://{public_host}/twilio/stream"/></Connect> -->\n'
        f'  <Say language="fr-FR">Bonjour, le support technique Ticatag est en cours de configuration. '
        f'Veuillez réessayer plus tard.</Say>\n'
        f'</Response>\n'
    )
    return PlainTextResponse(twiml, media_type="application/xml")


# ---------------------------------------------------------------------------
# Static + frontend
# ---------------------------------------------------------------------------
gradbot.routes.setup(
    app,
    config=_cfg,
    static_dir=Path(__file__).parent / "static",
)
