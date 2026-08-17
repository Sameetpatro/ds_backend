# app/routers/admin.py

import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel

from app.database import get_db
from app.models import HeritageSite, SiteImage, Node, NodeImage, Recommendation, Prompt

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin"])


# ─────────────────────────────────────────────────────────────
# Payloads
# ─────────────────────────────────────────────────────────────

class SitePayload(BaseModel):
    name: str
    latitude: float
    longitude: float
    radius: int
    location: Optional[str] = None
    rating: Optional[float] = None
    helpline: Optional[str] = None
    video_url: Optional[str] = None
    summary: Optional[str] = None
    history: Optional[str] = None
    fun_facts: Optional[str] = None
    images: List[str] = []


class NodePayload(BaseModel):
    name: str
    latitude: float
    longitude: float
    video_url: Optional[str] = None
    sequence: int
    qr: str
    description: Optional[str] = None
    images: List[str] = []
    is_king: bool = False


class LandmarkPayload(BaseModel):
    name: str
    latitude: float
    longitude: float


class LandmarksPayload(BaseModel):
    monuments: List[LandmarkPayload] = []
    restaurants: List[LandmarkPayload] = []
    hotels: List[LandmarkPayload] = []


# ✅ NEW (Flexible Recommendations)
class RecommendationPayload(BaseModel):
    type: str
    name: str
    latitude: float
    longitude: float
    description: Optional[str] = None


class SeedBulkRequest(BaseModel):
    site: SitePayload
    nodes: List[NodePayload]
    landmarks: Optional[LandmarksPayload] = None   # old system (kept)
    recommendations: List[RecommendationPayload] = []   # ✅ fixed


class SeedPromptRequest(BaseModel):
    site_id: int
    node_id: Optional[int] = None
    prompt_text: str
    title: Optional[str] = None


# ─────────────────────────────────────────────────────────────
# SEED BULK
# ─────────────────────────────────────────────────────────────

@router.post("/seed-bulk")
def seed_bulk(payload: SeedBulkRequest, db: Session = Depends(get_db)):

    # Enforce exactly ONE king node
    king_count = sum(1 for n in payload.nodes if n.is_king)
    if king_count != 1:
        raise HTTPException(
            status_code=400,
            detail=f"Exactly ONE king node must be defined per site. Found: {king_count}"
        )

    # Create Site
    site = HeritageSite(
        name=payload.site.name,
        location=payload.site.location,
        latitude=payload.site.latitude,
        longitude=payload.site.longitude,
        geofence_radius_meters=payload.site.radius,
        rating=payload.site.rating or 4.5,
        helpline_number=payload.site.helpline,
        intro_video_url=payload.site.video_url,
        summary=payload.site.summary,
        history=payload.site.history,
        fun_facts=payload.site.fun_facts,
    )
    db.add(site)
    db.flush()

    # Site Images
    for order, url in enumerate(payload.site.images):
        if url:
            db.add(SiteImage(site_id=site.id, image_url=url, display_order=order))

    # Nodes
    for node_data in payload.nodes:
        node = Node(
            site_id=site.id,
            name=node_data.name,
            latitude=node_data.latitude,
            longitude=node_data.longitude,
            sequence_order=node_data.sequence,
            is_king=node_data.is_king,
            qr_code_value=node_data.qr,
            description=node_data.description,
            video_url=node_data.video_url,
        )
        db.add(node)
        db.flush()

        # Node Images
        for order, url in enumerate(node_data.images):
            if url:
                db.add(NodeImage(node_id=node.id, image_url=url, display_order=order))

    # ─────────────────────────────────────────
    # OLD LANDMARKS (kept for compatibility)
    # ─────────────────────────────────────────
    if payload.landmarks:
        for ltype, items in {
            "monument": payload.landmarks.monuments,
            "restaurant": payload.landmarks.restaurants,
            "hotel": payload.landmarks.hotels,
        }.items():
            for item in items:
                if item.name:
                    db.add(Recommendation(
                        site_id=site.id,
                        type=ltype,
                        name=item.name,
                        latitude=item.latitude,
                        longitude=item.longitude,
                    ))

    # ─────────────────────────────────────────
    # ✅ NEW RECOMMENDATIONS (FIXED)
    # ─────────────────────────────────────────
    if payload.recommendations:
        for rec in payload.recommendations:
            db.add(Recommendation(
                site_id=site.id,
                type=rec.type,
                name=rec.name,
                latitude=rec.latitude,
                longitude=rec.longitude,
                description=rec.description,
            ))

    db.commit()

    return {
        "success": True,
        "site_id": site.id,
        "site_name": site.name,
        "nodes_created": len(payload.nodes),
    }


# ─────────────────────────────────────────────────────────────
# PROMPT SEED
# ─────────────────────────────────────────────────────────────

@router.post("/seed-prompt")
def seed_prompt(payload: SeedPromptRequest, db: Session = Depends(get_db)):

    site = db.query(HeritageSite).filter(HeritageSite.id == payload.site_id).first()
    if not site:
        raise HTTPException(
            status_code=404,
            detail=f"Site {payload.site_id} not found. Run /seed-bulk first."
        )

    if payload.node_id is not None:
        node = db.query(Node).filter(
            Node.id == payload.node_id,
            Node.site_id == payload.site_id,
        ).first()
        if not node:
            raise HTTPException(
                status_code=404,
                detail=f"Node {payload.node_id} not found under site {payload.site_id}."
            )

    # Title logic
    if payload.title:
        title = payload.title
    elif payload.node_id is not None:
        node_obj = db.query(Node).filter(Node.id == payload.node_id).first()
        title = f"{site.name} - {node_obj.name if node_obj else f'Node {payload.node_id}'}"
    else:
        title = f"{site.name} - General"

    existing = db.query(Prompt).filter(
        Prompt.site_id == payload.site_id,
        Prompt.node_id == payload.node_id,
    ).first()

    if existing:
        existing.title = title
        existing.content = payload.prompt_text
        db.commit()
        return {
            "success": True,
            "action": "updated",
            "site_id": payload.site_id,
            "node_id": payload.node_id,
            "title": title,
        }

    db.add(Prompt(
        site_id=payload.site_id,
        node_id=payload.node_id,
        title=title,
        content=payload.prompt_text,
    ))
    db.commit()

    return {
        "success": True,
        "action": "created",
        "site_id": payload.site_id,
        "node_id": payload.node_id,
        "title": title,
    }

@router.post("/add-recommendations")
def add_recommendations(site_id: int, recs: List[RecommendationPayload], db: Session = Depends(get_db)):

    # check site exists
    site = db.query(HeritageSite).filter_by(id=site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")

    for rec in recs:
        db.add(Recommendation(
            site_id=site_id,
            type=rec.type,
            name=rec.name,
            latitude=rec.latitude,
            longitude=rec.longitude,
            description=rec.description
        ))

    db.commit()

    return {"success": True, "site_id": site_id}