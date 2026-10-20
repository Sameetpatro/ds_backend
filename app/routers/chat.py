# app/routers/chat.py
# LangChain RAG-Powered Heritage Chatbot (SHREE).
# Ingests site prompts, node guardrails, and spot descriptions to provide
# grounded, hallucination-free answers.

import logging
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import HeritageSite, Node, Trip, NodeCheckIn, UserChatHistory
from app.routers.users import get_user_uuid
from app.schemas import ChatRequest, ChatResponse
from app.services.rag import run_rag_chat
from app.utils import normalize_qr_value

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Chat"])


def resolve_chat_site_and_node(
    req: ChatRequest,
    db: Session,
) -> Tuple[HeritageSite, Optional[Node]]:
    """
    Intelligently resolves the active HeritageSite and Node from all available signals:
    1. qr_value: If provided, normalized and matched against nodes table.
    2. node_id: If provided, the node's actual site_id overrides any mismatched site_id.
    3. trip_id: If provided, resolves the trip's site and latest checkin node.
    4. site_id: If provided and matches an existing HeritageSite.
    5. firebase_uid: Resolves user's active trip or most recent QR checkin.
    """
    node: Optional[Node] = None
    site: Optional[HeritageSite] = None

    # Tier 1: qr_value (User scanned a QR code and sent the raw value or URL)
    if req.qr_value and req.qr_value.strip():
        raw_qr = req.qr_value.strip()
        normalized = normalize_qr_value(raw_qr)
        node = db.query(Node).filter(
            or_(
                Node.qr_code_value == raw_qr,
                Node.qr_code_value == normalized,
                Node.legacy_qr_code_value == raw_qr,
                Node.legacy_qr_code_value == normalized,
            )
        ).first()
        if node:
            site = db.query(HeritageSite).filter(HeritageSite.id == node.site_id).first()
            if site:
                logger.info(f"[/chat] Resolved site='{site.name}' (id={site.id}) and node='{node.name}' (id={node.id}) via qr_value='{raw_qr}'")
                return site, node

    # Tier 2: node_id (Ground truth: A node physically belongs to only ONE site)
    if req.node_id and req.node_id > 0:
        node = db.query(Node).filter(Node.id == req.node_id).first()
        if node:
            site = db.query(HeritageSite).filter(HeritageSite.id == node.site_id).first()
            if req.site_id and req.site_id != node.site_id:
                logger.warning(
                    f"[/chat] Correcting mismatched site_id: request specified site_id={req.site_id}, "
                    f"but node_id={req.node_id} ('{node.name}') belongs to site_id={node.site_id} "
                    f"('{site.name if site else ''}'). Using site_id={node.site_id}."
                )
            if site:
                return site, node

    # Tier 3: trip_id (Trip records the exact site visited)
    if req.trip_id and req.trip_id > 0:
        trip = db.query(Trip).filter(Trip.id == req.trip_id).first()
        if trip:
            site = db.query(HeritageSite).filter(HeritageSite.id == trip.site_id).first()
            latest_ck = (
                db.query(NodeCheckIn)
                .filter(NodeCheckIn.trip_id == trip.id)
                .order_by(NodeCheckIn.id.desc())
                .first()
            )
            if latest_ck:
                node = db.query(Node).filter(Node.id == latest_ck.node_id).first()
            if site:
                logger.info(f"[/chat] Resolved site='{site.name}' (id={site.id}) via trip_id={trip.id}")
                return site, node

    # Tier 4: site_id (Explicit site identifier)
    if req.site_id and req.site_id > 0:
        site = db.query(HeritageSite).filter(HeritageSite.id == req.site_id).first()
        if site:
            if req.node_id and req.node_id > 0:
                node = db.query(Node).filter(Node.id == req.node_id).first()
            return site, node

    # Tier 5: firebase_uid (lookup user's active trip or most recent QR scan)
    if req.firebase_uid and req.firebase_uid.strip():
        try:
            user_uuid = get_user_uuid(req.firebase_uid.strip(), db)
            active_trip = (
                db.query(Trip)
                .filter(
                    Trip.user_id == user_uuid,
                    Trip.is_active == True,
                    Trip.status == "ACTIVE",
                )
                .order_by(Trip.last_activity_at.desc())
                .first()
            )
            if active_trip:
                site = db.query(HeritageSite).filter(HeritageSite.id == active_trip.site_id).first()
                latest_ck = (
                    db.query(NodeCheckIn)
                    .filter(NodeCheckIn.trip_id == active_trip.id)
                    .order_by(NodeCheckIn.id.desc())
                    .first()
                )
                if latest_ck:
                    node = db.query(Node).filter(Node.id == latest_ck.node_id).first()
                if site:
                    logger.info(f"[/chat] Resolved site='{site.name}' (id={site.id}) via active trip of user={user_uuid}")
                    return site, node

            # If no active trip, check most recent QR scan
            latest_ck = (
                db.query(NodeCheckIn)
                .filter(NodeCheckIn.user_id == user_uuid)
                .order_by(NodeCheckIn.id.desc())
                .first()
            )
            if latest_ck:
                site = db.query(HeritageSite).filter(HeritageSite.id == latest_ck.site_id).first()
                node = db.query(Node).filter(Node.id == latest_ck.node_id).first()
                if site:
                    logger.info(f"[/chat] Resolved site='{site.name}' (id={site.id}) via recent check-in of user={user_uuid}")
                    return site, node
        except Exception as exc:
            logger.debug(f"[/chat] User lookup for active site failed: {exc}")

    raise HTTPException(
        status_code=400,
        detail=(
            "Could not determine heritage site context. Please scan a site QR code "
            "or provide a valid site_id, node_id, or qr_value."
        )
    )


@router.post("/", response_model=ChatResponse)
async def chat(req: ChatRequest, db: Session = Depends(get_db)):
    # Intelligently resolve site and node
    site, node = resolve_chat_site_and_node(req, db)
    resolved_node_id = node.id if node else None

    # Execute LangChain RAG pipeline
    try:
        reply = await run_rag_chat(
            site_id=site.id,
            node_id=resolved_node_id,
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
                    site_id   = site.id,
                    node_id   = resolved_node_id,
                    role      = "user",
                    content   = req.message,
                    lang_code = lang,
                ),
                UserChatHistory(
                    user_id   = user_uuid,
                    trip_id   = req.trip_id,
                    site_id   = site.id,
                    node_id   = resolved_node_id,
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
