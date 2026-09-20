import io
from datetime import date as date_cls
from datetime import datetime, time, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image
from psycopg2.extras import Json

from db import get_cursor
from inference import (
    ChannelNotFoundError,
    InsufficientHistoryError,
    QuotaExceededError,
    get_state,
    run_forecast_on_image,
)
from models import PredictionOut
from routers.notifications import create_notification
from security import get_current_user
from storage import save_upload

router = APIRouter(prefix="/predictions", tags=["predictions"])

MAX_THUMBNAIL_BYTES = 10 * 1024 * 1024   # 10 MB
MAX_DATASET_BYTES = 50 * 1024 * 1024     # 50 MB
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}

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

# The model does not emit a calibrated confidence. What it does emit is an
# uncertainty BAND (range_7d), and the width of that band relative to the point
# estimate is a genuine signal. We derive a 0-1 score from it so the UI has
# something meaningful rather than a hardcoded constant.
#
# Narrow band -> higher score. This is still a heuristic, but it varies with the
# actual prediction instead of merely recording which code path ran.
def _confidence_from_band(point: float, low: float, high: float) -> float:
    if point <= 0 or high <= low:
        return 0.3
    relative_width = (high - low) / point
    # relative_width ~2.0 is typical; ~4.0 is very uncertain
    score = 1.0 / (1.0 + relative_width / 2.0)
    return round(min(max(score, 0.05), 0.95), 3)


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


def _read_limited(upload: UploadFile, max_bytes: int, label: str) -> bytes:
    data = upload.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"{label} exceeds the {max_bytes // (1024 * 1024)}MB limit",
        )
    return data


@router.post("", response_model=PredictionOut)
def create_prediction(
    user: dict = Depends(get_current_user),
    title: str = Form(...),
    category: str | None = Form(None),
    category_id: int | None = Form(None),
    description: str | None = Form(None),
    tags: str = Form(""),
    target_date: str | None = Form(None),
    target_time: str | None = Form(None),
    duration: str | None = Form(None),
    save_as_draft: str = Form("false"),
    thumbnail: UploadFile | None = File(None),
    dataset: UploadFile | None = File(None),
):
    is_draft = save_as_draft.strip().lower() in {"true", "1", "yes", "on"}
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    if not is_draft and thumbnail is None:
        raise HTTPException(status_code=400, detail="A thumbnail is required to run a prediction")

    thumbnail_bytes: bytes | None = None
    thumbnail_path: str | None = None
    if thumbnail is not None:
        if thumbnail.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported thumbnail type '{thumbnail.content_type}'. Use JPEG, PNG or WebP.",
            )
        thumbnail_bytes = _read_limited(thumbnail, MAX_THUMBNAIL_BYTES, "Thumbnail")
        thumbnail_path = save_upload(thumbnail_bytes, thumbnail.filename)

    dataset_path = None
    if dataset is not None:
        dataset_path = save_upload(
            _read_limited(dataset, MAX_DATASET_BYTES, "Dataset"), dataset.filename
        )

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
            raise HTTPException(
                status_code=400, detail=f"Thumbnail was not a valid image: {exc}"
            ) from exc

        scheduled_upload_time = _parse_scheduled_upload_time(target_date, target_time)
        channel_data = user.get("channel_data") or {}
        channel_id = channel_data.get("channel_id")

        if not channel_id:
            raise HTTPException(
                status_code=400,
                detail="Your account is not linked to a YouTube channel, "
                       "so we cannot estimate a baseline for your forecasts.",
            )

        try:
            result = run_forecast_on_image(
                get_state(),
                title=title,
                image=image,
                scheduled_upload_time=scheduled_upload_time,
                channel_id=channel_id,
                tags=tag_list,
                duration=duration,
                description=description,
                category_id=category_id,
            )
        except InsufficientHistoryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ChannelNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail="We could not find your YouTube channel. Check that it is public.",
            ) from exc
        except QuotaExceededError as exc:
            raise HTTPException(
                status_code=503,
                detail="Forecasting is temporarily unavailable (YouTube API limit reached). "
                       "Please try again later.",
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=503, detail=f"Forecast service unavailable: {exc}"
            ) from exc

        predicted_views = int(result["point_estimate_7d"])
        confidence = _confidence_from_band(
            result["point_estimate_7d"],
            result["range_7d"]["low"],
            result["range_7d"]["high"],
        )
        baseline = result["channel_baseline"]
        change_vs_avg = (predicted_views - baseline) / baseline if baseline else None

        shape_params = result.get("shape_params", {})

        fields.update(
            predicted_views=predicted_views,
            confidence=confidence,
            change_vs_avg=change_vs_avg,
            trajectory=Json(result["curve"]),
            # v_inf: the asymptotic 7-day total. tau: the curve's timing
            # parameter (t0 for logistic, c for power-law). Both are real
            # outputs of the shape model, not placeholders.
            v_inf=float(result["point_estimate_7d"]),
            tau=float(shape_params.get("t0", shape_params.get("c", 0.0))),
            used_channel_context=bool(result["used_channel_context"]),
        )

    with get_cursor(commit=True) as cur:
        cur.execute(INSERT_SQL, fields)
        prediction_id, created_at = cur.fetchone()

    if not is_draft:
        try:
            create_notification(
                user["id"],
                "prediction_complete",
                "Prediction ready",
                f'Your prediction for "{title}" is ready to view.',
            )
        except Exception:  # noqa: BLE001
            # the prediction is already committed; a failed notification
            # should not surface as a request failure
            pass

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