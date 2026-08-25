from fastapi import APIRouter, Depends

from models import DashboardSummary
from security import get_current_user

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
def get_dashboard_summary(user: dict = Depends(get_current_user)):
    return DashboardSummary(
        full_name=user["full_name"],
        subscribers=user["subscribers"],
        monthly_views=user["monthly_views"],
    )
