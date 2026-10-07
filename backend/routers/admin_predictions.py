"""Admin predictions explorer: every user's predictions, filtered, with a
summary of the matching set (view and confidence distributions, categories)
so drifting or collapsed model output is visible. Mounted under /admin by
routers/admin.py."""

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from db import get_cursor
from models import AdminPredictionDetail, AdminPredictionList
from routers.admin_common import like_pattern, record_action
from security import get_current_admin
from storage import delete_upload

router = APIRouter(prefix="/predictions")

# Before the ensemble went live every forecast saved used_channel_context =
# TRUE, so "ensemble" / "CatBoost only" only apply from `since` on.
FILTERED_CTE = """
    filtered AS (
        SELECT p.id, p.title, p.category, p.status, p.predicted_views, p.confidence,
               p.used_channel_context, p.created_at, p.user_id, p.thumbnail_path AS thumbnail_url,
               u.email AS user_email, u.full_name AS user_name
        FROM predictions p
        JOIN users u ON u.id = p.user_id
        WHERE (%(pattern)s::text IS NULL
               OR p.title ILIKE %(pattern)s OR u.email ILIKE %(pattern)s OR u.full_name ILIKE %(pattern)s)
          AND (%(status)s::text IS NULL OR p.status = %(status)s::text)
          AND (%(category)s::text IS NULL OR p.category = %(category)s::text)
          AND (%(user_id)s::bigint IS NULL OR p.user_id = %(user_id)s::bigint)
          AND (%(date_from)s::date IS NULL OR p.created_at >= %(date_from)s::date)
          AND (%(date_to)s::date IS NULL OR p.created_at < %(date_to)s::date + 1)
          AND (%(model)s::text IS NULL
               OR (p.status = 'complete'
                   AND p.created_at >= COALESCE(%(since)s::timestamptz, '-infinity')
                   AND p.used_channel_context IS NOT DISTINCT FROM (%(model)s::text = 'ensemble')))
    ),
    complete AS (
        SELECT * FROM filtered WHERE status = 'complete'
    )
"""

LIST_SQL = "WITH " + FILTERED_CTE + """
    SELECT
        (SELECT COUNT(*) FROM filtered),
        COALESCE((SELECT json_agg(page ORDER BY created_at DESC, id DESC) FROM (
            SELECT * FROM filtered ORDER BY created_at DESC, id DESC LIMIT %(limit)s OFFSET %(offset)s
        ) page), '[]'::json),
        (SELECT json_build_object(
            'count', (SELECT COUNT(*) FROM filtered),
            'complete', COUNT(*),
            'avg_confidence', AVG(confidence),
            'median_views', percentile_cont(0.5) WITHIN GROUP (ORDER BY predicted_views),
            'p90_views', percentile_cont(0.9) WITHIN GROUP (ORDER BY predicted_views)
        ) FROM complete),
        COALESCE((SELECT json_agg(h ORDER BY h.low) FROM (
            SELECT power(10, b) AS low, power(10, b + 1) AS high, COUNT(*) AS count
            FROM (SELECT floor(log(GREATEST(predicted_views, 1)))::int AS b
                  FROM complete WHERE predicted_views IS NOT NULL) x
            GROUP BY b
        ) h), '[]'::json),
        (SELECT json_agg(json_build_object(
                    'low', bucket / 10.0, 'high', (bucket + 1) / 10.0,
                    'count', (SELECT COUNT(*) FROM complete
                              WHERE confidence IS NOT NULL
                                AND LEAST(GREATEST(floor(confidence * 10)::int, 0), 9) = bucket))
                ORDER BY bucket)
         FROM generate_series(0, 9) AS bucket),
        COALESCE((SELECT json_agg(c ORDER BY c.count DESC, c.category) FROM (
            SELECT category, COUNT(*) AS count FROM filtered WHERE category IS NOT NULL GROUP BY category
        ) c), '[]'::json),
        ARRAY(SELECT DISTINCT category FROM predictions WHERE category IS NOT NULL ORDER BY category)
"""


@router.get("", response_model=AdminPredictionList)
def list_predictions(
    q: str | None = None,
    status: Literal["all", "complete", "draft"] = "all",
    category: str | None = None,
    model: Literal["all", "ensemble", "catboost_only"] = "all",
    user_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    ensemble_since: datetime | None = None,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    admin: dict = Depends(get_current_admin),
):
    params = {
        "pattern": like_pattern(q),
        "status": None if status == "all" else status,
        "category": category or None,
        "model": None if model == "all" else model,
        "user_id": user_id,
        "date_from": date_from,
        "date_to": date_to,
        "since": ensemble_since,
        "limit": limit,
        "offset": offset,
    }
    with get_cursor() as cur:
        cur.execute(LIST_SQL, params)
        total, items, stats, views_hist, confidence_hist, categories, all_categories = cur.fetchone()

    return AdminPredictionList(
        total=total,
        items=items,
        summary={**stats, "views_histogram": views_hist, "confidence_histogram": confidence_hist,
                 "categories": categories},
        all_categories=all_categories,
    )


DETAIL_SQL = """
    SELECT p.*, u.email AS user_email, u.full_name AS user_name
    FROM predictions p
    JOIN users u ON u.id = p.user_id
    WHERE p.id = %(id)s
"""


@router.get("/{prediction_id}", response_model=AdminPredictionDetail)
def get_prediction(prediction_id: int, admin: dict = Depends(get_current_admin)):
    with get_cursor() as cur:
        cur.execute(DETAIL_SQL, {"id": prediction_id})
        columns = [col.name for col in cur.description]
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Prediction not found")
    p = dict(zip(columns, row))
    return AdminPredictionDetail(
        **{k: p[k] for k in (
            "id", "title", "category", "status", "target_date", "target_time", "predicted_views",
            "confidence", "change_vs_avg", "v_inf", "tau", "used_channel_context", "created_at",
            "user_id", "user_email", "user_name",
        )},
        tags=p["tags"] or [],
        trajectory=p["trajectory"] or [],
        thumbnail_url=p["thumbnail_path"],
        dataset_url=p["dataset_path"],
    )


DELETE_SQL = """
    DELETE FROM predictions p
    USING users u
    WHERE p.id = %(id)s AND u.id = p.user_id
    RETURNING p.title, p.user_id, u.email, p.thumbnail_path, p.dataset_path
"""


@router.delete("/{prediction_id}", status_code=204)
def delete_prediction(prediction_id: int, admin: dict = Depends(get_current_admin)):
    with get_cursor(commit=True) as cur:
        cur.execute(DELETE_SQL, {"id": prediction_id})
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Prediction not found")
        title, owner_id, owner_email, thumbnail_path, dataset_path = row
        record_action(cur, admin, "prediction.delete", target_type="prediction", target_id=prediction_id,
                      details={"title": title, "user_id": owner_id, "user_email": owner_email})

    for path in (thumbnail_path, dataset_path):
        delete_upload(path)
