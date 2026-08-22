# app/routers/dashboard.py
# Admin dashboard stats — delegates to centralized admin module.

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.routers.admin import get_admin_dashboard, get_current_admin

router = APIRouter(prefix="/admin", tags=["Admin Dashboard"])

# Kept for backward compatibility if any service specifically imports or mounts this router
@router.get("/dashboard/overview")
def get_dashboard_overview(
    admin: dict = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    return get_admin_dashboard(admin=admin, db=db)



