from app.services.rag import run_rag_chat
# app/services/voice_orchestrator.py
# FIXED:
#   Previously site_id was passed in but NEVER used — LLM only got site_name string.
#   Now fetches heritage context from DB (same 3-tier logic as chat.py):
#     1. Node-specific Prompt row  → prompt.content
#     2. Site-level Prompt row     → prompt.content
#     3. HeritageSite summary/history/fun_facts columns (always available)
#   Voice and text chatbot now use identical knowledge.

import base64
import logging
from dataclasses import dataclass

from app.services.sarvam_stt import transcribe
from app.services.sarvam_tts import synthesize
from app.services.openrouter import call_openrouter   # direct import avoids circular dep

logger = logging.getLogger(__name__)

_LANG_INSTRUCTIONS: dict[str, str] = {
    "ENGLISH": "Respond in clear, natural English.",
    "HINDI": (
        "Respond only in Hindi using Devanagari script. "
        "Use formal but accessible language."
    ),
    "HINGLISH": (
        "Respond in Hinglish — a natural mix of Hindi and English "
        "as spoken by urban Indians. Use Roman script for Hindi words. "
        "Example: 'Yeh site bahut historic hai aur iska architecture amazing hai.' "
        "Keep it conversational and friendly."
    ),
}


@dataclass
class PipelineResult:
    user_text:    str
    bot_text:     str
    audio_bytes:  bytes
    audio_base64: str


def _get_heritage_context(db, site_id: int, node_id: int | None, site_name: str) -> str:
    """
    Same 3-tier fallback as chat.py.
    db is a SQLAlchemy Session passed from the caller.
    """
    from app.models import Prompt, HeritageSite, Node  # local import prevents circular

    # Tier 1 — node-specific prompt
    if node_id:
        record = db.query(Prompt).filter(
            Prompt.site_id == site_id,
            Prompt.node_id == node_id,
        ).first()
        if record:
            return record.content   # FIX: was record.context_prompt_text

    # Tier 2 — site-level prompt
    record = db.query(Prompt).filter(
        Prompt.site_id == site_id,
        Prompt.node_id == None,
    ).first()
    if record:
        return record.content       # FIX: was record.context_prompt_text

    # Tier 3 — build from HeritageSite columns
    site = db.query(HeritageSite).filter(HeritageSite.id == site_id).first()
    if not site:
        return f"{site_name} is a heritage site. No detailed context has been added yet."

    parts = [f"Heritage Site: {site.name}"]
    if site.summary:
        parts.append(f"Summary: {site.summary}")
    if site.history:
        parts.append(f"History: {site.history}")
    if site.fun_facts:
        parts.append(f"Fun Facts: {site.fun_facts}")

    if node_id:
        node = db.query(Node).filter(Node.id == node_id).first()
        if node:
            parts.append(f"\nCurrently at: {node.name} (stop #{node.sequence_order})")
            if node.description:
                parts.append(f"About this spot: {node.description}")

    return "\n".join(parts)


async def run(
    audio_bytes:   bytes,
    site_name:     str,
    site_id:       str,          # str from form field — convert to int
    language_code: str,
    lang_name:     str,
    node_id:       int | None = None,
    db=None,                     # SQLAlchemy Session (optional — skips DB context if None)
) -> PipelineResult:
    """
    Full voice pipeline: STT → LLM → TTS.
    Pass db=session to get DB-backed heritage context (recommended).
    Without db, falls back to site_name only (old behaviour).
    """

    # ── Stage 1: STT ─────────────────────────────────────────────────────
    logger.info(f"[Pipeline] STT start — {len(audio_bytes)}B lang={language_code}")
    try:
        user_text = await transcribe(audio_bytes, language_code)
    except Exception as exc:
        raise RuntimeError(f"STT_FAILED: {exc}") from exc

    # ── Stage 2: LLM (LangChain RAG) ──────────────────────────────────────
    lang_instruction = _LANG_INSTRUCTIONS.get(lang_name, _LANG_INSTRUCTIONS["ENGLISH"])
    logger.info(f"[Pipeline] LLM start — userText='{user_text[:60]}'")

    try:
        if db is not None and str(site_id).strip().isdigit():
            site_id_int = int(site_id)
            bot_text = await run_rag_chat(
                site_id=site_id_int,
                node_id=node_id,
                message=user_text,
                history=[],
                db=db,
                language_instruction=lang_instruction,
            )
        else:
            messages = [
                {"role": "system", "content": f"You are SHREE, AI voice guide for {site_name}. {lang_instruction}. Keep answers to 2-3 sentences. No markdown."},
                {"role": "user", "content": user_text},
            ]
            bot_text = await call_openrouter(messages)
    except Exception as exc:
        raise RuntimeError(f"LLM_FAILED: {exc}") from exc

    logger.info(f"[Pipeline] LLM done — botText='{bot_text[:60]}'")

    # ── Stage 3: TTS ─────────────────────────────────────────────────────
    logger.info(f"[Pipeline] TTS start — {len(bot_text)} chars")
    try:
        audio_bytes_out = await synthesize(bot_text, language_code)
    except Exception as exc:
        raise RuntimeError(f"TTS_FAILED: {exc}") from exc

    audio_b64 = base64.b64encode(audio_bytes_out).decode()
    logger.info(f"[Pipeline] Complete — STT={len(user_text)}c LLM={len(bot_text)}c TTS={len(audio_bytes_out)}B")

    return PipelineResult(
        user_text    = user_text,
        bot_text     = bot_text,
        audio_bytes  = audio_bytes_out,
        audio_base64 = audio_b64,
    )