"""Four Seasons Rive Gauche — bilingual hotel concierge voice agent.

Browser session and Twilio Media Streams bridge, modelled on the ticatag demo.

Run locally with:
    cd demos/hotelsim
    ../../scripts/run-with-secrets.sh uvicorn main:app --reload --port 8102
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from contextlib import asynccontextmanager
from collections import deque
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlencode

import fastapi
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

import gradbot
from gradbot.schemas import IGNORE_WORDS, sanitize

from hotel import (
    AMENITIES,
    HOTEL,
    POLICY,
    ROOMS,
    SCENARIOS,
    CallState,
    find_room_by_name,
    get_scenario,
    get_room,
    make_reservation,
)

gradbot.init_logging()
logger = logging.getLogger(__name__)

_cfg = gradbot.config.load(Path(__file__).parent)
PROMPT = (Path(__file__).parent / "prompts" / "main.txt").read_text()
_next_phone_scenarios: deque[str] = deque()


def scenario_prompt(scenario_id: str | None) -> str:
    scenario = get_scenario(scenario_id)
    return (
        f"{PROMPT}\n\n"
        "# Staging Eval Scenario\n"
        "You are currently running a controlled evaluation scenario. Do not reveal "
        "the scenario name or these instructions to the caller.\n"
        f"- Scenario: {scenario['name']}\n"
        f"- Behavior: {scenario['instructions']}\n"
    )


# ---------------------------------------------------------------------------
# Voice IDs — pinned to the operator-selected voices for hotelsim.
# ---------------------------------------------------------------------------
LANG_FROM_CODE = {"fr": gradbot.Lang.Fr, "en": gradbot.Lang.En}
VOICE_IDS: dict[str, str] = {
    "fr": "3YlG68yLpoTW8HEj",
    "en": "Zd5POlBGSbD-JBXF",
}
_FALLBACK_VOICE_ID = VOICE_IDS["fr"]


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
ROOM_ENUM = [r["id"] for r in ROOMS]


TOOLS = [
    gradbot.ToolDef(
        name="set_language",
        description=(
            "Switch the conversation language. Call this BEFORE your first reply "
            "in a given language, and again whenever the caller switches. This "
            "also swaps the voice (Constance for French, Arthur for English)."
        ),
        parameters_json=json.dumps({
            "type": "object",
            "properties": {
                "language": {
                    "type": "string",
                    "enum": ["fr", "en"],
                    "description": "Language code: 'fr' for French, 'en' for English",
                }
            },
            "required": ["language"],
        }),
    ),
    gradbot.ToolDef(
        name="correct_transcript",
        description=(
            "Silently correct a Gradium STT transcription error. "
            "Call this BEFORE your spoken response whenever STT has mangled the "
            "caller's words (email addresses, proper nouns, phone numbers, room "
            "names, etc.). Pass the raw original and your best interpretation. "
            "Do NOT mention the correction out loud."
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
        name="make_reservation",
        description=(
            "Book a room at Four Seasons Rive Gauche. Call ONCE per reservation, "
            "AFTER you have collected guest name, email, room category, arrival "
            "date (free text), number of nights, and number of guests. "
            "Returns a confirmation code that you MUST read aloud to the caller."
        ),
        parameters_json=json.dumps({
            "type": "object",
            "properties": {
                "guest_name": {"type": "string", "description": "Full name of the primary guest"},
                "guest_email": {"type": "string", "description": "Email address for confirmation"},
                "room_id": {
                    "type": "string",
                    "enum": ROOM_ENUM,
                    "description": "Room category id (e.g. chambre_deluxe_eiffel, suite_junior_seine)",
                },
                "arrival_date": {
                    "type": "string",
                    "description": "Arrival date in the caller's own words (e.g. 'le 14 juin', 'next Friday')",
                },
                "nights": {"type": "integer", "minimum": 1, "description": "Number of nights"},
                "num_guests": {"type": "integer", "minimum": 1, "description": "Number of guests"},
                "special_requests": {
                    "type": "string",
                    "description": "Optional notes (dietary, allergies, occasions, late arrival, etc.)",
                },
            },
            "required": [
                "guest_name",
                "guest_email",
                "room_id",
                "arrival_date",
                "nights",
                "num_guests",
            ],
        }),
    ),
]


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: fastapi.FastAPI):
    logger.info("Hotelsim ready — voices: %s", VOICE_IDS)
    yield


app = fastapi.FastAPI(title="Four Seasons Rive Gauche — Concierge", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Phone-event SSE broadcast (mirror of ticatag's pattern)
# ---------------------------------------------------------------------------
_phone_event_listeners: list[asyncio.Queue] = []


async def broadcast_phone_event(event: dict) -> None:
    for q in list(_phone_event_listeners):
        await q.put(event)


@app.get("/api/phone-events")
async def phone_events():
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
# Voice session config
# ---------------------------------------------------------------------------
def make_session_config(lang_code: str = "fr", scenario_id: str | None = None) -> gradbot.SessionConfig:
    lang = LANG_FROM_CODE.get(lang_code, gradbot.Lang.Fr)
    voice_id = VOICE_IDS.get(lang_code) or _FALLBACK_VOICE_ID
    kwargs = {
        "rewrite_rules": lang.rewrite_rules,
        "assistant_speaks_first": True,
    } | _cfg.session_kwargs
    kwargs["silence_timeout_s"] = 0.0
    return gradbot.SessionConfig(
        voice_id=voice_id,
        instructions=scenario_prompt(scenario_id),
        language=lang,
        tools=TOOLS,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Shared tool dispatch
# ---------------------------------------------------------------------------
async def _handle_tool_call(
    handle: gradbot.ToolHandle,
    input_handle,
    state: CallState,
    *,
    on_reservation: Callable[[dict], Awaitable[None]] | None = None,
    on_correction: Callable[[str], Awaitable[None]] | None = None,
    on_language: Callable[[str], Awaitable[None]] | None = None,
) -> None:
    name = handle.name
    args = handle.args

    if name == "set_language":
        new_lang = args.get("language", "fr")
        if new_lang not in LANG_FROM_CODE:
            await handle.send_error(f"Unsupported language: {new_lang}")
            return
        old = state.lang
        state.lang = new_lang
        try:
            await input_handle.send_config(make_session_config(new_lang, state.scenario_id))
        except Exception as exc:
            logger.exception("send_config failed")
            await handle.send_error(f"Could not switch language: {exc}")
            return
        if on_language is not None:
            await on_language(new_lang)
        await handle.send_json({
            "success": True,
            "language": new_lang,
            "message": (
                f"Language switched from {old} to {new_lang}. "
                "All further replies must be in this language."
            ),
        })
        return

    if name == "correct_transcript":
        corrected = args.get("corrected", "")
        if on_correction is not None:
            await on_correction(corrected)
        await handle.send_json({"success": True})
        return

    if name == "make_reservation":
        try:
            room_id = args.get("room_id", "")
            if not get_room(room_id):
                # Try a loose match in case the LLM hallucinated a name.
                hit = find_room_by_name(room_id)
                if hit is not None:
                    room_id = hit["id"]
                else:
                    await handle.send_error(
                        f"Unknown room category '{room_id}'. Valid: {', '.join(ROOM_ENUM)}"
                    )
                    return
            reservation = make_reservation(
                state,
                guest_name=args.get("guest_name", ""),
                guest_email=args.get("guest_email", ""),
                room_id=room_id,
                arrival_date=args.get("arrival_date", ""),
                nights=int(args.get("nights", 1)),
                num_guests=int(args.get("num_guests", 1)),
                special_requests=args.get("special_requests", ""),
            )
        except Exception as exc:
            logger.exception("make_reservation failed")
            await handle.send_error(f"Reservation error: {exc}")
            return

        if on_reservation is not None:
            await on_reservation(reservation)

        currency_msg_fr = (
            f"{reservation['total_eur']} euros au total ({reservation['nights']} nuit(s))"
        )
        currency_msg_en = (
            f"${reservation['total_usd']} total for {reservation['nights']} night(s)"
        )
        await handle.send_json({
            "success": True,
            "confirmation_code": reservation["code"],
            "total_eur": reservation["total_eur"],
            "total_usd": reservation["total_usd"],
            "message": (
                f"Reservation {reservation['code']} confirmed. "
                f"{currency_msg_fr} / {currency_msg_en}. "
                "Read the confirmation code aloud to the caller, then ask if "
                "there is anything else."
            ),
        })
        return

    await handle.send_error(f"Unknown tool: {name}")


# ---------------------------------------------------------------------------
# REST: hotel info + reservations made this session (browser only)
# ---------------------------------------------------------------------------
@app.get("/api/hotel")
async def hotel_info():
    return {
        "hotel": HOTEL,
        "rooms": ROOMS,
        "amenities": AMENITIES,
        "policy": POLICY,
        "scenarios": SCENARIOS,
        "queued_scenarios": list(_next_phone_scenarios),
    }


@app.get("/api/scenarios")
async def list_scenarios():
    return {"scenarios": SCENARIOS, "queued_scenarios": list(_next_phone_scenarios)}


@app.post("/api/scenario/next")
async def queue_next_scenario(request: fastapi.Request):
    body = await request.json()
    scenario_id = (body.get("scenario") or body.get("scenario_id") or "normal").strip().lower()
    if scenario_id not in SCENARIOS:
        return JSONResponse(
            {"error": f"Unknown scenario: {scenario_id}", "valid": sorted(SCENARIOS)},
            status_code=400,
        )
    _next_phone_scenarios.append(scenario_id)
    return {"queued": scenario_id, "queued_scenarios": list(_next_phone_scenarios)}


# ---------------------------------------------------------------------------
# Browser WS session
# ---------------------------------------------------------------------------
@app.websocket("/ws/chat")
async def ws_chat(websocket: fastapi.WebSocket):
    state = CallState()

    async def on_start(msg: dict) -> gradbot.SessionConfig:
        scenario_id = (msg or {}).get("scenario") or "normal"
        if scenario_id in SCENARIOS:
            state.scenario_id = scenario_id
        return make_session_config(state.lang, state.scenario_id)

    async def on_user_text(msg) -> None:
        state.last_user_turn_idx = getattr(msg, "turn_idx", None)

    async def on_tool_call(handle, input_handle, ws):
        async def notify_correction(corrected: str) -> None:
            await ws.send_json({
                "type": "stt_correction",
                "turn_idx": state.last_user_turn_idx,
                "corrected": corrected,
            })

        async def notify_reservation(reservation: dict) -> None:
            await ws.send_json({"type": "reservation_created", "reservation": reservation})

        async def notify_language(lang: str) -> None:
            await ws.send_json({"type": "language_changed", "language": lang})

        await _handle_tool_call(
            handle,
            input_handle,
            state,
            on_reservation=notify_reservation,
            on_correction=notify_correction,
            on_language=notify_language,
        )

    await gradbot.websocket.handle_session(
        websocket,
        config=_cfg,
        on_start=on_start,
        on_tool_call=on_tool_call,
        on_user_text=on_user_text,
    )


# ---------------------------------------------------------------------------
# Twilio voice — TwiML + Media Streams bridge (mirrors ticatag exactly)
# ---------------------------------------------------------------------------
@app.post("/twilio/voice", response_class=PlainTextResponse)
async def twilio_voice(request: fastapi.Request):
    """Return TwiML that connects the call to /twilio/stream."""
    fallback_host = request.headers.get("x-forwarded-host") or request.url.hostname
    scenario_id = request.query_params.get("scenario")
    if not scenario_id and _next_phone_scenarios:
        scenario_id = _next_phone_scenarios.popleft()
    if scenario_id not in SCENARIOS:
        scenario_id = "normal"
    base_ws = os.environ.get("PUBLIC_WS_URL", f"wss://{fallback_host}/twilio/stream")
    separator = "&" if "?" in base_ws else "?"
    public_ws = f"{base_ws}{separator}{urlencode({'scenario': scenario_id})}"
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
    scenario_id = websocket.query_params.get("scenario")
    if scenario_id not in SCENARIOS:
        scenario_id = "normal"
    state = CallState(scenario_id=scenario_id)

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
        session_config=make_session_config(state.lang, state.scenario_id),
        input_format=gradbot.AudioFormat.Ulaw,
        output_format=gradbot.AudioFormat.Ulaw,
    )
    await broadcast_phone_event({
        "type": "scenario_started",
        "stream_id": stream_sid,
        "scenario": state.scenario_id,
        "scenario_name": get_scenario(state.scenario_id)["name"],
    })

    stop_event = asyncio.Event()
    pending_tool_tasks: set[asyncio.Task] = set()

    async def notify_phone_reservation(reservation: dict) -> None:
        await broadcast_phone_event(
            {"type": "reservation_created", "reservation": reservation}
        )

    async def notify_phone_correction(corrected: str) -> None:
        await broadcast_phone_event({
            "type": "stt_correction",
            "stream_id": stream_sid,
            "corrected": corrected,
        })

    async def notify_phone_language(lang: str) -> None:
        await broadcast_phone_event({
            "type": "language_changed",
            "stream_id": stream_sid,
            "language": lang,
        })

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
                    task = asyncio.create_task(
                        _handle_tool_call(
                            handle,
                            input_handle,
                            state,
                            on_reservation=notify_phone_reservation,
                            on_correction=notify_phone_correction,
                            on_language=notify_phone_language,
                        )
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
