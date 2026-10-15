# app/routers/chat.py
# LangChain RAG-Powered Heritage Chatbot (SHREE).
# Ingests site prompts, node guardrails, and spot descriptions to provide
# grounded, hallucination-free answers.

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import HeritageSite, UserChatHistory
from app.routers.users import get_user_uuid
from app.schemas import ChatRequest, ChatResponse
from app.services.rag import run_rag_chat

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Chat"])


@router.post("/", response_model=ChatResponse)
async def chat(req: ChatRequest, db: Session = Depends(get_db)):
    # Verify site exists
    site = db.query(HeritageSite).filter(HeritageSite.id == req.site_id).first()
    if not site:
        raise HTTPException(
            status_code=404,
            detail=f"Site {req.site_id} not found. Seed it via POST /admin/seed-bulk first."
        )

    # Execute LangChain RAG pipeline
    try:
        reply = await run_rag_chat(
            site_id=req.site_id,
            node_id=req.node_id,
            message=req.message,
            history=req.history,
            db=db,
        )
    except Exception as exc:
        logger.error(f"[/chat] RAG execution failed: {exc}", exc_info=True)
        err_msg = str(exc)
        if any(term in err_msg.lower() for term in ["insufficient balance", "402", "credit", "rate limit", "quota"]):
            site_name = site.name if site else "this heritage site"
            reply = (
                f"Namaste! I am SHREE, your AI guide for {site_name}. "
                "Our live AI chat is currently momentarily unavailable due to API balance/quota limits. "
                "Please explore the spot audio guides and map landmarks in the app!"
            )
        else:
            raise HTTPException(
                status_code=500,
                detail=f"AI Chatbot service error: {str(exc)}"
            )

    # Persist the exchange to user_chat_history (best-effort).
    # Skipped silently when no firebase_uid is provided OR the user is not
    # registered yet — chat must never fail because of analytics writes.
    if req.firebase_uid:
        try:
            user_uuid = get_user_uuid(req.firebase_uid, db)
            lang = req.lang_code or "en-IN"
            db.add_all([
                UserChatHistory(
                    user_id   = user_uuid,
                    trip_id   = req.trip_id,
                    site_id   = req.site_id,
                    node_id   = req.node_id,
                    role      = "user",
                    content   = req.message,
                    lang_code = lang,
                ),
                UserChatHistory(
                    user_id   = user_uuid,
                    trip_id   = req.trip_id,
                    site_id   = req.site_id,
                    node_id   = req.node_id,
                    role      = "assistant",
                    content   = reply,
                    lang_code = lang,
                ),
            ])
            db.commit()
        except HTTPException:
            # User not registered — skip silently
            db.rollback()
        except Exception as exc:
            db.rollback()
            logger.warning(f"[/chat] user_chat_history write failed: {exc}")

    return ChatResponse(reply=reply)
