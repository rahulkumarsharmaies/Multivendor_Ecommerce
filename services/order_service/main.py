import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import ForeignKey, Numeric, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from shared.common.app import create_service_app
from shared.common.database import Base, get_db, make_session_factory
from shared.common.security import current_user

logger = logging.getLogger(__name__)
SessionFactory = make_session_factory("orders")
app = create_service_app("Order Service", SessionFactory)
DbSession = Annotated[Session, Depends(get_db(SessionFactory))]
ORDER_STATUSES = {"placed", "processing", "shipped", "delivered", "cancelled"}
ALLOWED_TRANSITIONS = {
    "placed": {"processing", "cancelled"},
    "processing": {"shipped", "cancelled"},
    "shipped": {"delivered"},
    "delivered": set(),
    "cancelled": set(),
}


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    buyer_id: Mapped[int] = mapped_column(index=True)
    buyer_email: Mapped[str] = mapped_column(String(254))
    delivery_address: Mapped[str] = mapped_column(Text)
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(24), default="placed")
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[int] = mapped_column(index=True)
    seller_id: Mapped[int] = mapped_column(index=True)
    product_name: Mapped[str] = mapped_column(String(160))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    quantity: Mapped[int]
    order: Mapped[Order] = relationship(back_populates="items")


class RequestedItem(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(ge=1, le=100)


class CreateOrderRequest(BaseModel):
    items: list[RequestedItem] = Field(min_length=1, max_length=50)
    delivery_address: str = Field(min_length=8, max_length=1000)


class StatusUpdate(BaseModel):
    status: str


def service_url(name: str) -> str:
    defaults = {
        "PRODUCT_SERVICE_URL": "http://localhost:8002",
        "INVENTORY_SERVICE_URL": "http://localhost:8003",
        "PAYMENT_SERVICE_URL": "http://localhost:8005",
        "NOTIFICATION_SERVICE_URL": "http://localhost:8006",
    }
    return os.getenv(name, defaults[name]).rstrip("/")


def internal_headers() -> dict[str, str]:
    token = os.getenv("INTERNAL_API_TOKEN", "")
    if not token:
        raise RuntimeError("INTERNAL_API_TOKEN must be configured for internal APIs.")
    return {"X-Internal-Token": token}


def call_service(method: str, url: str, **kwargs) -> httpx.Response:
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.request(method, url, **kwargs)
            response.raise_for_status()
            return response
    except httpx.HTTPStatusError as error:
        detail = "A dependent service rejected the request."
        try:
            detail = error.response.json().get("detail", detail)
        except ValueError:
            pass
        raise HTTPException(
            status_code=502,
            detail=f"Dependent service error: {detail}",
        ) from error
    except httpx.RequestError as error:
        raise HTTPException(
            status_code=503,
            detail="A required service is unavailable; the order was not completed.",
        ) from error


def release_reservations(reserved: list[tuple[int, int]]) -> None:
    for product_id, quantity in reversed(reserved):
        try:
            call_service(
                "POST",
                f"{service_url('INVENTORY_SERVICE_URL')}/internal/inventory/{product_id}/release",
                headers=internal_headers(),
                json={"quantity": quantity},
            )
        except (HTTPException, RuntimeError):
            logger.exception("Could not release reserved stock for product %s", product_id)


def order_response(order: Order) -> dict:
    return {
        "id": order.id,
        "buyer_id": order.buyer_id,
        "delivery_address": order.delivery_address,
        "total": order.total,
        "status": order.status,
        "created_at": order.created_at,
        "payment_method": "COD",
        "items": [
            {
                "product_id": item.product_id,
                "seller_id": item.seller_id,
                "product_name": item.product_name,
                "unit_price": item.unit_price,
                "quantity": item.quantity,
                "line_total": item.unit_price * item.quantity,
            }
            for item in order.items
        ],
    }


def notify_order(order: Order) -> None:
    try:
        call_service(
            "POST",
            f"{service_url('NOTIFICATION_SERVICE_URL')}/internal/order-updates",
            headers=internal_headers(),
            json={
                "user_id": order.buyer_id,
                "email": order.buyer_email,
                "order_id": order.id,
                "status": order.status,
                "message": f"Your order #{order.id} is now {order.status}. "
                f"Total: {order.total}. Payment: cash on delivery.",
            },
        )
    except (HTTPException, RuntimeError):
        logger.exception("Could not create notification for order %s", order.id)


@app.post("/orders", status_code=status.HTTP_201_CREATED)
def create_order(
    payload: CreateOrderRequest,
    db: DbSession,
    user: Annotated[dict, Depends(current_user)],
) -> dict:
    if user["role"] != "buyer":
        raise HTTPException(status_code=403, detail="Only buyer accounts can place orders.")
    quantities: dict[int, int] = {}
    for requested in payload.items:
        quantities[requested.product_id] = quantities.get(requested.product_id, 0) + requested.quantity
    if any(quantity > 100 for quantity in quantities.values()):
        raise HTTPException(status_code=422, detail="Maximum quantity per product is 100.")

    product_data: list[dict] = []
    total = Decimal("0.00")
    for product_id, quantity in quantities.items():
        product = call_service(
            "GET", f"{service_url('PRODUCT_SERVICE_URL')}/products/{product_id}"
        ).json()
        product_data.append({**product, "quantity": quantity})
        total += Decimal(str(product["price"])) * quantity

    seller_ids = {item["seller_id"] for item in product_data}
    if len(seller_ids) > 1:
        raise HTTPException(
            status_code=422,
            detail="Each order can contain products from one seller only.",
        )

    reserved: list[tuple[int, int]] = []
    try:
        for item in product_data:
            call_service(
                "POST",
                f"{service_url('INVENTORY_SERVICE_URL')}/internal/inventory/"
                f"{item['id']}/reserve",
                headers=internal_headers(),
                json={"quantity": item["quantity"]},
            )
            reserved.append((item["id"], item["quantity"]))
    except (HTTPException, RuntimeError):
        release_reservations(reserved)
        raise

    order = Order(
        buyer_id=user["id"],
        buyer_email=user["email"],
        delivery_address=payload.delivery_address.strip(),
        total=total,
        status="placed",
        items=[
            OrderItem(
                product_id=item["id"],
                seller_id=item["seller_id"],
                product_name=item["name"],
                unit_price=Decimal(str(item["price"])),
                quantity=item["quantity"],
            )
            for item in product_data
        ],
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    try:
        call_service(
            "POST",
            f"{service_url('PAYMENT_SERVICE_URL')}/internal/payments",
            headers=internal_headers(),
            json={
                "order_id": order.id,
                "buyer_id": order.buyer_id,
                "buyer_email": order.buyer_email,
                "amount": str(order.total),
                "method": "COD",
            },
        )
    except (HTTPException, RuntimeError):
        order.status = "cancelled"
        db.commit()
        release_reservations(reserved)
        raise HTTPException(
            status_code=502,
            detail="Payment service did not accept the COD record; the order was cancelled.",
        ) from None

    notify_order(order)
    return order_response(order)


@app.get("/orders/me")
def list_my_orders(
    db: DbSession,
    user: Annotated[dict, Depends(current_user)],
) -> list[dict]:
    if user["role"] == "buyer":
        orders = db.scalars(
            select(Order).where(Order.buyer_id == user["id"]).order_by(Order.id.desc())
        ).all()
    elif user["role"] in {"seller", "admin"}:
        statement = select(Order).order_by(Order.id.desc())
        orders = db.scalars(statement).all()
        if user["role"] == "seller":
            orders = [
                order for order in orders
                if any(item.seller_id == user["id"] for item in order.items)
            ]
    else:
        raise HTTPException(status_code=403, detail="Account role cannot view orders.")
    return [order_response(order) for order in orders]


@app.get("/orders/{order_id}")
def get_order(
    order_id: int,
    db: DbSession,
    user: Annotated[dict, Depends(current_user)],
) -> dict:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order was not found.")
    is_buyer = order.buyer_id == user["id"]
    owns_item = any(item.seller_id == user["id"] for item in order.items)
    if not is_buyer and not owns_item and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="You cannot view this order.")
    return order_response(order)


@app.patch("/orders/{order_id}/status")
def update_order_status(
    order_id: int,
    payload: StatusUpdate,
    db: DbSession,
    user: Annotated[dict, Depends(current_user)],
) -> dict:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order was not found.")
    new_status = payload.status.strip().lower()
    if new_status not in ORDER_STATUSES:
        raise HTTPException(status_code=422, detail="Unsupported order status.")
    if new_status not in ALLOWED_TRANSITIONS[order.status]:
        raise HTTPException(
            status_code=409,
            detail=f"Order cannot transition from {order.status} to {new_status}.",
        )
    if user["role"] == "buyer":
        if order.buyer_id != user["id"] or new_status != "cancelled":
            raise HTTPException(status_code=403, detail="Buyers may only cancel their own order.")
    elif user["role"] == "seller":
        if not order.items or any(item.seller_id != user["id"] for item in order.items):
            raise HTTPException(
                status_code=403,
                detail="Seller status updates are limited to orders containing only their products.",
            )
    elif user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Insufficient permissions.")

    order.status = new_status
    db.commit()
    db.refresh(order)
    if new_status == "cancelled":
        release_reservations([(item.product_id, item.quantity) for item in order.items])
    if new_status == "delivered":
        try:
            call_service(
                "POST",
                f"{service_url('PAYMENT_SERVICE_URL')}/internal/payments/{order.id}/collect",
                headers=internal_headers(),
            )
        except (HTTPException, RuntimeError):
            logger.exception("Could not record COD collection for order %s", order.id)
    notify_order(order)
    return order_response(order)
