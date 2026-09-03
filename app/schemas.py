# app/schemas.py
# UPGRADED: Added User, NodeRating, NodeComment, SiteFeedback, UserChatHistory schemas.
#   image_type/dimensions added to SiteImage and NodeImage responses.
#   user_id fields now return UUID strings.

from pydantic import BaseModel
from typing import List, Optional
from uuid import UUID
from datetime import datetime


# ── Auth / Users ──────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    firebase_uid:   str
    display_name:   Optional[str] = None
    email:          Optional[str] = None
    phone:          Optional[str] = None
    avatar_url:     Optional[str] = None
    preferred_lang: str = "en-IN"
    is_anonymous:   bool = False


class UserResponse(BaseModel):
    id:             UUID
    firebase_uid:   str
    display_name:   Optional[str] = None
    email:          Optional[str] = None
    phone:          Optional[str] = None
    avatar_url:     Optional[str] = None
    preferred_lang: str
    is_anonymous:   bool
    created_at:     datetime
    last_active_at: datetime

    class Config:
        from_attributes = True


class PhoneUpdate(BaseModel):
    phone: str


# ── Live stats / visitor counts ─────────────────────────────────────────────────

class LiveStatsResponse(BaseModel):
    active_users:    int      # users active within the last few minutes
    lifetime_visits: int      # cumulative app opens (all time)
    total_users:     int      # distinct registered users


# ── Insights & lightweight ML ───────────────────────────────────────────────────

class DailyVisit(BaseModel):
    date:  str               # "MM-DD"
    count: int


class NodePopularity(BaseModel):
    node_id:          int
    name:             str
    visits:           int
    avg_rating:       float = 0.0
    rating_count:     int   = 0
    engagement_score: float = 0.0


class SiteMlInsight(BaseModel):
    model:                       str   = "linear_regression"
    trained_on:                  int   = 0      # number of samples the model saw
    predicted_visits_next_day:   int   = 0
    visits_trend:                str   = "steady"   # rising | falling | steady
    mins_per_extra_node:         float = 0.0
    predicted_full_duration_mins: float = 0.0
    engagement_score:            float = 0.0    # 0–100
    insight_text:                str   = ""


class SiteInsightsResponse(BaseModel):
    site_id:             int
    site_name:           str
    total_visits:        int
    unique_visitors:     int
    avg_duration_mins:   float
    avg_nodes_completed: float
    completion_rate:     float           # 0–100 (%)
    total_interactions:  int             # chat/voice messages logged for the site
    avg_rating:          float
    daily_visits:        List[DailyVisit]    = []
    node_popularity:     List[NodePopularity] = []
    ml:                  SiteMlInsight


class NodeMlInsight(BaseModel):
    engagement_score: float = 0.0     # 0–100
    insight_text:     str   = ""


class NodeInsightsResponse(BaseModel):
    node_id:        int
    site_id:        int
    name:           str
    visits:         int
    avg_rating:     float = 0.0
    rating_count:   int   = 0
    comments:       int   = 0
    interactions:   int   = 0
    popularity_pct: float = 0.0       # share of site visits that reached this node
    ml:             NodeMlInsight


# ── Stored insight snapshots (training data) ─────────────────────────────────────

class SiteInsightSnapshotResponse(BaseModel):
    snapshot_date:                str            # "YYYY-MM-DD"
    total_visits:                 int   = 0
    unique_visitors:              int   = 0
    avg_duration_mins:            float = 0.0
    avg_nodes_completed:          float = 0.0
    completion_rate:              float = 0.0
    total_interactions:           int   = 0
    avg_rating:                   float = 0.0
    engagement_score:             float = 0.0
    predicted_visits_next_day:    int   = 0
    visits_trend:                 str   = "steady"
    mins_per_extra_node:          float = 0.0
    predicted_full_duration_mins: float = 0.0


# ── Personal (per-user) insights ─────────────────────────────────────────────────

class UserInsightsResponse(BaseModel):
    user_id:                      str
    total_visits:                 int   = 0
    sites_explored:               int   = 0
    total_duration_mins:          int   = 0
    avg_duration_mins:            float = 0.0
    total_nodes_completed:        int   = 0
    avg_completion_rate:          float = 0.0
    total_interactions:           int   = 0
    favorite_site_id:             Optional[int] = None
    favorite_site_name:           Optional[str] = None
    engagement_score:             float = 0.0
    explorer_level:               str   = "Newcomer"
    predicted_next_duration_mins: float = 0.0
    insight_text:                 str   = ""


# ── Chat ──────────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role:    str
    content: str


class ChatRequest(BaseModel):
    site_id: int
    node_id: Optional[int] = None
    message: str
    history: List[ChatMessage] = []
    # Optional persistence fields. When firebase_uid is provided the user's
    # message and the assistant reply are written to user_chat_history.
    # Calls without firebase_uid still work (no DB write) — keeps backwards
    # compatibility with older Android builds.
    firebase_uid: Optional[str] = None
    trip_id:      Optional[int] = None
    lang_code:    Optional[str] = None


class ChatResponse(BaseModel):
    reply: str


# ── Voice ─────────────────────────────────────────────────────────────────────

class VoiceChatResponse(BaseModel):
    user_text:    str
    bot_text:     str
    audio_base64: str
    audio_format: str


# ── Images ────────────────────────────────────────────────────────────────────

class SiteImageResponse(BaseModel):
    id:            int
    image_url:     str
    display_order: int
    image_type:    str = "gallery"
    width:         Optional[int] = None
    height:        Optional[int] = None

    class Config:
        from_attributes = True


class NodeImageResponse(BaseModel):
    id:            int
    image_url:     str
    display_order: int
    image_type:    str = "gallery"
    width:         Optional[int] = None
    height:        Optional[int] = None

    class Config:
        from_attributes = True


# ── Sites ─────────────────────────────────────────────────────────────────────

class NearbySiteResponse(BaseModel):
    id:              int
    name:            str
    location:        Optional[str] = None
    latitude:        float
    longitude:       float
    distance_meters: float
    inside_geofence: bool


class NodeResponse(BaseModel):
    id:             int
    name:           str
    latitude:       float
    longitude:      float
    sequence_order: int
    is_king:        bool           = False
    description:    Optional[str]  = None
    video_url:      Optional[str]  = None
    image_url:      Optional[str]  = None
    images:         List[NodeImageResponse] = []
    qr_code_value:        str
    legacy_qr_code_value: Optional[str] = None
    avg_rating:     float          = 0.0
    rating_count:   int            = 0

    class Config:
        from_attributes = True


class NodePositionResponse(BaseModel):
    id:             int
    name:           str
    latitude:       float
    longitude:      float
    sequence_order: int
    is_king:        bool = False

    class Config:
        from_attributes = True


class SiteDetailResponse(BaseModel):
    id:                     int
    name:                   str
    location:               Optional[str] = None
    latitude:               float
    longitude:              float
    geofence_radius_meters: int
    summary:                Optional[str] = None
    history:                Optional[str] = None
    fun_facts:              Optional[str] = None
    helpline_number:        Optional[str] = None
    static_map_url:         Optional[str] = None
    intro_video_url:        Optional[str] = None
    rating:                 float
    upvotes:                int
    images:                 List[SiteImageResponse] = []
    nodes:                  List[NodeResponse]      = []

    class Config:
        from_attributes = True


class SiteListItemResponse(BaseModel):
    id:                     int
    name:                   str
    location:               Optional[str] = None
    rating:                 float         = 0.0
    nodes_count:            int           = 0
    node_count:             int           = 0
    nodes:                  int           = 0
    image:                  Optional[str] = None
    summary:                Optional[str] = None
    latitude:               Optional[float] = None
    longitude:              Optional[float] = None
    king_node_qr:           Optional[str] = None

    class Config:
        from_attributes = True


# ── Trips ─────────────────────────────────────────────────────────────────────

class StartTripRequest(BaseModel):
    user_id:  str           # firebase_uid — resolved to UUID in router
    qr_value: str


class StartTripResponse(BaseModel):
    message: str
    trip_id: int


class EndTripRequest(BaseModel):
    trip_id: int


class EndTripResponse(BaseModel):
    message: str


# ── Ratings ───────────────────────────────────────────────────────────────────

class SiteRatingRequest(BaseModel):
    site_id:     int
    firebase_uid: str        # resolved to user UUID in router
    rating:      int         # 1–5


class SiteRatingResponse(BaseModel):
    message:    str
    new_avg:    float
    total:      int


class NodeRatingRequest(BaseModel):
    node_id:      int
    site_id:      int
    firebase_uid: str        # resolved to user UUID in router
    rating:       int        # 1–5


class NodeRatingResponse(BaseModel):
    message:   str
    new_avg:   float
    total:     int


# ── Reviews ───────────────────────────────────────────────────────────────────

class ReviewSubmitBody(BaseModel):
    trip_id:        int
    site_id:        int
    firebase_uid:   str      # resolved to user UUID in router
    star_rating:    int      # 1–5
    q1:             int
    q2:             int
    q3:             int
    suggestion_text: Optional[str] = None


class ReviewSubmitResponse(BaseModel):
    message:    str
    review_id:  int
    new_rating: float


class ReviewSummaryResponse(BaseModel):
    avg_star_rating:        float
    total_ratings:          int
    avg_overall_experience: float
    avg_guide_helpfulness:  float
    avg_recommend_score:    float
    total_reviews:          int
    recommend_pct:          float
    satisfaction_label:     str


# ── Community ─────────────────────────────────────────────────────────────────

class NodeCommentCreate(BaseModel):
    firebase_uid:      str
    site_id:           int
    node_id:           int
    content:           str
    parent_comment_id: Optional[int] = None   # set for replies; None = root post


class NodeCommentResponse(BaseModel):
    id:                int
    user_id:           UUID
    site_id:           int
    node_id:           int
    parent_comment_id: Optional[int] = None
    content:           str
    is_flagged:        bool
    created_at:        datetime
    # Display-time enrichments (joined from users table) — keep optional so
    # any caller that POSTs and gets back a fresh row still validates.
    display_name:      Optional[str] = None
    avatar_url:        Optional[str] = None
    reply_count:       int           = 0
    is_own:            bool          = False  # set when firebase_uid is passed

    class Config:
        from_attributes = True


class SiteFeedbackCreate(BaseModel):
    firebase_uid: Optional[str] = None  # None = anonymous
    site_id:      int
    category:     str = "general"
    content:      str


class SiteFeedbackResponse(BaseModel):
    id:         int
    site_id:    int
    category:   str
    content:    str
    status:     str
    created_at: datetime

    class Config:
        from_attributes = True


# ── Discovery ─────────────────────────────────────────────────────────────────

class RecommendationResponse(BaseModel):
    id:          int
    site_id:     int
    type:        str
    name:        str
    description: Optional[str]   = None
    latitude:    Optional[float]  = None
    longitude:   Optional[float]  = None

    class Config:
        from_attributes = True


class AmenityResponse(BaseModel):
    id:              int
    site_id:         int
    node_id:         Optional[int]   = None
    type:            str
    name:            str
    description:     Optional[str]   = None
    latitude:        float
    longitude:       float
    price_info:      Optional[str]   = None
    timing:          Optional[str]   = None
    is_paid:         bool            = False
    distance_meters: Optional[float] = None

    class Config:
        from_attributes = True


# ── Gamification: gems wallet ────────────────────────────────────────────────────

class GemTransactionItem(BaseModel):
    delta:         int
    reason:        str
    balance_after: int
    created_at:    datetime

    class Config:
        from_attributes = True


class GemBalanceResponse(BaseModel):
    gems:    int
    history: List[GemTransactionItem] = []


# ── Gamification: final quiz ─────────────────────────────────────────────────────

class QuizQuestionPublic(BaseModel):
    question_id: int
    idx:         int
    question:    str
    options:     List[str]
    answered:    bool = False


class QuizStartResponse(BaseModel):
    session_id:        int
    status:            str               # preparing | active | completed | abandoned
    seconds_per_question: int = 10
    total_questions:   int
    gems_earned:       int = 0
    questions:         List[QuizQuestionPublic] = []
    already_played:    bool = False      # true if a session already existed for the trip


class QuizPrepareRequest(BaseModel):
    node_ids: List[int]


class QuizPrepareResponse(BaseModel):
    session_id:      int
    status:          str
    total_questions: int
    nodes_prepared:  int


class QuizAnswerRequest(BaseModel):
    session_id:     int
    question_id:    int
    selected_index: int
    seconds_taken:  float


class QuizAnswerResponse(BaseModel):
    correct:        bool
    correct_index:  int
    gems_awarded:   int
    running_total:  int


class QuizCompleteRequest(BaseModel):
    session_id: int


class QuizCompleteResponse(BaseModel):
    status:        str
    gems_earned:   int
    new_balance:   int


class QuizAbandonRequest(BaseModel):
    session_id: int


# ── Gamification: coupon store ───────────────────────────────────────────────────

class StorePartner(BaseModel):
    id:              int
    name:            str
    type:            str
    description:     Optional[str]   = None
    latitude:        Optional[float] = None
    longitude:       Optional[float] = None
    distance_meters: Optional[float] = None


class CouponPurchaseRequest(BaseModel):
    firebase_uid: str
    tier:         str               # ultimate | special | normal
    partner_kind: str               # hotel | restaurant
    site_id:      Optional[int] = None
    user_lat:     Optional[float] = None
    user_lng:     Optional[float] = None


class CouponResponse(BaseModel):
    id:              int
    partner_name:    str
    partner_type:    str
    partner_lat:     Optional[float] = None
    partner_lng:     Optional[float] = None
    tier:            str
    discount_pct:    int
    gems_spent:      int
    code:            str
    status:          str
    distance_meters: Optional[float] = None
    created_at:      datetime
    expires_at:      datetime

    class Config:
        from_attributes = True


class CouponPurchaseResponse(BaseModel):
    coupon:      CouponResponse
    new_balance: int


# ── Gamification: bonus "Bingo" challenge ────────────────────────────────────────

class BonusOfferRequest(BaseModel):
    firebase_uid: str
    site_id:      int
    exclude_node_id: Optional[int] = None   # last scanned node (still eligible, but prefer variety)


class BonusOfferResponse(BaseModel):
    challenge_id:    int
    target_node_id:  int
    target_node_name: str
    site_name:       str
    minigame:        str               # zip | sudoku
    reward_gems:     int               # pre-rolled 100-200
    deadline_minutes: int
    expires_at:      datetime


class BonusCompleteRequest(BaseModel):
    firebase_uid: str
    challenge_id: int
    scanned_node_id: int
    solved:       bool


class BonusCompleteResponse(BaseModel):
    status:      str               # completed | expired | wrong_node | not_solved
    reward_gems: int = 0
    new_balance: int = 0


class BonusSiteStatus(BaseModel):
    site_id: int
    site_name: str
    played: bool
    reward_gems: int = 0
    status: Optional[str] = None      # completed | offered | reached | expired | None
    completed_at: Optional[datetime] = None
    available_after_minutes: int = 0


# ── Node Instants ─────────────────────────────────────────────────────────────

class NodeInstantCreate(BaseModel):
    firebase_uid: str
    site_id:      int
    node_id:      int
    media_url:    str
    media_type:   str = "image"    # image | video
    caption:      Optional[str] = None


class NodeInstantResponse(BaseModel):
    id:           int
    user_id:      UUID
    site_id:      int
    node_id:      int
    media_url:    str
    media_type:   str
    caption:      Optional[str] = None
    like_count:   int = 0
    created_at:   datetime
    display_name: Optional[str] = None
    avatar_url:   Optional[str] = None
    liked_by_me:  bool = False

    class Config:
        from_attributes = True


class NodeInstantLikeResponse(BaseModel):
    instant_id:  int
    liked:       bool
    like_count:  int


# ── Admin Portal Schemas ──────────────────────────────────────────────────────

class AdminDashboardStatsResponse(BaseModel):
    total_registered_users: int
    active_trips: int
    total_trips: int
    completed_trips: int
    abandoned_trips: int
    total_mapped_sites: int
    total_nodes: int
    total_node_checkins: int
    total_visitor_reviews: int
    overall_average_site_rating: float


class MonthlyTripItem(BaseModel):
    month: str  # "YYYY-MM"
    count: int


class MonthlyTripAnalyticsResponse(BaseModel):
    tripStarts: List[MonthlyTripItem] = []
    qrScans:    List[MonthlyTripItem] = []


class SiteRatingDetail(BaseModel):
    site_id:        int
    site_name:      str
    average_rating: float
    review_count:   int
    five_star:      int = 0
    four_star:      int = 0
    three_star:     int = 0
    two_star:       int = 0
    one_star:       int = 0


class RatingDistribution(BaseModel):
    five_star:  int = 0
    four_star:  int = 0
    three_star: int = 0
    two_star:   int = 0
    one_star:   int = 0


class RatingAnalyticsResponse(BaseModel):
    rating_distribution:    RatingDistribution
    total_reviews:          int
    overall_average_rating: float
    per_site_ratings:       List[SiteRatingDetail] = []


class TopSiteRanking(BaseModel):
    rank:            int
    site_id:         int
    site_name:       str
    average_rating:  float
    review_count:    int
    bayesian_rating: float


class NodeCheckInResponse(BaseModel):
    id:         int
    node_id:    int
    node_name:  Optional[str] = None
    site_id:    int
    site_name:  Optional[str] = None
    scan_type:  str = "checkin"
    scanned_at: datetime

    class Config:
        from_attributes = True


class AdminTouristProfile(BaseModel):
    user_id:      str
    display_name: Optional[str] = None
    email:        Optional[str] = None
    phone:        Optional[str] = None
    avatar_url:   Optional[str] = None


class AdminSiteSummary(BaseModel):
    site_id:  int
    site_name: str
    location: Optional[str] = None


class AdminNodeSummary(BaseModel):
    node_id:        int
    node_name:      str
    sequence_order: int = 0
    is_king:        bool = False


class AdminTripDetailResponse(BaseModel):
    trip_id:            int
    tourist:            Optional[AdminTouristProfile] = None
    heritage_site:      AdminSiteSummary
    starting_node:      Optional[AdminNodeSummary] = None
    start_time:         Optional[datetime] = None
    end_time:           Optional[datetime] = None
    status:             str
    trip_duration_mins: Optional[int] = None
    node_checkins:      List[NodeCheckInResponse] = []


class AdminTripListItem(BaseModel):
    trip_id:            int
    user_id:            Optional[str] = None
    user_name:          Optional[str] = None
    user_email:         Optional[str] = None
    site_id:            int
    site_name:          str
    starting_node_id:   Optional[int] = None
    starting_node_name: Optional[str] = None
    start_time:         Optional[datetime] = None
    end_time:           Optional[datetime] = None
    status:             str
    trip_duration_mins: Optional[int] = None
    checkin_count:      int = 0


class RecentActivityItem(BaseModel):
    activity_id:   str
    activity_type: str  # trip_start | node_checkin | trip_end | review
    trip_id:       Optional[int] = None
    user_id:       Optional[str] = None
    user_name:     Optional[str] = None
    site_id:       Optional[int] = None
    site_name:     Optional[str] = None
    node_id:       Optional[int] = None
    node_name:     Optional[str] = None
    status:        Optional[str] = None
    timestamp:     datetime
    headline:      str
    description:   Optional[str] = None


class AdminUserResponse(BaseModel):
    user_id:           str
    username:          Optional[str] = None
    email:             Optional[str] = None
    phone:             Optional[str] = None
    role:              str = "TOURIST"
    trips_taken:       int = 0
    sites_visited:     int = 0
    reviews_submitted: int = 0
    registered_at:     Optional[datetime] = None
    last_active_at:    Optional[datetime] = None


class AdminAccountResponse(BaseModel):
    user_id:        str
    username:       Optional[str] = None
    email:          Optional[str] = None
    role:           str
    registered_at:  Optional[datetime] = None
    last_active_at: Optional[datetime] = None
    platform:       Optional[str] = "web_portal"


class CheckInSiteSummary(BaseModel):
    site_id:         int
    site_name:       str
    total_checkins:  int


class CheckInNodeSummary(BaseModel):
    node_id:     int
    node_name:   str
    site_id:     int
    site_name:   str
    total_scans: int
    is_king:     bool = False


class CheckInAnalyticsResponse(BaseModel):
    total_node_checkins:            int
    trip_start_scans:               int
    subsequent_checkins:            int
    checkins_per_site:              List[CheckInSiteSummary] = []
    checkins_per_node:              List[CheckInNodeSummary] = []
    most_frequently_scanned_nodes:  List[CheckInNodeSummary] = []
    scans_over_time:                List[MonthlyTripItem] = []


class RoleUpdateRequest(BaseModel):
    role: str