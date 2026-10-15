import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Annotated

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from shared.common.app import create_service_app
from shared.common.database import Base, get_db, make_session_factory
from shared.common.security import current_user, require_internal_token

SessionFactory = make_session_factory("notifications")
app = create_service_app("Notification Service", SessionFactory)
DbSession = Annotated[Session, Depends(get_db(SessionFactory))]
Internal = Annotated[None, Depends(require_internal_token)]


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    order_id: Mapped[int] = mapped_column(index=True)
    email: Mapped[str] = mapped_column(String(254))
    subject: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    delivery_status: Mapped[str] = mapped_column(String(24), default="queued")
    delivery_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))


class OrderUpdate(BaseModel):
    user_id: int = Field(gt=0)
    email: EmailStr
    order_id: int = Field(gt=0)
    status: str = Field(min_length=2, max_length=32)
    message: str = Field(min_length=1, max_length=1000)


def notification_response(notification: Notification) -> dict:
    return {
        "id": notification.id,
        "order_id": notification.order_id,
        "subject": notification.subject,
        "message": notification.message,
        "delivery_status": notification.delivery_status,
        "delivery_error": notification.delivery_error,
        "created_at": notification.created_at,
    }


def send_email(notification: Notification) -> tuple[str, str | None]:
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        return "queued", None
    sender = os.getenv("SMTP_FROM", "").strip()
    if not sender:
        raise RuntimeError("SMTP_FROM is required when SMTP_HOST is configured.")
    message = EmailMessage()
    message["Subject"] = notification.subject
    message["From"] = sender
    message["To"] = notification.email
    message.set_content(notification.message)
    try:
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=10) as server:
            if os.getenv("SMTP_STARTTLS", "true").lower() == "true":
                server.starttls()
            username = os.getenv("SMTP_USERNAME", "")
            if username:
                server.login(username, os.getenv("SMTP_PASSWORD", ""))
            server.send_message(message)
        return "sent", None
    except (OSError, smtplib.SMTPException) as error:
        return "failed", str(error)[:1000]


@app.post("/internal/order-updates", status_code=status.HTTP_201_CREATED)
def create_order_update(payload: OrderUpdate, db: DbSession, _: Internal) -> dict:
    notification = Notification(
        user_id=payload.user_id,
        order_id=payload.order_id,
        email=str(payload.email),
        subject=f"Order #{payload.order_id} update",
        message=payload.message,
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)
    delivery_status, delivery_error = send_email(notification)
    notification.delivery_status = delivery_status
    notification.delivery_error = delivery_error
    db.commit()
    db.refresh(notification)
    return notification_response(notification)


@app.get("/notifications/me")
def my_notifications(db: DbSession, user: Annotated[dict, Depends(current_user)]) -> list[dict]:
    return [
        notification_response(notification)
        for notification in db.scalars(
            select(Notification)
            .where(Notification.user_id == user["id"])
            .order_by(Notification.id.desc())
            .limit(100)
        )
    ]
