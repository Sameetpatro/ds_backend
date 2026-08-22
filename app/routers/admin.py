# app/routers/admin.py

import os
import json
import base64
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Header, Query, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy import func, extract, or_, and_, desc

from app.database import get_db
from app.models import (
    User, HeritageSite, SiteImage, Node, NodeImage, Trip,
    NodeCheckIn, SiteRating, NodeRating, TripReview, UserVisitHistory,
    Recommendation, Prompt, GlobalStats,
)
from app.routers.trips import sync_abandoned_trips
from app.schemas import (
    AdminDashboardStatsResponse,
    MonthlyTripAnalyticsResponse,
    MonthlyTripItem,
    RatingAnalyticsResponse,
    RatingDistribution,
    SiteRatingDetail,
    TopSiteRanking,
    AdminTripDetailResponse,
    AdminTripListItem,
    AdminTouristProfile,
    AdminSiteSummary,
    AdminNodeSummary,
    NodeCheckInResponse,
    RecentActivityItem,
    AdminUserResponse,
    AdminAccountResponse,
    CheckInAnalyticsResponse,
    CheckInSiteSummary,
    CheckInNodeSummary,
    RoleUpdateRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin Portal"])

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "dharohar_admin_secret_key_2026")
security = HTTPBearer(auto_error=False)


# ─────────────────────────────────────────────────────────────
# Admin Authentication & Authorization Dependency
# ─────────────────────────────────────────────────────────────

def _decode_jwt_payload_unverified(token: str) -> dict:
    """Safely extracts claims from JWT / Firebase token without external SDK dependencies."""
    try:
        parts = token.split(".")
        if len(parts) >= 2:
            payload_b64 = parts[1]
            rem = len(payload_b64) % 4
            if rem > 0:
                payload_b64 += "=" * (4 - rem)
            payload_json = base64.urlsafe_b64decode(payload_b64.encode("utf-8")).decode("utf-8")
            return json.loads(payload_json)
    except Exception:
        pass
    return {}


def get_current_admin(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
    x_firebase_uid: Optional[str] = Header(None, alias="X-Firebase-UID"),
    x_user_email: Optional[str] = Header(None, alias="X-User-Email"),
    x_user_id: Optional[str] = Header(None, alias="X-User-ID"),
    bearer_creds: Optional[HTTPAuthorizationCredentials] = Security(security),
    db: Session = Depends(get_db),
) -> dict:
    """
    Identifies the authenticated admin user from their session token / credentials,
    and verifies that their database role is 'ADMIN' or 'SUPERADMIN'.

    Accepts:
    1. Authorization: Bearer <token> (Firebase Auth ID token, JWT, session token, or UID)
    2. X-Admin-Token, X-Firebase-UID, X-User-Email, or X-User-ID headers
    3. X-Admin-Key (optional fallback for internal server automation)

    Never trusts a client-supplied role claim without database verification.
    """
    # 1. Check optional server API key fallback
    if x_admin_key and x_admin_key == ADMIN_API_KEY:
        return {
            "auth_type": "api_key",
            "role": "ADMIN",
            "user_id": None,
            "email": "system_admin@dharohar.gov.in",
            "display_name": "System Admin",
        }

    # 2. Extract token from Bearer or custom headers
    token = None
    if bearer_creds and bearer_creds.credentials:
        token = bearer_creds.credentials.strip()
    elif x_admin_token:
        token = x_admin_token.strip()
    elif x_firebase_uid:
        token = x_firebase_uid.strip()
    elif x_user_email:
        token = x_user_email.strip()
    elif x_user_id:
        token = x_user_id.strip()

    if not token and not x_admin_key:
        raise HTTPException(
            status_code=401,
            detail="Admin authentication required. Please log in with your admin credentials."
        )

    user = None

    # A. If token looks like a JWT (Firebase ID token, OAuth token, etc.), extract claims
    if token and "." in token:
        claims = _decode_jwt_payload_unverified(token)
        # Firebase / JWT standard claims: user_id, sub, email, uid
        sub = claims.get("user_id") or claims.get("sub") or claims.get("uid")
        email = claims.get("email")

        if sub:
            user = db.query(User).filter(User.firebase_uid == sub).first()
            if not user:
                try:
                    uid_obj = uuid.UUID(sub)
                    user = db.query(User).filter(User.id == uid_obj).first()
                except Exception:
                    pass

        if not user and email:
            user = db.query(User).filter(func.lower(User.email) == email.strip().lower()).first()

    # B. Direct lookup by Firebase UID, email, or UUID string
    if not user and token:
        # Search by firebase_uid
        user = db.query(User).filter(User.firebase_uid == token).first()

        # Search by email
        if not user and "@" in token:
            user = db.query(User).filter(func.lower(User.email) == token.strip().lower()).first()

        # Search by UUID
        if not user:
            try:
                uid_val = uuid.UUID(token)
                user = db.query(User).filter(User.id == uid_val).first()
            except Exception:
                pass

    # C. Check explicit email or ID headers if provided
    if not user and x_user_email:
        user = db.query(User).filter(func.lower(User.email) == x_user_email.strip().lower()).first()

    if not user and x_user_id:
        try:
            uid_val = uuid.UUID(x_user_id.strip())
            user = db.query(User).filter(User.id == uid_val).first()
        except Exception:
            user = db.query(User).filter(User.firebase_uid == x_user_id.strip()).first()

    # If no user found in database
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Authentication failed: No user account matches the provided credentials."
        )

    # Verify user's database role
    user_role = (user.role or "TOURIST").strip().upper()
    if user_role not in ("ADMIN", "SUPERADMIN"):
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: User '{user.email or user.display_name}' has role '{user_role}', but ADMIN or SUPERADMIN privileges are required."
        )

    return {
        "auth_type": "session",
        "role": user_role,
        "user_id": str(user.id),
        "firebase_uid": user.firebase_uid,
        "email": user.email,
        "display_name": user.display_name or "Administrator",
    }


# ─────────────────────────────────────────────────────────────
# Admin Session / Profile Verification
# ─────────────────────────────────────────────────────────────

@router.get("/me")
def get_current_admin_profile(admin: dict = Depends(get_current_admin)):
    """
    Returns the currently authenticated admin's session profile.
    Used by the Admin Portal to verify credentials and role on page load.
    """
    return {
        "authenticated": True,
        "user_id": admin.get("user_id"),
        "firebase_uid": admin.get("firebase_uid"),
        "email": admin.get("email"),
        "display_name": admin.get("display_name"),
        "role": admin.get("role"),
    }


# ─────────────────────────────────────────────────────────────
# 1. Admin Dashboard Metrics (Cards)
# ─────────────────────────────────────────────────────────────

@router.get("/dashboard", response_model=dict)
@router.get("/dashboard/stats", response_model=dict)
def get_admin_dashboard(
    admin: dict = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """
    Returns core dashboard metric cards computed from real database tables:
    - Total registered users
    - Active/live trips (with stale trip auto-abandonment)
    - Total trips taken
    - Completed trips
    - Abandoned trips
    - Total mapped sites
    - Total nodes
    - Total node check-ins
    - Total visitor reviews
    - Overall average site rating
    """
    sync_abandoned_trips(db)

    total_registered_users = db.query(func.count(User.id)).scalar() or 0
    total_trips            = db.query(func.count(Trip.id)).scalar() or 0
    active_trips           = db.query(func.count(Trip.id)).filter(Trip.status == "ACTIVE", Trip.is_active == True).scalar() or 0
    completed_trips        = db.query(func.count(Trip.id)).filter(Trip.status == "COMPLETED").scalar() or 0
    abandoned_trips        = db.query(func.count(Trip.id)).filter(Trip.status == "ABANDONED").scalar() or 0

    total_mapped_sites     = db.query(func.count(HeritageSite.id)).scalar() or 0
    total_nodes            = db.query(func.count(Node.id)).scalar() or 0

    # Total check-ins (from node_checkins table, or fallback to sum of visit history)
    total_checkins_count   = db.query(func.count(NodeCheckIn.id)).scalar() or 0
    if total_checkins_count == 0:
        total_visits_history = db.query(func.count(UserVisitHistory.id)).scalar() or 0
        gs = db.query(GlobalStats).filter(GlobalStats.id == 1).first()
        lifetime_visits = gs.lifetime_visits if gs else 0
        total_checkins_count = max(total_visits_history, lifetime_visits)

    total_visitor_reviews  = db.query(func.count(TripReview.id)).scalar() or 0

    # Overall average site rating directly from underlying ratings table
    avg_rating_raw = db.query(func.avg(SiteRating.rating)).scalar()
    if avg_rating_raw is None:
        avg_rating_raw = db.query(func.avg(HeritageSite.rating)).scalar() or 0.0
    overall_average_site_rating = round(float(avg_rating_raw), 2)

    # ── Rating distribution ───────────────────────────────────────────
    dist_rows = db.query(SiteRating.rating, func.count(SiteRating.id)).group_by(SiteRating.rating).all()
    rating_distribution = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for r_val, count in dist_rows:
        if r_val in rating_distribution:
            rating_distribution[r_val] = count

    # ── Monthly trends ────────────────────────────────────────────────
    monthly_rows = (
        db.query(
            extract("year", Trip.started_at).label("yr"),
            extract("month", Trip.started_at).label("mo"),
            func.count(Trip.id).label("trip_count"),
        )
        .filter(Trip.started_at.isnot(None))
        .group_by("yr", "mo")
        .order_by("yr", "mo")
        .all()
    )
    month_abbr = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    monthly_trends = [
        {"month": f"{month_abbr[int(mo)]} {int(yr)}", "trips": cnt}
        for yr, mo, cnt in monthly_rows if yr and mo
    ]

    return {
        "success": True,
        "metrics": {
            "total_registered_users": total_registered_users,
            "active_trips": active_trips,
            "total_trips": total_trips,
            "completed_trips": completed_trips,
            "abandoned_trips": abandoned_trips,
            "total_mapped_sites": total_mapped_sites,
            "total_nodes": total_nodes,
            "total_node_checkins": total_checkins_count,
            "total_visitor_reviews": total_visitor_reviews,
            "overall_average_site_rating": overall_average_site_rating,
        },
        "rating_distribution": rating_distribution,
        "monthly_trends": monthly_trends,
    }


# ─────────────────────────────────────────────────────────────
# 2. Trip & QR Analytics (Monthly Aggregations)
# ─────────────────────────────────────────────────────────────

@router.get("/analytics/trips", response_model=MonthlyTripAnalyticsResponse)
def get_trip_analytics(
    admin: dict = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """
    Returns monthly aggregated historical analytics:
    - tripStarts: [{ "month": "YYYY-MM", "count": 120 }, ...]
    - qrScans:    [{ "month": "YYYY-MM", "count": 300 }, ...]
    Database-level SQL aggregation.
    """
    # 1. Monthly trip starts
    trip_rows = (
        db.query(
            extract("year", Trip.started_at).label("yr"),
            extract("month", Trip.started_at).label("mo"),
            func.count(Trip.id).label("count"),
        )
        .filter(Trip.started_at.isnot(None))
        .group_by("yr", "mo")
        .order_by("yr", "mo")
        .all()
    )
    trip_starts = [
        MonthlyTripItem(month=f"{int(yr):04d}-{int(mo):02d}", count=int(cnt))
        for yr, mo, cnt in trip_rows if yr and mo
    ]

    # 2. Monthly QR scans (from node_checkins, with fallback to trip starts if none recorded yet)
    scan_rows = (
        db.query(
            extract("year", NodeCheckIn.scanned_at).label("yr"),
            extract("month", NodeCheckIn.scanned_at).label("mo"),
            func.count(NodeCheckIn.id).label("count"),
        )
        .filter(NodeCheckIn.scanned_at.isnot(None))
        .group_by("yr", "mo")
        .order_by("yr", "mo")
        .all()
    )
    qr_scans = [
        MonthlyTripItem(month=f"{int(yr):04d}-{int(mo):02d}", count=int(cnt))
        for yr, mo, cnt in scan_rows if yr and mo
    ]

    # Fallback to trip starts if qrScans empty
    if not qr_scans and trip_starts:
        qr_scans = [MonthlyTripItem(month=item.month, count=item.count) for item in trip_starts]

    return MonthlyTripAnalyticsResponse(
        tripStarts=trip_starts,
        qrScans=qr_scans,
    )


# ─────────────────────────────────────────────────────────────
# 3. Rating Analytics & Distribution
# ─────────────────────────────────────────────────────────────

@router.get("/analytics/ratings", response_model=RatingAnalyticsResponse)
def get_rating_analytics(
    admin: dict = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """
    Returns:
    - Overall star rating distribution (1 to 5 stars)
    - Total reviews
    - Overall average rating calculated from underlying rating records
    - Per-site rating breakdown (site ID, name, average rating, review count, 1-5 star counts)
    """
    # 1. Overall star distribution
    dist_rows = db.query(SiteRating.rating, func.count(SiteRating.id)).group_by(SiteRating.rating).all()
    dist_dict = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    total_reviews = 0
    sum_ratings = 0
    for r_val, count in dist_rows:
        if r_val in dist_dict:
            dist_dict[r_val] = count
            total_reviews += count
            sum_ratings += r_val * count

    overall_avg = round(sum_ratings / total_reviews, 2) if total_reviews > 0 else 0.0

    # 2. Per-site ratings breakdown
    sites = db.query(HeritageSite).order_by(HeritageSite.id).all()
    per_site = []
    for site in sites:
        site_ratings = (
            db.query(SiteRating.rating, func.count(SiteRating.id))
            .filter(SiteRating.site_id == site.id)
            .group_by(SiteRating.rating)
            .all()
        )
        s_dist = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        s_total = 0
        s_sum = 0
        for r_val, count in site_ratings:
            if r_val in s_dist:
                s_dist[r_val] = count
                s_total += count
                s_sum += r_val * count

        s_avg = round(s_sum / s_total, 2) if s_total > 0 else round(site.rating or 0.0, 2)

        per_site.append(SiteRatingDetail(
            site_id=site.id,
            site_name=site.name,
            average_rating=s_avg,
            review_count=s_total,
            five_star=s_dist[5],
            four_star=s_dist[4],
            three_star=s_dist[3],
            two_star=s_dist[2],
            one_star=s_dist[1],
        ))

    return RatingAnalyticsResponse(
        rating_distribution=RatingDistribution(
            five_star=dist_dict[5],
            four_star=dist_dict[4],
            three_star=dist_dict[3],
            two_star=dist_dict[2],
            one_star=dist_dict[1],
        ),
        total_reviews=total_reviews,
        overall_average_rating=overall_avg,
        per_site_ratings=per_site,
    )


# ─────────────────────────────────────────────────────────────
# 4. Top Sites (Bayesian Weighted Rating Ranking)
# ─────────────────────────────────────────────────────────────

@router.get("/analytics/top-sites", response_model=List[TopSiteRanking])
def get_top_sites(
    min_reviews: int = Query(1, ge=0, description="Minimum reviews parameter for weighting"),
    admin: dict = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """
    Ranks heritage sites using the Bayesian Weighted Rating (IMDB formula):
        WR = (v / (v + m)) * R + (m / (v + m)) * C
    Where:
        v = review count for the site
        m = minimum review threshold (default 3)
        R = site average rating
        C = mean rating across all catalog sites
    This prevents 1-review 5-star sites from unfairly outranking well-reviewed sites.
    """
    overall_avg_raw = db.query(func.avg(SiteRating.rating)).scalar()
    C = float(overall_avg_raw) if overall_avg_raw else 4.0
    m = max(1, min_reviews)

    sites = db.query(HeritageSite).all()
    ranked = []

    for site in sites:
        r_rows = db.query(SiteRating.rating).filter(SiteRating.site_id == site.id).all()
        v = len(r_rows)
        if v > 0:
            R = sum(r[0] for r in r_rows) / v
        else:
            R = float(site.rating or 0.0)

        bayesian_score = ((v / (v + m)) * R) + ((m / (v + m)) * C) if (v + m) > 0 else R

        ranked.append({
            "site_id": site.id,
            "site_name": site.name,
            "average_rating": round(R, 2),
            "review_count": v,
            "bayesian_rating": round(bayesian_score, 2),
        })

    # Sort descending by Bayesian rating, review count, and average rating
    ranked.sort(key=lambda s: (s["bayesian_rating"], s["review_count"], s["average_rating"]), reverse=True)

    result = []
    for rank_idx, s in enumerate(ranked, 1):
        result.append(TopSiteRanking(
            rank=rank_idx,
            site_id=s["site_id"],
            site_name=s["site_name"],
            average_rating=s["average_rating"],
            review_count=s["review_count"],
            bayesian_rating=s["bayesian_rating"],
        ))

    return result


# ─────────────────────────────────────────────────────────────
# 5. Node Check-ins & QR Analytics
# ─────────────────────────────────────────────────────────────

@router.get("/analytics/checkins", response_model=CheckInAnalyticsResponse)
@router.get("/checkins", response_model=CheckInAnalyticsResponse)
def get_checkin_analytics(
    admin: dict = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """
    Provides comprehensive node check-in and QR scan analytics:
    - Total node check-ins
    - Trip start scans vs in-journey check-ins
    - Check-ins per site
    - Check-ins per node
    - Most frequently scanned nodes
    - Scans over time
    """
    total_checkins = db.query(func.count(NodeCheckIn.id)).scalar() or 0
    trip_start_scans = db.query(func.count(NodeCheckIn.id)).filter(NodeCheckIn.scan_type == "trip_start").scalar() or 0
    subsequent_checkins = total_checkins - trip_start_scans

    # Per site check-ins
    site_counts = (
        db.query(HeritageSite.id, HeritageSite.name, func.count(NodeCheckIn.id))
        .join(NodeCheckIn, HeritageSite.id == NodeCheckIn.site_id)
        .group_by(HeritageSite.id, HeritageSite.name)
        .order_by(desc(func.count(NodeCheckIn.id)))
        .all()
    )
    checkins_per_site = [
        CheckInSiteSummary(site_id=sid, site_name=sname, total_checkins=cnt)
        for sid, sname, cnt in site_counts
    ]

    # Per node check-ins
    node_counts = (
        db.query(Node.id, Node.name, HeritageSite.id, HeritageSite.name, Node.is_king, func.count(NodeCheckIn.id))
        .join(NodeCheckIn, Node.id == NodeCheckIn.node_id)
        .join(HeritageSite, Node.site_id == HeritageSite.id)
        .group_by(Node.id, Node.name, HeritageSite.id, HeritageSite.name, Node.is_king)
        .order_by(desc(func.count(NodeCheckIn.id)))
        .all()
    )
    checkins_per_node = [
        CheckInNodeSummary(
            node_id=nid,
            node_name=nname,
            site_id=sid,
            site_name=sname,
            total_scans=cnt,
            is_king=bool(is_king),
        )
        for nid, nname, sid, sname, is_king, cnt in node_counts
    ]

    # Most frequently scanned nodes (top 10)
    most_frequent = checkins_per_node[:10]

    # Scans over time
    scan_time_rows = (
        db.query(
            extract("year", NodeCheckIn.scanned_at).label("yr"),
            extract("month", NodeCheckIn.scanned_at).label("mo"),
            func.count(NodeCheckIn.id).label("count"),
        )
        .filter(NodeCheckIn.scanned_at.isnot(None))
        .group_by("yr", "mo")
        .order_by("yr", "mo")
        .all()
    )
    scans_over_time = [
        MonthlyTripItem(month=f"{int(yr):04d}-{int(mo):02d}", count=int(cnt))
        for yr, mo, cnt in scan_time_rows if yr and mo
    ]

    return CheckInAnalyticsResponse(
        total_node_checkins=total_checkins,
        trip_start_scans=trip_start_scans,
        subsequent_checkins=subsequent_checkins,
        checkins_per_site=checkins_per_site,
        checkins_per_node=checkins_per_node,
        most_frequently_scanned_nodes=most_frequent,
        scans_over_time=scans_over_time,
    )


# ─────────────────────────────────────────────────────────────
# 6. Visitor Trips List (Filterable & Paginated)
# ─────────────────────────────────────────────────────────────

@router.get("/trips", response_model=List[AdminTripListItem])
def get_admin_trips(
    status: Optional[str] = Query(None, description="Filter by status: ACTIVE, COMPLETED, ABANDONED"),
    site_id: Optional[int] = Query(None, description="Filter by site ID"),
    user_id: Optional[str] = Query(None, description="Filter by user UUID or firebase_uid"),
    date_from: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    trip_id: Optional[int] = Query(None, description="Filter by specific trip ID"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: dict = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """
    Returns visitor/tourist trips with multi-parameter filtering and pagination.
    """
    sync_abandoned_trips(db)

    query = (
        db.query(
            Trip,
            User.display_name,
            User.email,
            HeritageSite.name,
            Node.name,
        )
        .outerjoin(User, Trip.user_id == User.id)
        .outerjoin(HeritageSite, Trip.site_id == HeritageSite.id)
        .outerjoin(Node, Trip.starting_node_id == Node.id)
    )

    if trip_id is not None:
        query = query.filter(Trip.id == trip_id)

    if status:
        query = query.filter(Trip.status == status.upper())

    if site_id is not None:
        query = query.filter(Trip.site_id == site_id)

    if user_id:
        try:
            u_uuid = uuid.UUID(user_id)
            query = query.filter(Trip.user_id == u_uuid)
        except Exception:
            # Try by firebase_uid
            matching_user = db.query(User).filter(User.firebase_uid == user_id).first()
            if matching_user:
                query = query.filter(Trip.user_id == matching_user.id)

    if date_from:
        try:
            df = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
            query = query.filter(Trip.started_at >= df)
        except Exception:
            pass

    if date_to:
        try:
            dt = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc) + timedelta(days=1)
            query = query.filter(Trip.started_at < dt)
        except Exception:
            pass

    trips_raw = query.order_by(Trip.started_at.desc()).limit(limit).offset(offset).all()

    result = []
    for trip, user_name, user_email, site_name, start_node_name in trips_raw:
        # Calculate duration in minutes
        duration_mins = None
        if trip.started_at:
            end_t = trip.ended_at or (trip.last_activity_at if trip.status == "ABANDONED" else datetime.now(timezone.utc))
            st = trip.started_at if trip.started_at.tzinfo else trip.started_at.replace(tzinfo=timezone.utc)
            et = end_t if end_t.tzinfo else end_t.replace(tzinfo=timezone.utc)
            duration_mins = max(0, int((et - st).total_seconds() / 60))

        checkin_count = db.query(func.count(NodeCheckIn.id)).filter(NodeCheckIn.trip_id == trip.id).scalar() or 0

        result.append(AdminTripListItem(
            trip_id=trip.id,
            user_id=str(trip.user_id) if trip.user_id else None,
            user_name=user_name,
            user_email=user_email,
            site_id=trip.site_id,
            site_name=site_name or "Unknown Site",
            starting_node_id=trip.starting_node_id,
            starting_node_name=start_node_name,
            start_time=trip.started_at,
            end_time=trip.ended_at,
            status=trip.status or ("ACTIVE" if trip.is_active else "COMPLETED"),
            trip_duration_mins=duration_mins,
            checkin_count=checkin_count,
        ))

    return result


# ─────────────────────────────────────────────────────────────
# 7. Trip Journey Details (Detailed Single Trip)
# ─────────────────────────────────────────────────────────────

@router.get("/trips/{trip_id}", response_model=AdminTripDetailResponse)
def get_admin_trip_detail(
    trip_id: int,
    admin: dict = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """
    Returns full journey details for a specific trip:
    - Tourist profile (User ID, Name, Email, Phone, Avatar)
    - Heritage site visited
    - Starting node
    - Start time, End time, Status
    - Trip duration
    - All node check-ins during the trip in chronological order
    """
    sync_abandoned_trips(db)

    trip = db.query(Trip).filter(Trip.id == trip_id).first()
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")

    site = db.query(HeritageSite).filter(HeritageSite.id == trip.site_id).first()
    user = db.query(User).filter(User.id == trip.user_id).first() if trip.user_id else None
    start_node = db.query(Node).filter(Node.id == trip.starting_node_id).first() if trip.starting_node_id else None

    # Calculate duration
    duration_mins = None
    if trip.started_at:
        end_t = trip.ended_at or (trip.last_activity_at if trip.status == "ABANDONED" else datetime.now(timezone.utc))
        st = trip.started_at if trip.started_at.tzinfo else trip.started_at.replace(tzinfo=timezone.utc)
        et = end_t if end_t.tzinfo else end_t.replace(tzinfo=timezone.utc)
        duration_mins = max(0, int((et - st).total_seconds() / 60))

    # Node checkins
    checkins_raw = (
        db.query(NodeCheckIn, Node.name, HeritageSite.name)
        .join(Node, NodeCheckIn.node_id == Node.id)
        .join(HeritageSite, NodeCheckIn.site_id == HeritageSite.id)
        .filter(NodeCheckIn.trip_id == trip.id)
        .order_by(NodeCheckIn.scanned_at.asc())
        .all()
    )

    node_checkins = [
        NodeCheckInResponse(
            id=c.id,
            node_id=c.node_id,
            node_name=nname,
            site_id=c.site_id,
            site_name=sname,
            scan_type=c.scan_type,
            scanned_at=c.scanned_at,
        )
        for c, nname, sname in checkins_raw
    ]

    return AdminTripDetailResponse(
        trip_id=trip.id,
        tourist=AdminTouristProfile(
            user_id=str(user.id) if user else "",
            display_name=user.display_name if user else None,
            email=user.email if user else None,
            phone=user.phone if user else None,
            avatar_url=user.avatar_url if user else None,
        ) if user else None,
        heritage_site=AdminSiteSummary(
            site_id=site.id if site else trip.site_id,
            site_name=site.name if site else "Unknown Site",
            location=site.location if site else None,
        ),
        starting_node=AdminNodeSummary(
            node_id=start_node.id,
            node_name=start_node.name,
            sequence_order=start_node.sequence_order,
            is_king=bool(start_node.is_king),
        ) if start_node else None,
        start_time=trip.started_at,
        end_time=trip.ended_at,
        status=trip.status or ("ACTIVE" if trip.is_active else "COMPLETED"),
        trip_duration_mins=duration_mins,
        node_checkins=node_checkins,
    )


# ─────────────────────────────────────────────────────────────
# 8. Recent / Live Tour Activity Feed
# ─────────────────────────────────────────────────────────────

@router.get("/activity/recent", response_model=List[RecentActivityItem])
def get_recent_activity(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    admin: dict = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """
    Activity feed showing the latest tour events:
    - User scanned QR code at node
    - Trip started / completed
    - Newest first. Polled by Admin Portal for live updates.
    """
    sync_abandoned_trips(db)

    # 1. Recent check-ins / scans
    checkin_rows = (
        db.query(
            NodeCheckIn,
            User.display_name,
            HeritageSite.name,
            Node.name,
            Trip.status,
        )
        .outerjoin(User, NodeCheckIn.user_id == User.id)
        .outerjoin(HeritageSite, NodeCheckIn.site_id == HeritageSite.id)
        .outerjoin(Node, NodeCheckIn.node_id == Node.id)
        .outerjoin(Trip, NodeCheckIn.trip_id == Trip.id)
        .order_by(NodeCheckIn.scanned_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )

    activities = []
    for c, user_name, site_name, node_name, trip_status in checkin_rows:
        uname = user_name or "Visitor"
        sname = site_name or "Heritage Monument"
        nname = node_name or f"Node {c.node_id}"
        t_status = trip_status or "ACTIVE"

        if c.scan_type == "trip_start":
            headline = f"{uname} started Trip #{c.trip_id} at {nname} ({sname})"
            desc = f"Trip #{c.trip_id} initiated. Status: {t_status}"
            act_type = "trip_start"
        else:
            headline = f"{uname} scanned {nname} at {sname}"
            desc = f"Node check-in during Trip #{c.trip_id}. Status: {t_status}"
            act_type = "node_checkin"

        activities.append(RecentActivityItem(
            activity_id=f"checkin_{c.id}",
            activity_type=act_type,
            trip_id=c.trip_id,
            user_id=str(c.user_id) if c.user_id else None,
            user_name=uname,
            site_id=c.site_id,
            site_name=sname,
            node_id=c.node_id,
            node_name=nname,
            status=t_status,
            timestamp=c.scanned_at or datetime.now(timezone.utc),
            headline=headline,
            description=desc,
        ))

    return activities


# ─────────────────────────────────────────────────────────────
# 9. Tourists / Users Management
# ─────────────────────────────────────────────────────────────

@router.get("/users", response_model=List[AdminUserResponse])
def get_admin_users(
    q: Optional[str] = Query(None, description="Search name, email, or phone"),
    role: Optional[str] = Query(None, description="Filter by role (TOURIST, ADMIN)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: dict = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """
    Returns registered users with lifetime metrics:
    - User ID (UUID)
    - Username / Name
    - Email, Phone
    - Role
    - Trips taken
    - Sites visited
    - Reviews submitted
    - Registration date
    - Last active / login date
    """
    query = db.query(User)

    if q:
        search_pattern = f"%{q.strip()}%"
        query = query.filter(
            or_(
                User.display_name.ilike(search_pattern),
                User.email.ilike(search_pattern),
                User.phone.ilike(search_pattern),
            )
        )

    if role:
        query = query.filter(User.role == role.upper())

    users = query.order_by(User.created_at.desc()).limit(limit).offset(offset).all()

    result = []
    for user in users:
        trips_taken = db.query(func.count(Trip.id)).filter(Trip.user_id == user.id).scalar() or 0
        sites_visited = db.query(func.count(func.distinct(Trip.site_id))).filter(Trip.user_id == user.id).scalar() or 0
        reviews_submitted = db.query(func.count(TripReview.id)).filter(TripReview.user_id == user.id).scalar() or 0

        result.append(AdminUserResponse(
            user_id=str(user.id),
            username=user.display_name,
            email=user.email,
            phone=user.phone,
            role=user.role or "TOURIST",
            trips_taken=trips_taken,
            sites_visited=sites_visited,
            reviews_submitted=reviews_submitted,
            registered_at=user.created_at,
            last_active_at=user.last_active_at,
        ))

    return result


# ─────────────────────────────────────────────────────────────
# 10. Admin Accounts (Protected, Safe Non-Sensitive Info)
# ─────────────────────────────────────────────────────────────

@router.get("/admins", response_model=List[AdminAccountResponse])
def get_admin_accounts(
    admin: dict = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """
    Returns all administrative accounts with non-sensitive fields:
    - User ID
    - Username
    - Email
    - Role
    - Registration date
    - Last active date
    - Platform
    Never returns passwords, hashes, tokens, or auth secrets.
    """
    admin_users = (
        db.query(User)
        .filter(User.role.in_(["ADMIN", "SUPERADMIN"]))
        .order_by(User.created_at.asc())
        .all()
    )

    return [
        AdminAccountResponse(
            user_id=str(u.id),
            username=u.display_name or "Administrator",
            email=u.email,
            role=u.role,
            registered_at=u.created_at,
            last_active_at=u.last_active_at,
            platform="web_portal",
        )
        for u in admin_users
    ]


@router.post("/users/{user_id}/role")
def update_user_role(
    user_id: str,
    payload: RoleUpdateRequest,
    admin: dict = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """
    Promote or change a user's role (e.g. to ADMIN or TOURIST).
    """
    allowed_roles = ["TOURIST", "ADMIN", "SUPERADMIN"]
    new_role = payload.role.strip().upper()
    if new_role not in allowed_roles:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {allowed_roles}")

    try:
        u_uuid = uuid.UUID(user_id)
        user = db.query(User).filter(User.id == u_uuid).first()
    except Exception:
        user = db.query(User).filter(User.firebase_uid == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.role = new_role
    db.commit()
    db.refresh(user)

    return {
        "success": True,
        "user_id": str(user.id),
        "username": user.display_name,
        "email": user.email,
        "role": user.role,
    }


# ─────────────────────────────────────────────────────────────
# 11. Heritage Sites Admin List & Detail Analytics
# ─────────────────────────────────────────────────────────────

@router.get("/sites")
def get_admin_sites(
    admin: dict = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """List all mapped sites with aggregate admin counts."""
    sites = db.query(HeritageSite).order_by(HeritageSite.id).all()
    result = []
    for s in sites:
        node_cnt = db.query(func.count(Node.id)).filter(Node.site_id == s.id).scalar() or 0
        trip_cnt = db.query(func.count(Trip.id)).filter(Trip.site_id == s.id).scalar() or 0
        review_cnt = db.query(func.count(SiteRating.id)).filter(SiteRating.site_id == s.id).scalar() or 0
        img = db.query(SiteImage.image_url).filter(SiteImage.site_id == s.id).first()

        result.append({
            "id": s.id,
            "name": s.name,
            "location": s.location,
            "latitude": s.latitude,
            "longitude": s.longitude,
            "rating": round(float(s.rating or 0.0), 2),
            "node_count": node_cnt,
            "trip_count": trip_cnt,
            "review_count": review_cnt,
            "image_url": img[0] if img else None,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        })
    return result


@router.get("/sites/{site_id}/analytics")
def get_site_detailed_analytics(
    site_id: int,
    admin: dict = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Detailed analytics for a single heritage site."""
    site = db.query(HeritageSite).filter(HeritageSite.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")

    nodes = db.query(Node).filter(Node.site_id == site_id).order_by(Node.sequence_order).all()
    trip_count = db.query(func.count(Trip.id)).filter(Trip.site_id == site_id).scalar() or 0
    active_trips = db.query(func.count(Trip.id)).filter(Trip.site_id == site_id, Trip.status == "ACTIVE").scalar() or 0
    total_checkins = db.query(func.count(NodeCheckIn.id)).filter(NodeCheckIn.site_id == site_id).scalar() or 0

    # Rating distribution for this site
    r_rows = db.query(SiteRating.rating, func.count(SiteRating.id)).filter(SiteRating.site_id == site_id).group_by(SiteRating.rating).all()
    s_dist = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for r_val, cnt in r_rows:
        if r_val in s_dist:
            s_dist[r_val] = cnt

    return {
        "site_id": site.id,
        "name": site.name,
        "location": site.location,
        "rating": round(float(site.rating or 0.0), 2),
        "total_nodes": len(nodes),
        "total_trips": trip_count,
        "active_trips": active_trips,
        "total_checkins": total_checkins,
        "rating_distribution": s_dist,
        "nodes": [
            {
                "id": n.id,
                "name": n.name,
                "sequence_order": n.sequence_order,
                "is_king": n.is_king,
                "avg_rating": n.avg_rating,
                "rating_count": n.rating_count,
                "checkin_count": db.query(func.count(NodeCheckIn.id)).filter(NodeCheckIn.node_id == n.id).scalar() or 0,
            }
            for n in nodes
        ],
    }


# ─────────────────────────────────────────────────────────────
# 12. Seeding & Content Endpoints (Existing Compatible)
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


class RecommendationPayload(BaseModel):
    type: str
    name: str
    latitude: float
    longitude: float
    description: Optional[str] = None


class SeedBulkRequest(BaseModel):
    site: SitePayload
    nodes: List[NodePayload]
    landmarks: Optional[LandmarksPayload] = None
    recommendations: List[RecommendationPayload] = []


class SeedPromptRequest(BaseModel):
    site_id: int
    node_id: Optional[int] = None
    prompt_text: str
    title: Optional[str] = None


@router.post("/seed-bulk")
def seed_bulk(payload: SeedBulkRequest, db: Session = Depends(get_db)):
    king_count = sum(1 for n in payload.nodes if n.is_king)
    if king_count != 1:
        raise HTTPException(
            status_code=400,
            detail=f"Exactly ONE king node must be defined per site. Found: {king_count}"
        )

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

    for order, url in enumerate(payload.site.images):
        if url:
            db.add(SiteImage(site_id=site.id, image_url=url, display_order=order))

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

        for order, url in enumerate(node_data.images):
            if url:
                db.add(NodeImage(node_id=node.id, image_url=url, display_order=order))

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


@router.post("/seed-prompt")
def seed_prompt(payload: SeedPromptRequest, db: Session = Depends(get_db)):
    site = db.query(HeritageSite).filter(HeritageSite.id == payload.site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail=f"Site {payload.site_id} not found.")

    if payload.node_id is not None:
        node = db.query(Node).filter(Node.id == payload.node_id, Node.site_id == payload.site_id).first()
        if not node:
            raise HTTPException(status_code=404, detail=f"Node {payload.node_id} not found.")

    if payload.title:
        title = payload.title
    elif payload.node_id is not None:
        node_obj = db.query(Node).filter(Node.id == payload.node_id).first()
        title = f"{site.name} - {node_obj.name if node_obj else f'Node {payload.node_id}'}"
    else:
        title = f"{site.name} - General"

    existing = db.query(Prompt).filter(Prompt.site_id == payload.site_id, Prompt.node_id == payload.node_id).first()
    if existing:
        existing.title = title
        existing.content = payload.prompt_text
        db.commit()
        return {"success": True, "action": "updated", "site_id": payload.site_id, "node_id": payload.node_id, "title": title}

    db.add(Prompt(site_id=payload.site_id, node_id=payload.node_id, title=title, content=payload.prompt_text))
    db.commit()

    return {"success": True, "action": "created", "site_id": payload.site_id, "node_id": payload.node_id, "title": title}


@router.post("/add-recommendations")
def add_recommendations(site_id: int, recs: List[RecommendationPayload], db: Session = Depends(get_db)):
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
            description=rec.description,
        ))

    db.commit()
    return {"success": True, "site_id": site_id}