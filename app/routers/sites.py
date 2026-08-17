# app/routers/sites.py
# UPDATED: joinedload(Node.images) so NodeDetailScreen gets node_images

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload
from app.database import get_db
from app.models import HeritageSite, Node, NodeImage, Recommendation
from app.utils import haversine
from app.schemas import (
    SiteDetailResponse,
    SiteListItemResponse,
    SiteImageResponse,
    NodeResponse,
    NearbySiteResponse,
    RecommendationResponse,
    NodePositionResponse,
)

router = APIRouter(prefix="/sites", tags=["Sites"])

# Common districts/cities and identifiers for Haryana
HARYANA_KEYWORDS = [
    "haryana", "sonipat", "sonepat", "gurugram", "gurgaon",
    "faridabad", "panipat", "kurukshetra", "karnal", "rohtak",
    "hisar", "panchkula", "ambala", "yamunanagar", "rewari",
    "bhiwani", "sirsa", "jind", "jhajjar", "kaithal", "palwal",
    "fatehabad", "narnaul", "charkhi dadri", "mahendragarh"
]


def _format_site_list(sites: list[HeritageSite]) -> list[SiteListItemResponse]:
    result = []
    for site in sites:
        king_node = next((n for n in site.nodes if n.is_king), None)
        king_qr = king_node.qr_code_value if king_node else (site.nodes[0].qr_code_value if site.nodes else None)
        hero_img = site.images[0].image_url if site.images else None
        node_total = len(site.nodes)
        result.append(
            SiteListItemResponse(
                id=site.id,
                name=site.name,
                location=site.location,
                rating=site.rating or 0.0,
                nodes_count=node_total,
                node_count=node_total,
                nodes=node_total,
                image=hero_img,
                summary=site.summary,
                latitude=site.latitude,
                longitude=site.longitude,
                king_node_qr=king_qr,
            )
        )
    return result


@router.get("", response_model=list[SiteListItemResponse])
def get_all_sites(
    location: Optional[str] = Query(None, description="Search/filter by location or city name (e.g. Sonepat, Delhi)"),
    search: Optional[str] = Query(None, description="Search by site name, location, or summary"),
    q: Optional[str] = Query(None, description="Search term alias"),
    all_sites: bool = Query(False, description="If true, disable default Haryana filter and return all sites"),
    db: Session = Depends(get_db),
):
    """
    Get all listed heritage sites.
    - When `location` or `search`/`q` is specified: returns sites around/matching that location or name.
    - When no location or search is given: returns all sites listed in Haryana by default.
    - Set `all_sites=true` to retrieve all sites across all states.
    """
    query = db.query(HeritageSite).options(
        joinedload(HeritageSite.images),
        joinedload(HeritageSite.nodes).joinedload(Node.images)
    )

    loc_term = (location or "").strip()
    search_term = (search or q or "").strip()

    if loc_term:
        query = query.filter(
            or_(
                HeritageSite.location.ilike(f"%{loc_term}%"),
                HeritageSite.name.ilike(f"%{loc_term}%")
            )
        )
    elif search_term:
        query = query.filter(
            or_(
                HeritageSite.name.ilike(f"%{search_term}%"),
                HeritageSite.location.ilike(f"%{search_term}%"),
                HeritageSite.summary.ilike(f"%{search_term}%")
            )
        )
    elif not all_sites:
        # Default behavior: list all sites in Haryana
        haryana_filters = [HeritageSite.location.ilike(f"%{kw}%") for kw in HARYANA_KEYWORDS]
        haryana_sites = query.filter(or_(*haryana_filters)).order_by(HeritageSite.id).all()
        if haryana_sites:
            return _format_site_list(haryana_sites)
        # Fallback if no specific Haryana-tagged sites exist in DB yet
        sites = query.order_by(HeritageSite.id).all()
        return _format_site_list(sites)

    sites = query.order_by(HeritageSite.id).all()
    return _format_site_list(sites)



@router.get("/scan/{qr_value}")
def scan_qr(qr_value: str, db: Session = Depends(get_db)):
    node = db.query(Node).filter(Node.qr_code_value == qr_value).first()
    if not node:
        return {"status": "invalid"}
    return {
        "status":         "valid",
        "site_id":        node.site_id,
        "node_id":        node.id,
        "sequence_order": node.sequence_order,
        "node_name":      node.name,
    }


@router.get("/nearby", response_model=list[NearbySiteResponse])
def get_nearby_sites(
    lat:          float,
    lng:          float,
    max_range_km: float = 100,
    db:           Session = Depends(get_db),
):
    sites  = db.query(HeritageSite).all()
    result = []
    for site in sites:
        distance = haversine(lat, lng, site.latitude, site.longitude)
        if distance <= max_range_km * 1000:
            result.append({
                "id":              site.id,
                "name":            site.name,
                "location":        site.location,
                "latitude":        site.latitude,
                "longitude":       site.longitude,
                "distance_meters": round(distance),
                "inside_geofence": distance <= site.geofence_radius_meters,
            })
    result.sort(key=lambda x: x["distance_meters"])
    return result


@router.get("/{site_id}/nodes", response_model=list[NodePositionResponse])
def get_site_nodes(site_id: int, db: Session = Depends(get_db)):
    """Return node id, name, latitude, longitude, sequence_order, is_king for map/directions.
    Uses exact values from the nodes table."""
    nodes = (
        db.query(Node)
        .filter(Node.site_id == site_id)
        .order_by(Node.sequence_order)
        .all()
    )
    return nodes


@router.get("/{site_id}", response_model=SiteDetailResponse)
def get_site_details(site_id: int, db: Session = Depends(get_db)):
    site = (
        db.query(HeritageSite)
        .options(
            joinedload(HeritageSite.images),
            # Load nodes AND each node's images in one query
            joinedload(HeritageSite.nodes).joinedload(Node.images),
        )
        .filter(HeritageSite.id == site_id)
        .first()
    )
    if not site:
        raise HTTPException(status_code=404, detail=f"Site {site_id} not found")
    return site


@router.get("/{site_id}/recommendations", response_model=list[RecommendationResponse])
def get_site_recommendations(
    site_id: int,
    type: str = None,  # Optional filter: monument, hotel, restaurant
    db: Session = Depends(get_db)
):
    """
    Get recommendations for a heritage site.
    Optional filter by type: monument, hotel, restaurant
    """
    query = db.query(Recommendation).filter(Recommendation.site_id == site_id)
    
    if type:
        query = query.filter(Recommendation.type == type)
    
    recommendations = query.all()
    return recommendations
