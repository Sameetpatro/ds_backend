# app/models.py
# UPGRADED: Full Dharohar Setu schema v2
#   - Added User model with UUID PK + firebase_uid
#   - All user_id columns now reference users.id (UUID FK)
#   - Added NodeRating, NodeComment, SiteFeedback tables
#   - Added image_type, width, height to SiteImage and NodeImage
#   - Added avg_rating, rating_count to Node
#   - Added entry_lat, entry_lng to Trip
#   - Removed dependency on db_triggers.py (ratings updated transactionally in routers)

import uuid
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Boolean,
    ForeignKey,
    Text,
    Date,
    DateTime,
    Index,
    SmallInteger,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


# ── Users ─────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    firebase_uid   = Column(String(128), unique=True, nullable=False)
    display_name   = Column(String(255), nullable=True)
    email          = Column(String(255), nullable=True)
    phone          = Column(String(20), nullable=True)
    avatar_url     = Column(Text, nullable=True)
    preferred_lang = Column(String(10), default="en-IN")
    is_anonymous   = Column(Boolean, default=False)
    gems           = Column(Integer, default=0, nullable=False, server_default=text("0"))
    created_at     = Column(DateTime(timezone=True), server_default=func.now())
    last_active_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── Global / App-wide Stats ─────────────────────────────────────────────────────

class GlobalStats(Base):
    """
    Single-row table (id=1) holding app-wide counters that are expensive to
    derive on every request. `lifetime_visits` is incremented once per app
    cold-start via POST /stats/visit. "Active users right now" is derived from
    users.last_active_at and never stored here.
    """
    __tablename__ = "global_stats"

    id              = Column(Integer, primary_key=True, default=1)
    lifetime_visits = Column(Integer, default=0, nullable=False)
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── Heritage Sites ────────────────────────────────────────────────────────────

class HeritageSite(Base):
    __tablename__ = "heritage_sites"

    id                     = Column(Integer, primary_key=True, index=True)
    name                   = Column(String(255), nullable=False)
    location               = Column(String(255), nullable=True)
    latitude               = Column(Float, nullable=False)
    longitude              = Column(Float, nullable=False)
    geofence_radius_meters = Column(Integer, default=100)
    summary                = Column(Text)
    history                = Column(Text)
    fun_facts              = Column(Text)
    helpline_number        = Column(String(50))
    static_map_url         = Column(Text)
    intro_video_url        = Column(Text)
    rating                 = Column(Float, default=0.0)   # denormalized avg — updated transactionally
    upvotes                = Column(Integer, default=0)
    created_at             = Column(DateTime(timezone=True), server_default=func.now())

    images = relationship("SiteImage", back_populates="site", order_by="SiteImage.display_order",  cascade="all, delete-orphan")
    nodes  = relationship("Node",      back_populates="site", order_by="Node.sequence_order",       cascade="all, delete-orphan")
    trips  = relationship("Trip",      back_populates="site",                                       cascade="all, delete-orphan")


class SiteImage(Base):
    __tablename__ = "site_images"

    id            = Column(Integer, primary_key=True, index=True)
    site_id       = Column(Integer, ForeignKey("heritage_sites.id", ondelete="CASCADE"), nullable=False)
    image_url     = Column(Text, nullable=False)
    display_order = Column(Integer, default=0)
    image_type    = Column(String(20), default="gallery")   # gallery | hero | thumbnail
    width         = Column(Integer, nullable=True)
    height        = Column(Integer, nullable=True)

    site = relationship("HeritageSite", back_populates="images")


# ── Nodes ─────────────────────────────────────────────────────────────────────

class Node(Base):
    __tablename__ = "nodes"
    __table_args__ = (
        # Hard guarantee at the DB layer: exactly ONE king node per site.
        # Application layer (admin/seed-bulk) also enforces this, but a partial
        # unique index protects against direct DB inserts / future endpoints.
        Index(
            "uq_king_node_per_site",
            "site_id",
            unique=True,
            postgresql_where=text("is_king = TRUE"),
        ),
    )

    id             = Column(Integer, primary_key=True, index=True)
    site_id        = Column(Integer, ForeignKey("heritage_sites.id", ondelete="CASCADE"), nullable=False)
    name           = Column(String(255), nullable=False)
    latitude       = Column(Float, nullable=False)
    longitude      = Column(Float, nullable=False)
    sequence_order = Column(Integer, default=0)
    is_king        = Column(Boolean, default=False)
    description    = Column(Text)
    video_url      = Column(String)
    image_url      = Column(String)                         # legacy single image — kept for compat
    qr_code_value  = Column(String(255), unique=True, nullable=False)
    avg_rating     = Column(Float, default=0.0)             # denormalized — updated transactionally
    rating_count   = Column(Integer, default=0)

    site   = relationship("HeritageSite", back_populates="nodes")
    images = relationship("NodeImage", back_populates="node", order_by="NodeImage.display_order", cascade="all, delete-orphan")


class NodeImage(Base):
    __tablename__ = "node_images"

    id            = Column(Integer, primary_key=True, index=True)
    node_id       = Column(Integer, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False)
    image_url     = Column(Text, nullable=False)
    display_order = Column(Integer, default=0)
    image_type    = Column(String(20), default="gallery")   # gallery | hero | thumbnail
    width         = Column(Integer, nullable=True)
    height        = Column(Integer, nullable=True)

    node = relationship("Node", back_populates="images")


# ── Trips ─────────────────────────────────────────────────────────────────────

class Trip(Base):
    __tablename__ = "trips"

    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    site_id    = Column(Integer, ForeignKey("heritage_sites.id", ondelete="CASCADE"), nullable=False)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    ended_at   = Column(DateTime(timezone=True), nullable=True)
    is_active  = Column(Boolean, default=True)
    entry_lat  = Column(Float, nullable=True)
    entry_lng  = Column(Float, nullable=True)

    site = relationship("HeritageSite", back_populates="trips")


# ── Visit History ─────────────────────────────────────────────────────────────

class UserVisitHistory(Base):
    __tablename__ = "user_visit_history"
    __table_args__ = (UniqueConstraint("user_id", "trip_id", name="uq_user_visit_user_trip"),)

    id               = Column(Integer, primary_key=True, index=True)
    user_id          = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    site_id          = Column(Integer, ForeignKey("heritage_sites.id", ondelete="CASCADE"), nullable=False)
    trip_id          = Column(Integer, ForeignKey("trips.id", ondelete="SET NULL"), nullable=True)
    site_name        = Column(String(255), nullable=False)
    nodes_visited    = Column(ARRAY(Integer), server_default=text("'{}'"), default=lambda: [])
    total_nodes      = Column(Integer, default=0)
    nodes_completed  = Column(Integer, default=0)
    completed        = Column(Boolean, default=True)
    visited_at       = Column(DateTime(timezone=True), nullable=False)
    ended_at         = Column(DateTime(timezone=True), nullable=True)
    duration_mins    = Column(Integer, nullable=True)
    entry_lat        = Column(Float, nullable=True)
    entry_lng        = Column(Float, nullable=True)
    review_submitted = Column(Boolean, default=False)


# ── Ratings & Reviews ─────────────────────────────────────────────────────────

class SiteRating(Base):
    __tablename__ = "site_ratings"
    __table_args__ = (UniqueConstraint("site_id", "user_id", name="uq_site_ratings_site_user"),)

    id         = Column(Integer, primary_key=True, index=True)
    site_id    = Column(Integer, ForeignKey("heritage_sites.id", ondelete="CASCADE"), nullable=False)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    rating     = Column(SmallInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class NodeRating(Base):
    """Per-user per-node star rating. New table — not in original schema."""
    __tablename__ = "node_ratings"
    __table_args__ = (UniqueConstraint("node_id", "user_id", name="uq_node_ratings_node_user"),)

    id         = Column(Integer, primary_key=True, index=True)
    node_id    = Column(Integer, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False)
    site_id    = Column(Integer, ForeignKey("heritage_sites.id", ondelete="CASCADE"), nullable=False)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    rating     = Column(SmallInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class TripReview(Base):
    __tablename__ = "trip_reviews"
    __table_args__ = (UniqueConstraint("trip_id", name="uq_trip_reviews_trip"),)

    id                     = Column(Integer, primary_key=True, index=True)
    trip_id                = Column(Integer, ForeignKey("trips.id", ondelete="CASCADE"), nullable=False)
    site_id                = Column(Integer, ForeignKey("heritage_sites.id", ondelete="CASCADE"), nullable=False)
    user_id                = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    q1_overall_experience  = Column(SmallInteger, nullable=False)
    q2_guide_helpfulness   = Column(SmallInteger, nullable=False)
    q3_recommend_to_others = Column(SmallInteger, nullable=False)
    suggestion_text        = Column(Text, nullable=True)
    submitted_at           = Column(DateTime(timezone=True), server_default=func.now())


class AnalyzedResponse(Base):
    """Materialized analytics per site. Refreshed by background worker, never by trigger."""
    __tablename__ = "analyzed_responses"
    __table_args__ = (UniqueConstraint("site_id", name="uq_analyzed_responses_site"),)

    id                     = Column(Integer, primary_key=True, index=True)
    site_id                = Column(Integer, ForeignKey("heritage_sites.id", ondelete="CASCADE"), nullable=False, unique=True)
    avg_star_rating        = Column(Float, default=0.0)
    total_ratings          = Column(Integer, default=0)
    avg_overall_experience = Column(Float, default=0.0)
    avg_guide_helpfulness  = Column(Float, default=0.0)
    avg_recommend_score    = Column(Float, default=0.0)
    total_reviews          = Column(Integer, default=0)
    recommend_pct          = Column(Float, default=0.0)
    satisfaction_label     = Column(String(50), default="No data")
    last_updated           = Column(DateTime(timezone=True), server_default=func.now())


# ── Insight snapshots (training data) ───────────────────────────────────────────

class SiteInsightSnapshot(Base):
    """
    One row per site per day capturing the computed insight metrics + the tiny
    model's outputs. Accumulates a historical time-series you can train richer
    models on later (vs. the live 14-day window). Upserted on (site_id, date).
    """
    __tablename__ = "site_insight_snapshots"
    __table_args__ = (
        UniqueConstraint("site_id", "snapshot_date", name="uq_site_snapshot_site_date"),
        Index("ix_site_snapshot_site_date", "site_id", "snapshot_date"),
    )

    id                           = Column(Integer, primary_key=True, index=True)
    site_id                      = Column(Integer, ForeignKey("heritage_sites.id", ondelete="CASCADE"), nullable=False)
    snapshot_date                = Column(Date, nullable=False)
    total_visits                 = Column(Integer, default=0)
    unique_visitors              = Column(Integer, default=0)
    avg_duration_mins            = Column(Float, default=0.0)
    avg_nodes_completed          = Column(Float, default=0.0)
    completion_rate              = Column(Float, default=0.0)
    total_interactions           = Column(Integer, default=0)
    avg_rating                   = Column(Float, default=0.0)
    engagement_score             = Column(Float, default=0.0)
    predicted_visits_next_day    = Column(Integer, default=0)
    visits_trend                 = Column(String(20), default="steady")
    mins_per_extra_node          = Column(Float, default=0.0)
    predicted_full_duration_mins = Column(Float, default=0.0)
    created_at                   = Column(DateTime(timezone=True), server_default=func.now())


class UserInsight(Base):
    """
    Personal, per-user aggregated insights — the user's own heritage footprint.
    Recomputed and upserted on demand (one row per user).
    """
    __tablename__ = "user_insights"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_insights_user"),)

    id                           = Column(Integer, primary_key=True, index=True)
    user_id                      = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    total_visits                 = Column(Integer, default=0)
    sites_explored               = Column(Integer, default=0)
    total_duration_mins          = Column(Integer, default=0)
    avg_duration_mins            = Column(Float, default=0.0)
    total_nodes_completed        = Column(Integer, default=0)
    avg_completion_rate          = Column(Float, default=0.0)
    total_interactions           = Column(Integer, default=0)
    favorite_site_id             = Column(Integer, nullable=True)
    favorite_site_name           = Column(String(255), nullable=True)
    engagement_score             = Column(Float, default=0.0)
    explorer_level               = Column(String(40), default="Newcomer")
    predicted_next_duration_mins = Column(Float, default=0.0)
    last_computed_at             = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_at                   = Column(DateTime(timezone=True), server_default=func.now())


# ── AI & Content ──────────────────────────────────────────────────────────────

class Prompt(Base):
    __tablename__ = "prompts"

    id      = Column(Integer, primary_key=True, index=True)
    site_id = Column(Integer, ForeignKey("heritage_sites.id"), nullable=True)
    node_id = Column(Integer, ForeignKey("nodes.id"), nullable=True)
    title   = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)

    site = relationship("HeritageSite")
    node = relationship("Node")


class UserChatHistory(Base):
    """Every chat message per user per trip. Fastest growing table — partition by month at 10M rows."""
    __tablename__ = "user_chat_history"

    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    trip_id    = Column(Integer, ForeignKey("trips.id", ondelete="SET NULL"), nullable=True)
    site_id    = Column(Integer, ForeignKey("heritage_sites.id", ondelete="CASCADE"), nullable=False)
    node_id    = Column(Integer, ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True)
    role       = Column(String(20), nullable=False)         # 'user' | 'assistant'
    content    = Column(Text, nullable=False)
    lang_code  = Column(String(10), default="en-IN")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


# ── Community ─────────────────────────────────────────────────────────────────

class NodeComment(Base):
    """
    User comments on individual nodes.

    Supports flat 2-level threading: a comment is either a root post
    (parent_comment_id IS NULL) or a reply to a root post. Replies-to-replies
    are normalized server-side back to the root parent so the UI stays flat.
    """
    __tablename__ = "node_comments"
    __table_args__ = (
        Index("ix_node_comments_node_root",
              "node_id", "parent_comment_id", "created_at"),
    )

    id                = Column(Integer, primary_key=True, index=True)
    user_id           = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    site_id           = Column(Integer, ForeignKey("heritage_sites.id", ondelete="CASCADE"), nullable=False)
    node_id           = Column(Integer, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False)
    parent_comment_id = Column(Integer, ForeignKey("node_comments.id", ondelete="CASCADE"), nullable=True)
    content           = Column(Text, nullable=False)
    is_flagged        = Column(Boolean, default=False)
    created_at        = Column(DateTime(timezone=True), server_default=func.now())
    updated_at        = Column(DateTime(timezone=True), server_default=func.now())


class SiteFeedback(Base):
    """General feedback. Supports anonymous submissions (user_id nullable)."""
    __tablename__ = "site_feedback"

    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    site_id    = Column(Integer, ForeignKey("heritage_sites.id", ondelete="CASCADE"), nullable=False)
    category   = Column(String(50), default="general")      # general | accessibility | content | bug
    content    = Column(Text, nullable=False)
    status     = Column(String(30), default="open")         # open | reviewed | resolved
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ── Discovery ─────────────────────────────────────────────────────────────────

class Recommendation(Base):
    __tablename__ = "recommendations"

    id          = Column(Integer, primary_key=True, index=True)
    site_id     = Column(Integer, ForeignKey("heritage_sites.id", ondelete="CASCADE"), nullable=False)
    type        = Column(String(50), nullable=False)        # monument | hotel | restaurant | activity
    name        = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    latitude    = Column(Float, nullable=True)
    longitude   = Column(Float, nullable=True)

    site = relationship("HeritageSite")


class Amenity(Base):
    __tablename__ = "amenities"

    id          = Column(Integer, primary_key=True, index=True)
    site_id     = Column(Integer, ForeignKey("heritage_sites.id", ondelete="CASCADE"), nullable=False)
    node_id     = Column(Integer, ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True)
    type        = Column(String(50), nullable=False)        # washroom | shop | first_aid | parking
    name        = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    latitude    = Column(Float, nullable=False)
    longitude   = Column(Float, nullable=False)
    price_info  = Column(String(255), nullable=True)
    timing      = Column(String(255), nullable=True)
    is_paid     = Column(Boolean, default=False)

    site = relationship("HeritageSite")
    node = relationship("Node")


# ── Gamification: gems wallet ledger ────────────────────────────────────────────

class GemTransaction(Base):
    """
    Append-only ledger of every gem credit/debit. `users.gems` is the live
    balance; this table is the audit trail (quiz rewards, bonus games, coupon
    purchases). `balance_after` snapshots the wallet right after the row.
    """
    __tablename__ = "gem_transactions"

    id            = Column(Integer, primary_key=True, index=True)
    user_id       = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    delta         = Column(Integer, nullable=False)         # +credit / -debit
    reason        = Column(String(40), nullable=False)      # quiz | bonus_game | coupon_purchase | adjust
    ref_id        = Column(String(64), nullable=True)       # session id / coupon id / challenge id
    balance_after = Column(Integer, nullable=False, default=0)
    created_at    = Column(DateTime(timezone=True), server_default=func.now(), index=True)


# ── Gamification: final quiz ────────────────────────────────────────────────────

class QuizSession(Base):
    """
    One quiz per trip. Created when the user reaches the end-of-trip quiz.
    Anti re-entry is enforced by the unique trip_id: once a session exists for a
    trip, the user can never start it again (so quitting forfeits earned gems).
    """
    __tablename__ = "quiz_sessions"
    __table_args__ = (UniqueConstraint("trip_id", name="uq_quiz_session_trip"),)

    id            = Column(Integer, primary_key=True, index=True)
    user_id       = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    trip_id       = Column(Integer, ForeignKey("trips.id", ondelete="SET NULL"), nullable=True)
    site_id       = Column(Integer, ForeignKey("heritage_sites.id", ondelete="CASCADE"), nullable=False)
    status        = Column(String(20), nullable=False, default="active")   # preparing | active | completed | abandoned
    num_questions = Column(Integer, default=0)
    gems_earned   = Column(Integer, default=0)              # provisional until completed; 0 if abandoned
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    completed_at  = Column(DateTime(timezone=True), nullable=True)

    questions = relationship("QuizQuestion", back_populates="session", cascade="all, delete-orphan")


class QuizQuestion(Base):
    """A single MCQ in a quiz session. `correct_index` never leaves the server."""
    __tablename__ = "quiz_questions"

    id            = Column(Integer, primary_key=True, index=True)
    session_id    = Column(Integer, ForeignKey("quiz_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    idx           = Column(Integer, nullable=False)         # 0-based order within the session
    question      = Column(Text, nullable=False)
    options       = Column(ARRAY(String), nullable=False)
    correct_index = Column(SmallInteger, nullable=False)
    source_node_id = Column(Integer, nullable=True)
    answered      = Column(Boolean, default=False)
    selected_index = Column(SmallInteger, nullable=True)
    seconds_taken = Column(Float, nullable=True)
    is_correct    = Column(Boolean, default=False)
    gems_awarded  = Column(Integer, default=0)

    session = relationship("QuizSession", back_populates="questions")


# ── Gamification: coupon store ──────────────────────────────────────────────────

class UserCoupon(Base):
    """
    A coupon a user bought with gems via the spin-wheel store. Bound to a partner
    (a hotel/restaurant Recommendation). Deadline (`expires_at`) is derived from
    the user's distance to the partner at purchase time.
    """
    __tablename__ = "user_coupons"

    id             = Column(Integer, primary_key=True, index=True)
    user_id        = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    recommendation_id = Column(Integer, ForeignKey("recommendations.id", ondelete="SET NULL"), nullable=True)
    partner_name   = Column(String(255), nullable=False)
    partner_type   = Column(String(50), nullable=False)    # hotel | restaurant
    partner_lat    = Column(Float, nullable=True)
    partner_lng    = Column(Float, nullable=True)
    site_id        = Column(Integer, ForeignKey("heritage_sites.id", ondelete="SET NULL"), nullable=True)
    tier           = Column(String(20), nullable=False)    # ultimate | special | normal
    discount_pct   = Column(Integer, nullable=False)
    gems_spent     = Column(Integer, nullable=False)
    code           = Column(String(20), unique=True, nullable=False)
    status         = Column(String(20), nullable=False, default="active")  # active | redeemed | expired
    distance_meters = Column(Float, nullable=True)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())
    expires_at     = Column(DateTime(timezone=True), nullable=False)


# ── Gamification: bonus "Bingo" challenge ────────────────────────────────────────

class BonusChallenge(Base):
    """
    A surprise location challenge: reach + scan target_node within the deadline
    to unlock a single random minigame. Solving it awards 100-200 bonus gems.
    """
    __tablename__ = "bonus_challenges"

    id             = Column(Integer, primary_key=True, index=True)
    user_id        = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    site_id        = Column(Integer, ForeignKey("heritage_sites.id", ondelete="CASCADE"), nullable=False)
    target_node_id = Column(Integer, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False)
    minigame       = Column(String(30), nullable=False)   # zip | sudoku
    reward_gems    = Column(Integer, nullable=False)       # pre-rolled 100-200
    status         = Column(String(20), nullable=False, default="offered")  # offered | reached | completed | expired
    created_at     = Column(DateTime(timezone=True), server_default=func.now())
    expires_at     = Column(DateTime(timezone=True), nullable=False)
    completed_at   = Column(DateTime(timezone=True), nullable=True)


# ── Community: Node Instants (Instagram-style UGC per node) ─────────────────

class NodeInstant(Base):
    """
    User-submitted photo/video moment at a heritage node.
    Ranked by like_count for the top-50 featured feed per node.
    Auto-deleted after 34 hours or when outside the top 50 for that node.
    media_url is a Firebase Storage (or CDN) URL uploaded by the client.
    """
    __tablename__ = "node_instants"
    __table_args__ = (
        Index("ix_node_instants_node_rank", "node_id", "like_count", "created_at"),
    )

    id          = Column(Integer, primary_key=True, index=True)
    user_id     = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    site_id     = Column(Integer, ForeignKey("heritage_sites.id", ondelete="CASCADE"), nullable=False)
    node_id     = Column(Integer, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    media_url   = Column(Text, nullable=False)
    media_type  = Column(String(20), nullable=False, default="image")   # image | video
    caption     = Column(Text, nullable=True)
    like_count  = Column(Integer, default=0, nullable=False, server_default=text("0"))
    is_flagged  = Column(Boolean, default=False)
    photographer_name = Column(String(255), nullable=True)   # snapshot at upload time
    created_at  = Column(DateTime(timezone=True), server_default=func.now())


class NodeInstantLike(Base):
    """One like per user per instant — toggled via POST /instants/{id}/like."""
    __tablename__ = "node_instant_likes"
    __table_args__ = (
        UniqueConstraint("instant_id", "user_id", name="uq_node_instant_like"),
    )

    id         = Column(Integer, primary_key=True, index=True)
    instant_id = Column(Integer, ForeignKey("node_instants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())