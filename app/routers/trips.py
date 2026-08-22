# app/routers/trips.py

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.database import get_db
from app.models import Trip, Node, HeritageSite, NodeCheckIn
from app.routers.users import get_user_uuid

router = APIRouter(prefix="/trips", tags=["Trips"])

TRIP_INACTIVITY_LIMIT_MINUTES = 10


def sync_abandoned_trips(db: Session) -> int:
    """
    Detects and transitions stale active trips to ABANDONED status if inactive for >= 10 minutes.
    Called synchronously across trip and admin endpoints to guarantee real-time accuracy.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=TRIP_INACTIVITY_LIMIT_MINUTES)
    try:
        res = db.execute(
            text("""
                UPDATE trips
                SET status = 'ABANDONED',
                    is_active = FALSE,
                    ended_at = COALESCE(last_activity_at, started_at)
                WHERE (status = 'ACTIVE' OR (status IS NULL AND is_active = TRUE))
                  AND COALESCE(last_activity_at, started_at) < :cutoff
            """),
            {"cutoff": cutoff}
        )
        db.commit()
        return res.rowcount if res else 0
    except Exception:
        db.rollback()
        return 0


@router.post("/start")
def start_trip(
    firebase_uid: str,
    qr_value: str,
    entry_lat: float | None = None,
    entry_lng: float | None = None,
    db: Session = Depends(get_db),
):
    """
    Start a trip by scanning any valid node QR code.
    Requires the user to be registered (POST /users/register) first.
    Sets status=ACTIVE and records the initial QR scan into node_checkins.
    """
    # Clean up any previously abandoned trips first
    sync_abandoned_trips(db)

    user_uuid = get_user_uuid(firebase_uid, db)

    node = db.query(Node).filter(Node.qr_code_value == qr_value).first()
    if not node:
        raise HTTPException(status_code=400, detail="Invalid QR Code")

    now = datetime.now(timezone.utc)
    trip = Trip(
        user_id          = user_uuid,
        site_id          = node.site_id,
        starting_node_id = node.id,
        status           = "ACTIVE",
        started_at       = now,
        last_activity_at = now,
        is_active        = True,
        entry_lat        = entry_lat,
        entry_lng        = entry_lng,
    )
    db.add(trip)
    db.flush()

    # Record the trip-starting QR scan in node_checkins
    checkin = NodeCheckIn(
        trip_id    = trip.id,
        node_id    = node.id,
        site_id    = node.site_id,
        user_id    = user_uuid,
        scan_type  = "trip_start",
        scanned_at = now,
    )
    db.add(checkin)
    db.commit()
    db.refresh(trip)

    return {
        "message":           "Trip Started",
        "trip_id":           trip.id,
        "site_id":           node.site_id,
        "starting_node_id":  node.id,
        "status":            trip.status,
        "started_from_king": bool(node.is_king),
    }


@router.post("/checkin")
def checkin_node(
    trip_id: int,
    qr_value: str,
    db: Session = Depends(get_db),
):
    """
    Record an in-journey node check-in by scanning a QR code during an active trip.
    Refreshes the trip's last_activity_at timestamp to prevent auto-abandonment.
    """
    sync_abandoned_trips(db)

    trip = db.query(Trip).filter(Trip.id == trip_id).first()
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")

    if not trip.is_active or trip.status != "ACTIVE":
        raise HTTPException(status_code=400, detail=f"Cannot check in: trip is {trip.status or 'inactive'}")

    node = db.query(Node).filter(Node.qr_code_value == qr_value).first()
    if not node:
        raise HTTPException(status_code=400, detail="Invalid QR Code")

    if node.site_id != trip.site_id:
        raise HTTPException(status_code=400, detail="Node does not belong to this trip's heritage site")

    now = datetime.now(timezone.utc)
    trip.last_activity_at = now

    # Record checkin
    checkin = NodeCheckIn(
        trip_id    = trip.id,
        node_id    = node.id,
        site_id    = node.site_id,
        user_id    = trip.user_id,
        scan_type  = "checkin",
        scanned_at = now,
    )
    db.add(checkin)
    db.commit()

    return {
        "message":   "Node checked in successfully",
        "trip_id":   trip.id,
        "node_id":   node.id,
        "node_name": node.name,
        "site_id":   node.site_id,
    }


@router.post("/end")
def end_trip(
    trip_id: int,
    visited_nodes: str | None = None,   # comma-separated node IDs e.g. "1,2,5"
    db: Session = Depends(get_db),
):
    """
    End an active trip. Transitions status to COMPLETED and records visit history.
    """
    trip = db.query(Trip).filter(Trip.id == trip_id).first()
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")

    now = datetime.now(timezone.utc)
    trip.is_active = False
    trip.status    = "COMPLETED"
    trip.ended_at  = now
    trip.last_activity_at = now

    site      = db.query(HeritageSite).filter(HeritageSite.id == trip.site_id).first()
    site_name = site.name if site else "Unknown"

    node_ids = []
    if visited_nodes:
        node_ids = [int(x.strip()) for x in visited_nodes.split(",") if x.strip().isdigit()]

    # If starting node is known and not in node_ids, include it
    if trip.starting_node_id and trip.starting_node_id not in node_ids:
        node_ids.insert(0, trip.starting_node_id)

    total_nodes     = db.query(Node).filter(Node.site_id == trip.site_id).count()
    nodes_completed = len(node_ids)

    # Sync node checkins for any visited nodes not yet in node_checkins
    existing_checked_nodes = {
        c.node_id for c in db.query(NodeCheckIn.node_id).filter(NodeCheckIn.trip_id == trip.id).all()
    }
    for nid in node_ids:
        if nid not in existing_checked_nodes:
            db.add(NodeCheckIn(
                trip_id    = trip.id,
                node_id    = nid,
                site_id    = trip.site_id,
                user_id    = trip.user_id,
                scan_type  = "checkin",
                scanned_at = now,
            ))

    duration_mins = None
    if trip.started_at and trip.ended_at:
        started = trip.started_at
        ended   = trip.ended_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if ended.tzinfo is None:
            ended   = ended.replace(tzinfo=timezone.utc)
        delta = ended - started
        duration_mins = max(0, int(delta.total_seconds() / 60))

    try:
        from app.models import UserVisitHistory
        uvh = (
            db.query(UserVisitHistory)
            .filter(UserVisitHistory.user_id == trip.user_id, UserVisitHistory.trip_id == trip.id)
            .first()
        )
        if uvh:
            uvh.ended_at = trip.ended_at
            uvh.duration_mins = duration_mins
            uvh.nodes_visited = node_ids
            uvh.nodes_completed = nodes_completed
            uvh.completed = True
        elif trip.user_id:
            uvh = UserVisitHistory(
                user_id         = trip.user_id,
                site_id         = trip.site_id,
                trip_id         = trip.id,
                site_name       = site_name,
                nodes_visited   = node_ids,
                total_nodes     = total_nodes,
                nodes_completed = nodes_completed,
                completed       = True,
                visited_at      = trip.started_at,
                ended_at        = trip.ended_at,
                duration_mins   = duration_mins,
                entry_lat       = trip.entry_lat,
                entry_lng       = trip.entry_lng,
            )
            db.add(uvh)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[end_trip] visit history insert failed: {e}")

    db.commit()
    return {
        "message":       "Trip Ended",
        "status":        trip.status,
        "duration_mins": duration_mins,
    }


@router.get("/active/{firebase_uid}")
def get_active_trip(firebase_uid: str, db: Session = Depends(get_db)):
    """Return the current active trip for a user, if any."""
    sync_abandoned_trips(db)

    user_uuid = get_user_uuid(firebase_uid, db)
    trip = (
        db.query(Trip)
        .filter(Trip.user_id == user_uuid, Trip.is_active == True, Trip.status == "ACTIVE")
        .order_by(Trip.started_at.desc())
        .first()
    )
    if not trip:
        return {"active": False}
    return {
        "active":      True,
        "trip_id":     trip.id,
        "site_id":     trip.site_id,
        "status":      trip.status,
        "started_at":  trip.started_at.isoformat() if trip.started_at else None,
    }