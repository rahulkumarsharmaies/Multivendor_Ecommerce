from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import Numeric, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from shared.common.app import create_service_app
from shared.common.database import Base, get_db, make_session_factory
from shared.common.security import current_user, require_internal_token

SessionFactory = make_session_factory("payments")
app = create_service_app("Payment Service", SessionFactory)
DbSession = Annotated[Session, Depends(get_db(SessionFactory))]
Internal = Annotated[None, Depends(require_internal_token)]


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(unique=True, index=True)
    buyer_id: Mapped[int] = mapped_column(index=True)
    buyer_email: Mapped[str] = mapped_column(String(254))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    method: Mapped[str] = mapped_column(String(16), default="COD")
    status: Mapped[str] = mapped_column(String(32), default="pending_collection")
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    collected_at: Mapped[datetime | None] = mapped_column(nullable=True)


class PaymentRequest(BaseModel):
    order_id: int = Field(gt=0)
    buyer_id: int = Field(gt=0)
    buyer_email: EmailStr
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    method: str = "COD"


def payment_response(payment: Payment) -> dict:
    return {
        "id": payment.id,
        "order_id": payment.order_id,
        "buyer_id": payment.buyer_id,
        "amount": payment.amount,
        "method": payment.method,
        "status": payment.status,
        "created_at": payment.created_at,
        "collected_at": payment.collected_at,
    }


@app.post("/internal/payments", status_code=status.HTTP_201_CREATED)
def create_cod_payment(payload: PaymentRequest, db: DbSession, _: Internal) -> dict:
    if payload.method != "COD":
        raise HTTPException(status_code=422, detail="Only cash on delivery is supported.")
    if db.scalar(select(Payment).where(Payment.order_id == payload.order_id)):
        raise HTTPException(status_code=409, detail="A payment already exists for this order.")
    payment = Payment(
        order_id=payload.order_id,
        buyer_id=payload.buyer_id,
        buyer_email=str(payload.buyer_email),
        amount=payload.amount,
        method="COD",
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment_response(payment)


@app.get("/payments/me")
def my_payments(db: DbSession, user: Annotated[dict, Depends(current_user)]) -> list[dict]:
    return [
        payment_response(payment)
        for payment in db.scalars(
            select(Payment).where(Payment.buyer_id == user["id"]).order_by(Payment.id.desc())
        )
    ]


@app.post("/internal/payments/{order_id}/collect")
def collect_payment(order_id: int, db: DbSession, _: Internal) -> dict:
    payment = db.scalar(select(Payment).where(Payment.order_id == order_id))
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment record was not found.")
    if payment.status == "collected":
        return payment_response(payment)
    payment.status = "collected"
    payment.collected_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(payment)
    return payment_response(payment)
