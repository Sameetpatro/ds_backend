# app/routers/dashboard.py
# Admin dashboard stats — all computed from real PostgreSQL data.

import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, case, extract, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    User, HeritageSite, SiteImage, Node, Trip,
    UserVisitHistory, SiteRating, TripReview, GlobalStats,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin Dashboard"])


@router.get("/dashboard/stats")
def get_dashboard_stats(db: Session = Depends(get_db)):
    """
    Returns all data needed by the frontend admin dashboard.
    Every value is computed from real database tables — nothing is hardcoded.
    """

    # ── 1. Core counts ───────────────────────────────────────────────────
    total_registered_users = db.query(func.count(User.id)).scalar() or 0

    total_trips = db.query(func.count(Trip.id)).scalar() or 0
    active_trips = (
        db.query(func.count(Trip.id))
        .filter(Trip.is_active == True)
        .scalar() or 0
    )
    completed_trips = (
        db.query(func.count(Trip.id))
        .filter(Trip.is_active == False, Trip.ended_at.isnot(None))
        .scalar() or 0
    )

    total_sites = db.query(func.count(HeritageSite.id)).scalar() or 0
    total_nodes = db.query(func.count(Node.id)).scalar() or 0
    total_reviews = db.query(func.count(TripReview.id)).scalar() or 0

    # Total node check-ins (visit history rows)
    total_visits_history = db.query(func.count(UserVisitHistory.id)).scalar() or 0

    # Also grab global_stats lifetime_visits as a fallback/secondary metric
    gs = db.query(GlobalStats).filter(GlobalStats.id == 1).first()
    lifetime_visits = gs.lifetime_visits if gs else 0

    # Average site rating
    avg_site_rating_raw = db.query(func.avg(SiteRating.rating)).scalar()
    average_site_rating = round(float(avg_site_rating_raw), 1) if avg_site_rating_raw else 0.0

    # ── 2. Rating distribution ───────────────────────────────────────────
    rating_dist_rows = (
        db.query(SiteRating.rating, func.count(SiteRating.id))
        .group_by(SiteRating.rating)
        .all()
    )
    rating_distribution = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for rating_val, cnt in rating_dist_rows:
        if rating_val in rating_distribution:
            rating_distribution[rating_val] = cnt

    # ── 3. Monthly trends (last 6 months of trips) ──────────────────────
    six_months_ago = datetime.now(timezone.utc) - timedelta(days=180)
    monthly_rows = (
        db.query(
            extract("year", Trip.started_at).label("yr"),
            extract("month", Trip.started_at).label("mo"),
            func.count(Trip.id).label("trip_count"),
        )
        .filter(Trip.started_at >= six_months_ago)
        .group_by("yr", "mo")
        .order_by("yr", "mo")
        .all()
    )

    month_abbr = [
        "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]
    monthly_trends = []
    for yr, mo, cnt in monthly_rows:
        monthly_trends.append({
            "month": month_abbr[int(mo)],
            "trips": cnt,
        })

    # If no trip data yet, provide the current month with 0
    if not monthly_trends:
        now = datetime.now(timezone.utc)
        monthly_trends = [{"month": month_abbr[now.month], "trips": 0}]

    # ── 4. Recent trips (last 5) ─────────────────────────────────────────
    recent_trips_raw = (
        db.query(Trip, User.display_name, HeritageSite.name)
        .outerjoin(User, Trip.user_id == User.id)
        .outerjoin(HeritageSite, Trip.site_id == HeritageSite.id)
        .order_by(Trip.started_at.desc())
        .limit(5)
        .all()
    )
    recent_trips = []
    for trip, user_name, site_name in recent_trips_raw:
        recent_trips.append({
            "id": trip.id,
            "status": "active" if trip.is_active else "completed",
            "start_time": trip.started_at.strftime("%Y-%m-%d %H:%M:%S") if trip.started_at else None,
            "user_name": user_name or "Visitor",
            "site_name": site_name or "Heritage Monument",
        })

    # ── 5. Recent reviews (last 5) ───────────────────────────────────────
    recent_reviews_raw = (
        db.query(TripReview, User.display_name, HeritageSite.name)
        .outerjoin(User, TripReview.user_id == User.id)
        .outerjoin(HeritageSite, TripReview.site_id == HeritageSite.id)
        .order_by(TripReview.submitted_at.desc())
        .limit(5)
        .all()
    )
    recent_reviews = []
    for review, user_name, site_name in recent_reviews_raw:
        recent_reviews.append({
            "id": review.id,
            "rating": review.q1_overall_experience,
            "comment": review.suggestion_text or "",
            "created_at": review.submitted_at.strftime("%Y-%m-%d %H:%M:%S") if review.submitted_at else None,
            "user_name": user_name or "Visitor",
            "site_name": site_name or "Heritage Site",
        })

    # ── 6. Top sites (up to 4) ───────────────────────────────────────────
    # Subqueries for per-site aggregates
    node_counts = (
        db.query(Node.site_id, func.count(Node.id).label("node_count"))
        .group_by(Node.site_id)
        .subquery()
    )
    trip_counts = (
        db.query(Trip.site_id, func.count(Trip.id).label("trip_count"))
        .group_by(Trip.site_id)
        .subquery()
    )
    avg_ratings = (
        db.query(
            SiteRating.site_id,
            func.avg(SiteRating.rating).label("avg_rating"),
        )
        .group_by(SiteRating.site_id)
        .subquery()
    )
    first_images = (
        db.query(
            SiteImage.site_id,
            func.min(SiteImage.image_url).label("image_url"),
        )
        .group_by(SiteImage.site_id)
        .subquery()
    )

    top_sites_raw = (
        db.query(
            HeritageSite.id,
            HeritageSite.name,
            HeritageSite.location,
            first_images.c.image_url,
            func.coalesce(node_counts.c.node_count, 0).label("node_count"),
            func.coalesce(trip_counts.c.trip_count, 0).label("trip_count"),
            func.coalesce(avg_ratings.c.avg_rating, HeritageSite.rating).label("avg_rating"),
        )
        .outerjoin(node_counts, HeritageSite.id == node_counts.c.site_id)
        .outerjoin(trip_counts, HeritageSite.id == trip_counts.c.site_id)
        .outerjoin(avg_ratings, HeritageSite.id == avg_ratings.c.site_id)
        .outerjoin(first_images, HeritageSite.id == first_images.c.site_id)
        .order_by(func.coalesce(trip_counts.c.trip_count, 0).desc())
        .limit(4)
        .all()
    )

    top_sites = []
    for site_id, name, location, image_url, n_count, t_count, a_rating in top_sites_raw:
        top_sites.append({
            "id": site_id,
            "name": name,
            "location": location or "",
            "image_url": image_url,
            "node_count": n_count,
            "trip_count": t_count,
            "avg_rating": round(float(a_rating), 1) if a_rating else 0.0,
        })

    # ── Assemble response ────────────────────────────────────────────────
    return {
        "success": True,
        "stats": {
            "total_registered_users": total_registered_users,
            "total_trips": total_trips,
            "active_trips": active_trips,
            "completed_trips": completed_trips,
            "total_sites": total_sites,
            "total_nodes": total_nodes,
            "total_reviews": total_reviews,
            "total_visits_history": total_visits_history or lifetime_visits,
            "average_site_rating": average_site_rating,
        },
        "rating_distribution": rating_distribution,
        "monthly_trends": monthly_trends,
        "recent_trips": recent_trips,
        "recent_reviews": recent_reviews,
        "top_sites": top_sites,
    }


