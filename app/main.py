# app/main.py
# UPGRADED:
#   - Added /users and /community routers
#   - Removed install_review_triggers() — triggers replaced by transactional updates
#   - db_triggers.py removed; aggregate columns (rating, avg_rating, rating_count)
#     are now updated in the same Python transaction as the INSERT (see reviews.py)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.database import engine, Base
from app.routers import sites, trips, chat, voice, admin, reviews, amenities
from app.routers import users, community, stats, insights
from app.routers import gems, quiz, store, bonus, instants
from app.routers import dashboard

# Create all tables (new tables: users, node_ratings, node_comments,
# site_feedback, user_chat_history are created automatically here)
Base.metadata.create_all(bind=engine) #creates all tables if it is not initiated before app starts

# ── In-place migrations ──────────────────────────────────────────────────────
# create_all() never ALTERs existing tables, so when we add columns to a model
# we apply them here. Idempotent — safe to run on every boot. Move to Alembic
# once the schema stops moving every release.
def _run_inplace_migrations() -> None:
    statements = [
        # Threaded comments (replies)
        """ALTER TABLE node_comments
           ADD COLUMN IF NOT EXISTS parent_comment_id INTEGER
           REFERENCES node_comments(id) ON DELETE CASCADE""",
        """CREATE INDEX IF NOT EXISTS ix_node_comments_node_root
           ON node_comments (node_id, parent_comment_id, created_at)""",
        # Gamification: gems wallet balance on users
        """ALTER TABLE users
           ADD COLUMN IF NOT EXISTS gems INTEGER NOT NULL DEFAULT 0""",
        # Node instants: photographer name snapshot at upload
        """ALTER TABLE node_instants
           ADD COLUMN IF NOT EXISTS photographer_name VARCHAR(255)""",
        # Heritage sites: location display string
        """ALTER TABLE heritage_sites
           ADD COLUMN IF NOT EXISTS location VARCHAR(255)""",
    ]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


_run_inplace_migrations()

app = FastAPI(
    title="Dharohar Setu Backend",
    description="Heritage Travel Platform - SHREE AI Guide · IIIT Sonepat",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Core
app.include_router(users.router)
app.include_router(sites.router)
app.include_router(trips.router)

# AI
app.include_router(chat.router)
app.include_router(voice.router)

# Reviews & ratings
app.include_router(reviews.router)

# Community (comments + feedback)
app.include_router(community.router)

# Discovery
app.include_router(amenities.router)

# Live stats & insights (visitor counts + per-site / per-node analytics + ML)
app.include_router(stats.router)
app.include_router(insights.router)

# Gamification (gems wallet + final quiz + coupon store + bonus games)
app.include_router(gems.router)
app.include_router(quiz.router)
app.include_router(store.router)
app.include_router(bonus.router)
app.include_router(instants.router)

# Admin / seeding
app.include_router(admin.router)
app.include_router(dashboard.router)


@app.get("/")
def root():
    return {
        "service": "Dharohar Setu Backend",
        "version": "2.0.0",
        "assistant": "SHREE",
        "status": "running",
    }


@app.get("/health")
@app.head("/health")
def health():
    return {"status": "ok"}