"""Ticatag IT Support — French voice agent that creates Jira tickets.

Run locally with:
    cd demos/ticatag
    ../../scripts/run-with-secrets.sh uvicorn main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

import fastapi
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse

import gradbot
from gradbot.schemas import IGNORE_WORDS, sanitize

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
    last_user_turn_idx: int | None = None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
TOOLS = [
    gradbot.ToolDef(
        name="correct_transcript",
        description=(
            "Silently correct a Gradium STT transcription error. "
            "Call this BEFORE your spoken response whenever STT has mangled the caller's words "
            "(email addresses, proper nouns, phone numbers, etc.). "
            "Pass the raw original and your best interpretation."
        ),
        parameters_json=json.dumps({
            "type": "object",
            "properties": {
                "original": {
                    "type": "string",
                    "description": "The raw STT text as Gradium produced it",
                },
                "corrected": {
                    "type": "string",
                    "description": "The cleaned-up, human-readable version",
                },
            },
            "required": ["original", "corrected"],
        }),
    ),
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
# Phone-event SSE broadcast
# ---------------------------------------------------------------------------
_phone_event_listeners: list[asyncio.Queue] = []


async def broadcast_phone_event(event: dict) -> None:
    """Fan-out a phone transcript/ticket event to all connected SSE clients."""
    for q in list(_phone_event_listeners):
        await q.put(event)


@app.get("/api/phone-events")
async def phone_events():
    """Server-Sent Events stream for phone-call transcript and ticket events."""
    queue: asyncio.Queue[dict] = asyncio.Queue()
    _phone_event_listeners.append(queue)

    async def generate():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            try:
                _phone_event_listeners.remove(queue)
            except ValueError:
                pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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


async def _create_jira_ticket_tool(
    handle: gradbot.ToolHandle,
    state: CallState,
    jira: JiraClient,
    on_created: Callable[[dict], Awaitable[None]] | None = None,
) -> None:
    """Shared tool-call handler used by both the browser and Twilio sessions."""
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

        if on_created is not None:
            await on_created(ticket)

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


@app.websocket("/ws/chat")
async def ws_chat(websocket: fastapi.WebSocket):
    state = CallState()
    jira: JiraClient = app.state.jira

    async def on_start(msg: dict) -> gradbot.SessionConfig:
        del msg
        return make_session_config()

    async def on_user_text(msg) -> None:
        state.last_user_turn_idx = getattr(msg, "turn_idx", None)

    async def on_tool_call(handle, input_handle, ws):
        del input_handle

        if handle.name == "correct_transcript":
            corrected = handle.args.get("corrected", "")
            await ws.send_json({
                "type": "stt_correction",
                "turn_idx": state.last_user_turn_idx,
                "corrected": corrected,
            })
            await handle.send_json({"success": True})
            return

        async def notify(ticket: dict) -> None:
            await ws.send_json({"type": "ticket_created", "ticket": ticket})

        await _create_jira_ticket_tool(handle, state, jira, notify)

    await gradbot.websocket.handle_session(
        websocket,
        config=_cfg,
        on_start=on_start,
        on_tool_call=on_tool_call,
        on_user_text=on_user_text,
    )


# ---------------------------------------------------------------------------
# REST: ticket list (polled by the frontend)
# ---------------------------------------------------------------------------
@app.get("/api/tickets")
async def list_tickets():
    return await app.state.jira.list_recent_tickets()


# ---------------------------------------------------------------------------
# Twilio voice — TwiML + Media Streams bridge
# ---------------------------------------------------------------------------
@app.post("/twilio/voice", response_class=PlainTextResponse)
async def twilio_voice(request: fastapi.Request):
    """Return TwiML that connects the call to /twilio/stream.

    Twilio dials the literal URL written in the <Stream> element, so any
    reverse-proxy path prefix (or subdomain) must be included. Set
    PUBLIC_WS_URL to the full external WebSocket URL, e.g.
        PUBLIC_WS_URL=wss://ticatag.gradgtm.com/twilio/stream
    """
    fallback_host = request.headers.get("x-forwarded-host") or request.url.hostname
    public_ws = os.environ.get("PUBLIC_WS_URL", f"wss://{fallback_host}/twilio/stream")
    twiml = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<Response>\n'
        f'  <Connect>\n'
        f'    <Stream url="{public_ws}"/>\n'
        f'  </Connect>\n'
        f'</Response>\n'
    )
    return PlainTextResponse(twiml, media_type="application/xml")


@app.websocket("/twilio/stream")
async def twilio_stream(websocket: fastapi.WebSocket):
    """Bridge Twilio Media Streams (μ-law 8 kHz, base64 in JSON) to gradbot."""
    await websocket.accept()
    state = CallState()
    jira: JiraClient = app.state.jira

    stream_sid: str | None = None
    try:
        while stream_sid is None:
            raw = await websocket.receive_text()
            evt = json.loads(raw)
            kind = evt.get("event")
            if kind == "start":
                stream_sid = evt["streamSid"]
                logger.info("twilio stream started: %s", stream_sid)
            elif kind == "connected":
                logger.info("twilio connected")
            else:
                logger.debug("twilio pre-start event: %s", kind)
    except (fastapi.WebSocketDisconnect, json.JSONDecodeError):
        try:
            await websocket.close()
        except Exception:
            pass
        return

    input_handle, output_handle = await gradbot.run(
        **_cfg.client_kwargs,
        session_config=make_session_config(),
        input_format=gradbot.AudioFormat.Ulaw,
        output_format=gradbot.AudioFormat.Ulaw,
    )

    stop_event = asyncio.Event()
    pending_tool_tasks: set[asyncio.Task] = set()

    async def notify_phone_ticket(ticket: dict) -> None:
        await broadcast_phone_event({"type": "ticket_created", "ticket": ticket})

    async def consumer() -> None:
        try:
            while not stop_event.is_set():
                msg = await output_handle.receive()
                if msg is None:
                    return
                if msg.msg_type == "audio":
                    payload = base64.b64encode(msg.data).decode("ascii")
                    await websocket.send_text(json.dumps({
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {"payload": payload},
                    }))
                elif msg.msg_type == "stt_text":
                    text = getattr(msg, "text", "") or ""
                    if text.strip():
                        state.last_user_turn_idx = getattr(msg, "turn_idx", None)
                        await broadcast_phone_event({
                            "type": "phone_stt",
                            "stream_id": stream_sid,
                            "text": text,
                            "start_s": getattr(msg, "start_s", 0) or 0,
                        })
                elif msg.msg_type == "tts_text":
                    text = sanitize(getattr(msg, "text", "") or "")
                    if text and text.lower() not in IGNORE_WORDS:
                        await broadcast_phone_event({
                            "type": "phone_tts",
                            "stream_id": stream_sid,
                            "text": text,
                            "turn_idx": getattr(msg, "turn_idx", None),
                            "start_s": getattr(msg, "start_s", 0) or 0,
                        })
                elif msg.msg_type == "tool_call":
                    handle = gradbot.ToolHandle(msg.tool_call_handle, msg.tool_call)
                    if handle.name == "correct_transcript":
                        corrected = handle.args.get("corrected", "")
                        await broadcast_phone_event({
                            "type": "stt_correction",
                            "stream_id": stream_sid,
                            "corrected": corrected,
                        })
                        task = asyncio.create_task(
                            handle.send_json({"success": True})
                        )
                    else:
                        task = asyncio.create_task(
                            _create_jira_ticket_tool(handle, state, jira, notify_phone_ticket)
                        )
                    pending_tool_tasks.add(task)
                    task.add_done_callback(pending_tool_tasks.discard)
        except Exception:
            logger.exception("twilio consumer error")
        finally:
            stop_event.set()

    async def producer() -> None:
        try:
            while not stop_event.is_set():
                raw = await websocket.receive_text()
                evt = json.loads(raw)
                kind = evt.get("event")
                if kind == "media":
                    audio = base64.b64decode(evt["media"]["payload"])
                    await input_handle.send_audio(audio)
                elif kind == "stop":
                    logger.info("twilio stop received")
                    return
        except fastapi.WebSocketDisconnect:
            logger.info("twilio websocket disconnected")
        except Exception:
            logger.exception("twilio producer error")
        finally:
            stop_event.set()
            try:
                await input_handle.close()
            except Exception:
                pass

    try:
        await asyncio.gather(consumer(), producer(), return_exceptions=True)
    finally:
        for t in pending_tool_tasks:
            t.cancel()
        if pending_tool_tasks:
            await asyncio.gather(*pending_tool_tasks, return_exceptions=True)
        try:
            await websocket.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Static + frontend
# ---------------------------------------------------------------------------
gradbot.routes.setup(
    app,
    config=_cfg,
    static_dir=Path(__file__).parent / "static",
)
