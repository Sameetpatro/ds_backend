# app/routers/voice.py

import logging
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import HeritageSite, Node, UserChatHistory
from app.routers.users import get_user_uuid
from app.schemas import VoiceChatResponse
from app.services import voice_orchestrator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice-chat", tags=["voice"])


@router.post("", response_model=VoiceChatResponse)
async def voice_chat(
    audio:        UploadFile = File(...,  description="WAV audio from Android AudioRecord"),
    site_name:    str        = Form(...,  description="Heritage site name"),
    site_id:      str        = Form(...,  description="Heritage site ID"),
    language:     str        = Form(...,  description="BCP-47 code e.g. en-IN"),
    lang_name:    str        = Form(...,  description="ENGLISH | HINDI | HINGLISH"),
    node_id:      str        = Form("",   description="Optional node ID"),
    firebase_uid: str        = Form("",   description="Optional — when provided, exchange is persisted to user_chat_history"),
    trip_id:      str        = Form("",   description="Optional trip ID for chat history correlation"),
    db:           Session    = Depends(get_db),
):
    if audio.content_type and not audio.content_type.startswith("audio/"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Expected audio/*, received {audio.content_type}",
        )

    audio_bytes = await audio.read()
    if len(audio_bytes) < 1000:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Audio too short — minimum ~1 second required",
        )

    node_id_int = int(node_id) if node_id.strip().isdigit() else None
    trip_id_int = int(trip_id) if trip_id.strip().isdigit() else None

    # Verify and ground site_id with node.site_id if node_id is provided
    if node_id_int:
        node_record = db.query(Node).filter(Node.id == node_id_int).first()
        if node_record and node_record.site_id:
            if str(node_record.site_id) != str(site_id).strip():
                logger.warning(
                    f"[/voice-chat] Overriding mismatched site_id '{site_id}' with node.site_id '{node_record.site_id}' "
                    f"for node {node_id_int} ('{node_record.name}')"
                )
                site_id = str(node_record.site_id)
                site_record = db.query(HeritageSite).filter(HeritageSite.id == node_record.site_id).first()
                if site_record:
                    site_name = site_record.name

    logger.info(
        f"[/voice-chat] site='{site_name}' id={site_id} node={node_id_int} "
        f"lang={lang_name}({language}) audio={len(audio_bytes)}B"
    )

    try:
        result = await voice_orchestrator.run(
            audio_bytes   = audio_bytes,
            site_name     = site_name,
            site_id       = site_id,
            language_code = language,
            lang_name     = lang_name.upper(),
            node_id       = node_id_int,
            db            = db,
        )
    except RuntimeError as exc:
        msg = str(exc)
        logger.error(f"[/voice-chat] Pipeline failed: {msg}")
        if   msg.startswith("STT_FAILED"): raise HTTPException(status.HTTP_502_BAD_GATEWAY,           detail=msg)
        elif msg.startswith("LLM_FAILED"): raise HTTPException(status.HTTP_502_BAD_GATEWAY,           detail=msg)
        elif msg.startswith("TTS_FAILED"): raise HTTPException(status.HTTP_502_BAD_GATEWAY,           detail=msg)
        else:                              raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,  detail=msg)

    # Persist the voice exchange to user_chat_history (best-effort).
    # Skipped silently when no firebase_uid is provided OR the user is not
    # registered yet — voice must never fail because of analytics writes.
    if firebase_uid.strip():
        try:
            user_uuid = get_user_uuid(firebase_uid.strip(), db)
            site_id_int = int(site_id) if str(site_id).strip().isdigit() else None
            if site_id_int is not None:
                db.add_all([
                    UserChatHistory(
                        user_id   = user_uuid,
                        trip_id   = trip_id_int,
                        site_id   = site_id_int,
                        node_id   = node_id_int,
                        role      = "user",
                        content   = result.user_text,
                        lang_code = language,
                    ),
                    UserChatHistory(
                        user_id   = user_uuid,
                        trip_id   = trip_id_int,
                        site_id   = site_id_int,
                        node_id   = node_id_int,
                        role      = "assistant",
                        content   = result.bot_text,
                        lang_code = language,
                    ),
                ])
                db.commit()
        except HTTPException:
            db.rollback()
        except Exception as exc:
            db.rollback()
            logger.warning(f"[/voice-chat] user_chat_history write failed: {exc}")

    return VoiceChatResponse(
        user_text    = result.user_text,
        bot_text     = result.bot_text,
        audio_base64 = result.audio_base64,
        audio_format = "wav",
    )