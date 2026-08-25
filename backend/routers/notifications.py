from fastapi import APIRouter, Depends, HTTPException

from db import get_cursor
from models import NotificationOut, NotificationsResponse
from security import get_current_user

router = APIRouter(prefix="/notifications", tags=["notifications"])

LIST_SQL = """
    SELECT id, type, title, message, read, created_at
    FROM notifications
    WHERE user_id = %(user_id)s
    ORDER BY created_at DESC
"""

UNREAD_COUNT_SQL = """
    SELECT COUNT(*) FROM notifications WHERE user_id = %(user_id)s AND NOT read
"""

INSERT_SQL = """
    INSERT INTO notifications (user_id, type, title, message)
    VALUES (%(user_id)s, %(type)s, %(title)s, %(message)s)
"""

MARK_READ_SQL = """
    UPDATE notifications SET read = TRUE
    WHERE id = %(id)s AND user_id = %(user_id)s
"""

MARK_ALL_READ_SQL = """
    UPDATE notifications SET read = TRUE WHERE user_id = %(user_id)s AND NOT read
"""


def create_notification(user_id: int, type_: str, title: str, message: str) -> None:
    with get_cursor(commit=True) as cur:
        cur.execute(INSERT_SQL, {"user_id": user_id, "type": type_, "title": title, "message": message})


@router.get("", response_model=NotificationsResponse)
def list_notifications(user: dict = Depends(get_current_user)):
    with get_cursor() as cur:
        cur.execute(LIST_SQL, {"user_id": user["id"]})
        columns = [col.name for col in cur.description]
        rows = cur.fetchall()
        cur.execute(UNREAD_COUNT_SQL, {"user_id": user["id"]})
        unread_count = cur.fetchone()[0]

    notifications = [NotificationOut(**dict(zip(columns, row))) for row in rows]
    return NotificationsResponse(notifications=notifications, unread_count=unread_count)


@router.post("/{notification_id}/read")
def mark_notification_read(notification_id: int, user: dict = Depends(get_current_user)):
    with get_cursor(commit=True) as cur:
        cur.execute(MARK_READ_SQL, {"id": notification_id, "user_id": user["id"]})
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Notification not found")
    return {"status": "ok"}


@router.post("/read-all")
def mark_all_notifications_read(user: dict = Depends(get_current_user)):
    with get_cursor(commit=True) as cur:
        cur.execute(MARK_ALL_READ_SQL, {"user_id": user["id"]})
    return {"status": "ok"}
