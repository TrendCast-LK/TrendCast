import io
from datetime import date as date_cls
from datetime import datetime, time, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image
from psycopg2.extras import Json

from db import get_cursor
from inference import get_state, run_forecast_on_image
from models import PredictionOut
from routers.notifications import create_notification
from security import get_current_user
from storage import save_upload

router = APIRouter(prefix="/predictions", tags=["predictions"])

INSERT_SQL = """
    INSERT INTO predictions (
        user_id, title, category, tags, target_date, target_time,
        thumbnail_path, dataset_path, status,
        predicted_views, confidence, change_vs_avg, trajectory,
        v_inf, tau, used_channel_context
    ) VALUES (
        %(user_id)s, %(title)s, %(category)s, %(tags)s, %(target_date)s, %(target_time)s,
        %(thumbnail_path)s, %(dataset_path)s, %(status)s,
        %(predicted_views)s, %(confidence)s, %(change_vs_avg)s, %(trajectory)s,
        %(v_inf)s, %(tau)s, %(used_channel_context)s
    )
    RETURNING id, created_at
"""

SELECT_ONE_SQL = "SELECT * FROM predictions WHERE id = %(id)s AND user_id = %(user_id)s"
SELECT_ALL_SQL = "SELECT * FROM predictions WHERE user_id = %(user_id)s ORDER BY created_at DESC"
DELETE_SQL = "DELETE FROM predictions WHERE id = %(id)s AND user_id = %(user_id)s"

# Heuristic, not a model output: the XGBoost regressors behind run_forecast_on_image
# don't produce a real confidence/uncertainty estimate, so this reflects whether
# the prediction used the caller's actual channel stats (more reliable) or fell
# back to dataset-wide medians (a rougher estimate).
CONFIDENCE_WITH_CHANNEL_CONTEXT = 0.85
CONFIDENCE_WITHOUT_CHANNEL_CONTEXT = 0.55


def row_to_prediction_out(row: dict) -> PredictionOut:
    return PredictionOut(
        id=row["id"],
        title=row["title"],
        category=row["category"],
        tags=row["tags"] or [],
        status=row["status"],
        target_date=row["target_date"],
        target_time=row["target_time"],
        thumbnail_url=row["thumbnail_path"],
        predicted_views=row["predicted_views"],
        confidence=row["confidence"],
        change_vs_avg=row["change_vs_avg"],
        trajectory=row["trajectory"] or [],
        v_inf=row["v_inf"],
        tau=row["tau"],
        used_channel_context=row["used_channel_context"],
        created_at=row["created_at"],
    )


def _parse_scheduled_upload_time(target_date: str | None, target_time: str | None) -> datetime:
    if not target_date:
        return datetime.now(timezone.utc)
    parsed_date = date_cls.fromisoformat(target_date)
    parsed_time = time.fromisoformat(target_time) if target_time else time(0, 0)
    return datetime.combine(parsed_date, parsed_time, tzinfo=timezone.utc)


@router.post("", response_model=PredictionOut)
def create_prediction(
    user: dict = Depends(get_current_user),
    title: str = Form(...),
    category: str | None = Form(None),
    tags: str = Form(""),
    target_date: str | None = Form(None),
    target_time: str | None = Form(None),
    save_as_draft: str = Form("false"),
    thumbnail: UploadFile | None = File(None),
    dataset: UploadFile | None = File(None),
):
    is_draft = save_as_draft.lower() == "true"
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    if not is_draft and thumbnail is None:
        raise HTTPException(status_code=400, detail="A thumbnail is required to run a prediction")

    thumbnail_path = None
    if thumbnail is not None:
        thumbnail_bytes = thumbnail.file.read()
        thumbnail_path = save_upload(thumbnail_bytes, thumbnail.filename)

    dataset_path = None
    if dataset is not None:
        dataset_path = save_upload(dataset.file.read(), dataset.filename)

    fields = {
        "user_id": user["id"],
        "title": title,
        "category": category,
        "tags": tag_list,
        "target_date": date_cls.fromisoformat(target_date) if target_date else None,
        "target_time": target_time or None,
        "thumbnail_path": thumbnail_path,
        "dataset_path": dataset_path,
        "status": "draft" if is_draft else "complete",
        "predicted_views": None,
        "confidence": None,
        "change_vs_avg": None,
        "trajectory": None,
        "v_inf": None,
        "tau": None,
        "used_channel_context": None,
    }

    if not is_draft:
        try:
            image = Image.open(io.BytesIO(thumbnail_bytes)).convert("RGB")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"thumbnail was not a valid image: {exc}") from exc

        scheduled_upload_time = _parse_scheduled_upload_time(target_date, target_time)
        channel_data = user.get("channel_data") or {}
        channel_id = channel_data.get("channel_id")

        result = run_forecast_on_image(get_state(), title, image, scheduled_upload_time, channel_id)

        predicted_views = result["curve"][-1]["views"]
        confidence = (
            CONFIDENCE_WITH_CHANNEL_CONTEXT if result["used_channel_context"] else CONFIDENCE_WITHOUT_CHANNEL_CONTEXT
        )
        avg_views_per_video = result["avg_views_per_video"]
        change_vs_avg = (
            (predicted_views - avg_views_per_video) / avg_views_per_video if avg_views_per_video else None
        )

        fields.update(
            predicted_views=predicted_views,
            confidence=confidence,
            change_vs_avg=change_vs_avg,
            trajectory=Json(result["curve"]),
            v_inf=result["v_inf"],
            tau=result["tau"],
            used_channel_context=result["used_channel_context"],
        )

    with get_cursor(commit=True) as cur:
        cur.execute(INSERT_SQL, fields)
        prediction_id, created_at = cur.fetchone()

    if not is_draft:
        create_notification(
            user["id"],
            "prediction_complete",
            "Prediction ready",
            f'Your prediction for "{title}" is ready to view.',
        )

    with get_cursor() as cur:
        cur.execute(SELECT_ONE_SQL, {"id": prediction_id, "user_id": user["id"]})
        columns = [col.name for col in cur.description]
        row = dict(zip(columns, cur.fetchone()))

    return row_to_prediction_out(row)


@router.get("", response_model=list[PredictionOut])
def list_predictions(user: dict = Depends(get_current_user)):
    with get_cursor() as cur:
        cur.execute(SELECT_ALL_SQL, {"user_id": user["id"]})
        columns = [col.name for col in cur.description]
        rows = cur.fetchall()
    return [row_to_prediction_out(dict(zip(columns, row))) for row in rows]


@router.get("/{prediction_id}", response_model=PredictionOut)
def get_prediction(prediction_id: int, user: dict = Depends(get_current_user)):
    with get_cursor() as cur:
        cur.execute(SELECT_ONE_SQL, {"id": prediction_id, "user_id": user["id"]})
        columns = [col.name for col in cur.description] if cur.description else []
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Prediction not found")
    return row_to_prediction_out(dict(zip(columns, row)))


@router.delete("/{prediction_id}", status_code=204)
def delete_prediction(prediction_id: int, user: dict = Depends(get_current_user)):
    with get_cursor(commit=True) as cur:
        cur.execute(DELETE_SQL, {"id": prediction_id, "user_id": user["id"]})
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Prediction not found")
