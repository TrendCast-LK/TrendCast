from collections import defaultdict

from fastapi import APIRouter, Depends

from db import get_cursor
from models import CategoryBreakdown, TrendsSummary, TrendsTimelinePoint
from security import get_current_user

router = APIRouter(prefix="/trends", tags=["trends"])

PREDICTIONS_SQL = """
    SELECT title, category, status, predicted_views, confidence
    FROM predictions
    WHERE user_id = %(user_id)s
    ORDER BY created_at ASC
"""


@router.get("/summary", response_model=TrendsSummary)
def get_trends_summary(user: dict = Depends(get_current_user)):
    with get_cursor() as cur:
        cur.execute(PREDICTIONS_SQL, {"user_id": user["id"]})
        columns = [col.name for col in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    total_predictions = len(rows)
    draft_predictions = sum(1 for r in rows if r["status"] == "draft")
    completed = [r for r in rows if r["status"] == "complete"]
    completed_predictions = len(completed)

    average_predicted_views = (
        sum(r["predicted_views"] or 0 for r in completed) / completed_predictions
        if completed_predictions
        else 0.0
    )

    confidences = [r["confidence"] for r in completed if r["confidence"] is not None]
    average_confidence = sum(confidences) / len(confidences) if confidences else None

    by_category: dict[str, list[int]] = defaultdict(list)
    for r in completed:
        if r["category"]:
            by_category[r["category"]].append(r["predicted_views"] or 0)

    category_breakdown = sorted(
        (
            CategoryBreakdown(category=category, count=len(views), average_views=sum(views) / len(views))
            for category, views in by_category.items()
        ),
        key=lambda c: c.average_views,
        reverse=True,
    )
    best_category = category_breakdown[0].category if category_breakdown else None

    timeline = [
        TrendsTimelinePoint(title=r["title"], predicted_views=r["predicted_views"] or 0) for r in completed
    ]

    return TrendsSummary(
        total_predictions=total_predictions,
        draft_predictions=draft_predictions,
        completed_predictions=completed_predictions,
        average_predicted_views=average_predicted_views,
        average_confidence=average_confidence,
        best_category=best_category,
        timeline=timeline,
        category_breakdown=category_breakdown,
    )
