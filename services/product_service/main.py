from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from shared.common.app import create_service_app
from shared.common.database import Base, get_db, make_session_factory
from shared.common.security import require_roles

SessionFactory = make_session_factory("products")
app = create_service_app("Product Service", SessionFactory)
DbSession = Annotated[Session, Depends(get_db(SessionFactory))]
Seller = Annotated[dict, Depends(require_roles("seller", "admin"))]


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    seller_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[Decimal] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))


class ProductInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    description: str = Field(default="", max_length=4000)
    price: Decimal = Field(gt=0, max_digits=10, decimal_places=2)


def product_response(product: Product) -> dict:
    return {
        "id": product.id,
        "seller_id": product.seller_id,
        "name": product.name,
        "description": product.description,
        "price": product.price,
        "created_at": product.created_at,
    }


@app.get("/products")
def search_products(
    db: DbSession,
    search: str = Query(default="", max_length=160),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    statement = select(Product).order_by(Product.id.desc())
    if search.strip():
        statement = statement.where(Product.name.ilike(f"%{search.strip()}%"))
    products = db.scalars(statement.offset(offset).limit(limit)).all()
    return {"items": [product_response(item) for item in products], "limit": limit, "offset": offset}


@app.get("/products/{product_id}")
def get_product(product_id: int, db: DbSession) -> dict:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product was not found.")
    return product_response(product)


@app.post("/products", status_code=status.HTTP_201_CREATED)
def add_product(payload: ProductInput, db: DbSession, seller: Seller) -> dict:
    product = Product(
        seller_id=seller["id"],
        name=payload.name.strip(),
        description=payload.description.strip(),
        price=payload.price,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return product_response(product)


@app.patch("/products/{product_id}")
def update_product(product_id: int, payload: ProductInput, db: DbSession, seller: Seller) -> dict:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product was not found.")
    if seller["role"] != "admin" and product.seller_id != seller["id"]:
        raise HTTPException(status_code=403, detail="You can only manage your own products.")
    product.name = payload.name.strip()
    product.description = payload.description.strip()
    product.price = payload.price
    db.commit()
    db.refresh(product)
    return product_response(product)


@app.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_product(product_id: int, db: DbSession, seller: Seller) -> None:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product was not found.")
    if seller["role"] != "admin" and product.seller_id != seller["id"]:
        raise HTTPException(status_code=403, detail="You can only manage your own products.")
    db.delete(product)
    db.commit()


@app.get("/seller/products")
def my_products(db: DbSession, seller: Seller) -> list[dict]:
    products = db.scalars(select(Product).where(Product.seller_id == seller["id"])).all()
    return [product_response(product) for product in products]
