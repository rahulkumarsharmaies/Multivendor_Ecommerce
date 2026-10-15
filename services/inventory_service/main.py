import os
from datetime import datetime, timezone
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import Integer, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from shared.common.app import create_service_app
from shared.common.database import Base, get_db, make_session_factory
from shared.common.security import require_internal_token, require_roles

SessionFactory = make_session_factory("inventory")
app = create_service_app("Inventory Service", SessionFactory)
DbSession = Annotated[Session, Depends(get_db(SessionFactory))]
Seller = Annotated[dict, Depends(require_roles("seller", "admin"))]
Internal = Annotated[None, Depends(require_internal_token)]


class Inventory(Base):
    __tablename__ = "inventory"

    product_id: Mapped[int] = mapped_column(primary_key=True)
    seller_id: Mapped[int] = mapped_column(index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class StockInput(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(ge=0)


class QuantityChange(BaseModel):
    quantity: int = Field(gt=0, le=100_000)


def inventory_response(item: Inventory) -> dict:
    return {
        "product_id": item.product_id,
        "seller_id": item.seller_id,
        "quantity": item.quantity,
        "updated_at": item.updated_at,
    }


@app.post("/inventory", status_code=status.HTTP_201_CREATED)
def register_stock(payload: StockInput, db: DbSession, seller: Seller) -> dict:
    product_service_url = os.getenv("PRODUCT_SERVICE_URL", "http://localhost:8002").rstrip("/")
    try:
        response = httpx.get(
            f"{product_service_url}/products/{payload.product_id}",
            timeout=5.0,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Product was not found.") from error
        raise HTTPException(status_code=502, detail="Product service rejected the request.") from error
    except httpx.RequestError as error:
        raise HTTPException(status_code=503, detail="Product service is unavailable.") from error
    product = response.json()
    if seller["role"] != "admin" and product["seller_id"] != seller["id"]:
        raise HTTPException(status_code=403, detail="You can only register stock for your products.")
    existing = db.get(Inventory, payload.product_id)
    if existing is not None:
        raise HTTPException(status_code=409, detail="Inventory is already registered for this product.")
    item = Inventory(
        product_id=payload.product_id,
        seller_id=seller["id"],
        quantity=payload.quantity,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return inventory_response(item)


@app.get("/inventory/{product_id}")
def get_stock(product_id: int, db: DbSession) -> dict:
    item = db.get(Inventory, product_id)
    if item is None:
        return {"product_id": product_id, "quantity": 0}
    return inventory_response(item)


@app.patch("/inventory/{product_id}")
def adjust_stock(product_id: int, payload: QuantityChange, db: DbSession, seller: Seller) -> dict:
    item = db.get(Inventory, product_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Inventory was not registered.")
    if seller["role"] != "admin" and item.seller_id != seller["id"]:
        raise HTTPException(status_code=403, detail="You can only manage your own inventory.")
    item.quantity += payload.quantity
    db.commit()
    db.refresh(item)
    return inventory_response(item)


def change_reserved_stock(db: Session, product_id: int, quantity: int, reserve: bool) -> dict:
    item = db.get(Inventory, product_id)
    if item is None:
        raise HTTPException(status_code=404, detail="No stock is registered for this product.")
    condition = Inventory.quantity >= quantity if reserve else True
    delta = -quantity if reserve else quantity
    result = db.execute(
        update(Inventory)
        .where(Inventory.product_id == product_id, condition)
        .values(quantity=Inventory.quantity + delta)
    )
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="Insufficient stock for this order.")
    db.commit()
    db.refresh(item)
    return inventory_response(item)


@app.post("/internal/inventory/{product_id}/reserve", dependencies=[Depends(require_internal_token)])
def reserve_stock(product_id: int, payload: QuantityChange, db: DbSession) -> dict:
    return change_reserved_stock(db, product_id, payload.quantity, reserve=True)


@app.post("/internal/inventory/{product_id}/release", dependencies=[Depends(require_internal_token)])
def release_stock(product_id: int, payload: QuantityChange, db: DbSession) -> dict:
    return change_reserved_stock(db, product_id, payload.quantity, reserve=False)
