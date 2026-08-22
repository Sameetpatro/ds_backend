# tests/test_admin_portal.py

import os
import unittest
import uuid
import json
import base64
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app.database import Base, get_db
from app.main import app
from app.models import (
    User, HeritageSite, SiteImage, Node, NodeImage, Trip,
    NodeCheckIn, SiteRating, NodeRating, TripReview, UserVisitHistory,
)
from app.routers.trips import sync_abandoned_trips

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
Base.metadata.create_all(bind=test_engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

def create_mock_jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()).decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = "mock_signature"
    return f"{header}.{body}.{sig}"

class TestAdminPortal(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)
        self.db = TestingSessionLocal()

        self.admin_user = User(
            id=uuid.uuid4(),
            firebase_uid="admin_firebase_001",
            display_name="Admin Chief",
            email="admin@dharohar.gov.in",
            role="ADMIN",
        )
        self.db.add(self.admin_user)

        self.superadmin_user = User(
            id=uuid.uuid4(),
            firebase_uid="superadmin_firebase_001",
            display_name="Super Administrator",
            email="superadmin@dharohar.gov.in",
            role="SUPERADMIN",
        )
        self.db.add(self.superadmin_user)

        self.tourist_user = User(
            id=uuid.uuid4(),
            firebase_uid="tourist_firebase_001",
            display_name="Rahul Sharma",
            email="rahul@example.com",
            phone="+919876543210",
            role="TOURIST",
        )
        self.db.add(self.tourist_user)

        self.site = HeritageSite(
            id=1,
            name="Red Fort",
            location="Delhi, India",
            latitude=28.6562,
            longitude=77.2410,
            geofence_radius_meters=150,
            rating=4.8,
        )
        self.db.add(self.site)
        self.db.flush()

        self.node1 = Node(
            id=1,
            site_id=self.site.id,
            name="Lahori Gate",
            latitude=28.6560,
            longitude=77.2408,
            sequence_order=1,
            is_king=True,
            qr_code_value="RED_FORT_GATE_01",
            avg_rating=4.9,
            rating_count=10,
        )
        self.node2 = Node(
            id=2,
            site_id=self.site.id,
            name="Diwan-i-Aam",
            latitude=28.6565,
            longitude=77.2415,
            sequence_order=2,
            is_king=False,
            qr_code_value="RED_FORT_NODE_02",
            avg_rating=4.7,
            rating_count=8,
        )
        self.db.add(self.node1)
        self.db.add(self.node2)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    # ── 1. Admin Session / Token Authentication Tests ─────────────────────────

    def test_admin_auth_via_jwt_session_token(self):
        # 1. Bearer Token containing Firebase / JWT claims with admin email
        admin_jwt = create_mock_jwt({"email": "admin@dharohar.gov.in", "user_id": "admin_firebase_001"})
        res = client.get("/admin/dashboard", headers={"Authorization": f"Bearer {admin_jwt}"})
        self.assertEqual(res.status_code, 200)

        # 2. Bearer Token with Superadmin
        superadmin_jwt = create_mock_jwt({"email": "superadmin@dharohar.gov.in", "sub": "superadmin_firebase_001"})
        res_super = client.get("/admin/dashboard", headers={"Authorization": f"Bearer {superadmin_jwt}"})
        self.assertEqual(res_super.status_code, 200)

        # 3. Bearer Token for non-admin tourist (must return 403)
        tourist_jwt = create_mock_jwt({"email": "rahul@example.com", "user_id": "tourist_firebase_001"})
        res_tourist = client.get("/admin/dashboard", headers={"Authorization": f"Bearer {tourist_jwt}"})
        self.assertEqual(res_tourist.status_code, 403)
        self.assertIn("Access denied", res_tourist.json()["detail"])

        # 4. Direct Bearer token with Firebase UID or Email
        res_direct_uid = client.get("/admin/dashboard", headers={"Authorization": "Bearer admin_firebase_001"})
        self.assertEqual(res_direct_uid.status_code, 200)

        res_direct_email = client.get("/admin/dashboard", headers={"Authorization": "Bearer admin@dharohar.gov.in"})
        self.assertEqual(res_direct_email.status_code, 200)

        # 5. Missing / Empty credentials (must return 401)
        res_anon = client.get("/admin/dashboard")
        self.assertEqual(res_anon.status_code, 401)

        # 6. GET /admin/me verification endpoint
        res_me = client.get("/admin/me", headers={"Authorization": f"Bearer {admin_jwt}"})
        self.assertEqual(res_me.status_code, 200)
        me_data = res_me.json()
        self.assertTrue(me_data["authenticated"])
        self.assertEqual(me_data["email"], "admin@dharohar.gov.in")
        self.assertEqual(me_data["role"], "ADMIN")

    # ── 2. Trip Lifecycle & 10-Minute Abandonment Tests ────────────────────────

    def test_trip_start_and_end_lifecycle(self):
        res = client.post("/trips/start", params={
            "firebase_uid": "tourist_firebase_001",
            "qr_value": "RED_FORT_GATE_01",
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        trip_id = data["trip_id"]
        self.assertEqual(data["status"], "ACTIVE")

        trip = self.db.query(Trip).filter(Trip.id == trip_id).first()
        self.assertEqual(trip.status, "ACTIVE")
        self.assertTrue(trip.is_active)
        self.assertEqual(trip.starting_node_id, 1)

        checkins = self.db.query(NodeCheckIn).filter(NodeCheckIn.trip_id == trip_id).all()
        self.assertEqual(len(checkins), 1)
        self.assertEqual(checkins[0].scan_type, "trip_start")
        self.assertEqual(checkins[0].node_id, 1)

        res_ci = client.post("/trips/checkin", params={
            "trip_id": trip_id,
            "qr_value": "RED_FORT_NODE_02",
        })
        self.assertEqual(res_ci.status_code, 200)

        checkins = self.db.query(NodeCheckIn).filter(NodeCheckIn.trip_id == trip_id).all()
        self.assertEqual(len(checkins), 2)
        self.assertEqual(checkins[1].scan_type, "checkin")
        self.assertEqual(checkins[1].node_id, 2)

        res_end = client.post("/trips/end", params={"trip_id": trip_id, "visited_nodes": "1,2"})
        self.assertEqual(res_end.status_code, 200)
        end_data = res_end.json()
        self.assertEqual(end_data["status"], "COMPLETED")

        self.db.expire_all()
        trip = self.db.query(Trip).filter(Trip.id == trip_id).first()
        self.assertEqual(trip.status, "COMPLETED")
        self.assertFalse(trip.is_active)
        self.assertIsNotNone(trip.ended_at)

    def test_trip_auto_abandonment_after_10_minutes(self):
        res = client.post("/trips/start", params={
            "firebase_uid": "tourist_firebase_001",
            "qr_value": "RED_FORT_GATE_01",
        })
        trip_id = res.json()["trip_id"]

        past_time = datetime.now(timezone.utc) - timedelta(minutes=15)
        self.db.query(Trip).filter(Trip.id == trip_id).update({
            Trip.started_at: past_time,
            Trip.last_activity_at: past_time,
        })
        self.db.commit()

        count_abandoned = sync_abandoned_trips(self.db)
        self.assertEqual(count_abandoned, 1)

        self.db.expire_all()
        trip = self.db.query(Trip).filter(Trip.id == trip_id).first()
        self.assertEqual(trip.status, "ABANDONED")
        self.assertFalse(trip.is_active)
        self.assertIsNotNone(trip.ended_at)

    # ── 3. Dashboard Metrics & Analytics Tests ─────────────────────────────────

    def test_admin_dashboard_metrics(self):
        trip = Trip(
            user_id=self.tourist_user.id,
            site_id=self.site.id,
            starting_node_id=1,
            status="COMPLETED",
            is_active=False,
            started_at=datetime.now(timezone.utc) - timedelta(hours=1),
            ended_at=datetime.now(timezone.utc),
        )
        self.db.add(trip)
        self.db.flush()

        rating = SiteRating(
            site_id=self.site.id,
            user_id=self.tourist_user.id,
            rating=5,
        )
        self.db.add(rating)

        review = TripReview(
            trip_id=trip.id,
            site_id=self.site.id,
            user_id=self.tourist_user.id,
            q1_overall_experience=5,
            q2_guide_helpfulness=5,
            q3_recommend_to_others=5,
            suggestion_text="Amazing experience!",
        )
        self.db.add(review)
        self.db.commit()

        admin_auth_headers = {"Authorization": "Bearer admin_firebase_001"}
        res = client.get("/admin/dashboard", headers=admin_auth_headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()

        metrics = data["metrics"]
        self.assertEqual(metrics["total_registered_users"], 3)
        self.assertEqual(metrics["total_trips"], 1)
        self.assertEqual(metrics["completed_trips"], 1)
        self.assertEqual(metrics["active_trips"], 0)
        self.assertEqual(metrics["total_mapped_sites"], 1)
        self.assertEqual(metrics["total_nodes"], 2)
        self.assertEqual(metrics["total_visitor_reviews"], 1)
        self.assertEqual(metrics["overall_average_site_rating"], 5.0)

    def test_monthly_trip_analytics(self):
        t1 = Trip(
            user_id=self.tourist_user.id,
            site_id=self.site.id,
            started_at=datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
            status="COMPLETED",
            is_active=False,
        )
        t2 = Trip(
            user_id=self.tourist_user.id,
            site_id=self.site.id,
            started_at=datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc),
            status="COMPLETED",
            is_active=False,
        )
        self.db.add(t1)
        self.db.add(t2)
        self.db.commit()

        admin_auth_headers = {"Authorization": "Bearer admin_firebase_001"}
        res = client.get("/admin/analytics/trips", headers=admin_auth_headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertIn("tripStarts", data)
        self.assertIn("qrScans", data)
        months = [item["month"] for item in data["tripStarts"]]
        self.assertIn("2026-01", months)
        self.assertIn("2026-02", months)

    def test_rating_analytics_and_top_sites(self):
        site2 = HeritageSite(id=2, name="Qutub Minar", latitude=28.5244, longitude=77.1855, rating=4.5)
        self.db.add(site2)
        self.db.flush()

        for i in range(5):
            u = User(firebase_uid=f"u_{i}", display_name=f"User {i}")
            self.db.add(u)
            self.db.flush()
            self.db.add(SiteRating(site_id=self.site.id, user_id=u.id, rating=5))

        u_single = User(firebase_uid="u_single", display_name="Single Reviewer")
        self.db.add(u_single)
        self.db.flush()
        self.db.add(SiteRating(site_id=site2.id, user_id=u_single.id, rating=5))
        self.db.commit()

        admin_auth_headers = {"Authorization": "Bearer admin_firebase_001"}
        res_r = client.get("/admin/analytics/ratings", headers=admin_auth_headers)
        self.assertEqual(res_r.status_code, 200)
        data_r = res_r.json()
        self.assertEqual(data_r["rating_distribution"]["five_star"], 6)
        self.assertEqual(data_r["total_reviews"], 6)
        self.assertEqual(data_r["overall_average_rating"], 5.0)

        res_top = client.get("/admin/analytics/top-sites?min_reviews=3", headers=admin_auth_headers)
        self.assertEqual(res_top.status_code, 200)
        data_top = res_top.json()
        self.assertTrue(len(data_top) >= 2)
        self.assertEqual(data_top[0]["site_id"], self.site.id)

    def test_trip_detail_and_recent_activity(self):
        res = client.post("/trips/start", params={
            "firebase_uid": "tourist_firebase_001",
            "qr_value": "RED_FORT_GATE_01",
        })
        trip_id = res.json()["trip_id"]

        admin_auth_headers = {"Authorization": "Bearer admin_firebase_001"}
        res_detail = client.get(f"/admin/trips/{trip_id}", headers=admin_auth_headers)
        self.assertEqual(res_detail.status_code, 200)
        detail = res_detail.json()

        self.assertEqual(detail["trip_id"], trip_id)
        self.assertEqual(detail["tourist"]["display_name"], "Rahul Sharma")
        self.assertEqual(detail["heritage_site"]["site_name"], "Red Fort")
        self.assertEqual(detail["starting_node"]["node_name"], "Lahori Gate")
        self.assertEqual(len(detail["node_checkins"]), 1)
        self.assertEqual(detail["node_checkins"][0]["scan_type"], "trip_start")

        res_act = client.get("/admin/activity/recent", headers=admin_auth_headers)
        self.assertEqual(res_act.status_code, 200)
        activities = res_act.json()
        self.assertTrue(len(activities) >= 1)
        self.assertIn("Rahul Sharma", activities[0]["headline"])

    def test_users_and_admins_lists(self):
        admin_auth_headers = {"Authorization": "Bearer admin_firebase_001"}
        res_u = client.get("/admin/users", headers=admin_auth_headers)
        self.assertEqual(res_u.status_code, 200)
        users = res_u.json()
        self.assertTrue(len(users) >= 3)
        tourist = next(u for u in users if u["user_id"] == str(self.tourist_user.id))
        self.assertEqual(tourist["username"], "Rahul Sharma")
        self.assertEqual(tourist["role"], "TOURIST")

        res_a = client.get("/admin/admins", headers=admin_auth_headers)
        self.assertEqual(res_a.status_code, 200)
        admins = res_a.json()
        self.assertEqual(len(admins), 2)
        admin_emails = [a["email"] for a in admins]
        self.assertIn("admin@dharohar.gov.in", admin_emails)
        self.assertIn("superadmin@dharohar.gov.in", admin_emails)
        self.assertNotIn("password", admins[0])
        self.assertNotIn("password_hash", admins[0])

        # Promote tourist to admin
        res_promo = client.post(
            f"/admin/users/{self.tourist_user.id}/role",
            json={"role": "ADMIN"},
            headers=admin_auth_headers,
        )
        self.assertEqual(res_promo.status_code, 200)
        self.assertEqual(res_promo.json()["role"], "ADMIN")

        res_a2 = client.get("/admin/admins", headers=admin_auth_headers)
        self.assertEqual(len(res_a2.json()), 3)

if __name__ == "__main__":
    unittest.main()
