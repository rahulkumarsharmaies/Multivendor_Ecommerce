from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from services.inventory_service.main import (
    Inventory,
    SessionFactory as InventorySessionFactory,
    app as inventory_app,
    change_reserved_stock,
)
from services.inventory_service.main import httpx as inventory_httpx
from services.notification_service.main import (
    SessionFactory as NotificationSessionFactory,
    app as notification_app,
)
from services.order_service.main import (
    ALLOWED_TRANSITIONS,
    Order,
    OrderItem,
    SessionFactory as OrderSessionFactory,
    app as order_app,
    order_response,
)
import services.order_service.main as order_service_module
from services.payment_service.main import (
    SessionFactory as PaymentSessionFactory,
    app as payment_app,
)
from services.product_service.main import (
    SessionFactory as ProductSessionFactory,
    app as product_app,
)
from services.user_service.main import SessionFactory as UserSessionFactory, app as user_app
from shared.common.database import Base
from shared.common.security import create_access_token, current_user, hash_password, verify_password


def test_password_hash_is_salted_and_verifies() -> None:
    first = hash_password("strong-password-2026")
    second = hash_password("strong-password-2026")

    assert first != second
    assert verify_password("strong-password-2026", first)
    assert not verify_password("incorrect-password", first)


def test_bearer_token_restores_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-secret-that-is-long-enough-for-hmac")
    token = create_access_token(42, "buyer@example.com", "buyer")

    assert current_user(f"Bearer {token}") == {
        "id": 42,
        "email": "buyer@example.com",
        "role": "buyer",
    }


def test_user_registration_login_and_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-secret-that-is-long-enough-for-hmac")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    UserSessionFactory.configure(bind=engine)
    with TestClient(user_app) as client:
        registration = client.post(
            "/auth/register",
            json={
                "full_name": "Market Buyer",
                "email": "market.buyer@example.com",
                "password": "strong-password-2026",
                "role": "buyer",
            },
        )
        assert registration.status_code == 201

        login = client.post(
            "/auth/login",
            json={"email": "market.buyer@example.com", "password": "strong-password-2026"},
        )
        assert login.status_code == 200
        token = login.json()["access_token"]

        profile = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
        assert profile.status_code == 200
        assert profile.json()["email"] == "market.buyer@example.com"
        assert client.get("/users/me").status_code == 401
    engine.dispose()


def test_seller_can_manage_only_owned_products(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-secret-that-is-long-enough-for-hmac")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ProductSessionFactory.configure(bind=engine)
    seller_token = create_access_token(31, "seller@example.com", "seller")
    another_seller_token = create_access_token(32, "other@example.com", "seller")
    with TestClient(product_app) as client:
        response = client.post(
            "/products",
            headers={"Authorization": f"Bearer {seller_token}"},
            json={"name": "Handmade mug", "description": "Stoneware", "price": "12.50"},
        )
        assert response.status_code == 201
        product_id = response.json()["id"]

        denied = client.patch(
            f"/products/{product_id}",
            headers={"Authorization": f"Bearer {another_seller_token}"},
            json={"name": "Changed item", "description": "", "price": "13.00"},
        )
        assert denied.status_code == 403
    engine.dispose()


def test_inventory_registration_checks_catalog_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-secret-that-is-long-enough-for-hmac")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    InventorySessionFactory.configure(bind=engine)
    seller_token = create_access_token(41, "seller@example.com", "seller")

    def product_response(*args, **kwargs):
        request = inventory_httpx.Request("GET", "http://localhost/products/17")
        return inventory_httpx.Response(
            200,
            json={"id": 17, "seller_id": 41},
            request=request,
        )

    monkeypatch.setattr("services.inventory_service.main.httpx.get", product_response)
    with TestClient(inventory_app) as client:
        response = client.post(
            "/inventory",
            headers={"Authorization": f"Bearer {seller_token}"},
            json={"product_id": 17, "quantity": 6},
        )
        assert response.status_code == 201
        assert response.json()["quantity"] == 6
    engine.dispose()


def test_inventory_reservation_and_release_are_atomic() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Inventory(product_id=7, seller_id=3, quantity=5))
        db.commit()

        reserved = change_reserved_stock(db, 7, 3, reserve=True)
        assert reserved["quantity"] == 2

        with pytest.raises(HTTPException, match="Insufficient stock"):
            change_reserved_stock(db, 7, 3, reserve=True)

        released = change_reserved_stock(db, 7, 3, reserve=False)
        assert released["quantity"] == 5


def test_order_response_includes_item_total_and_cod() -> None:
    order = Order(
        id=11,
        buyer_id=5,
        buyer_email="buyer@example.com",
        delivery_address="123 Market Street",
        total=Decimal("19.98"),
        status="placed",
        items=[
            OrderItem(
                product_id=8,
                seller_id=3,
                product_name="Handmade mug",
                unit_price=Decimal("9.99"),
                quantity=2,
            )
        ],
    )

    result = order_response(order)

    assert result["payment_method"] == "COD"
    assert result["items"][0]["line_total"] == Decimal("19.98")


def test_order_status_transitions_are_bounded() -> None:
    assert "processing" in ALLOWED_TRANSITIONS["placed"]
    assert "delivered" not in ALLOWED_TRANSITIONS["placed"]
    assert not ALLOWED_TRANSITIONS["delivered"]


def test_order_api_rejects_mixed_sellers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-secret-that-is-long-enough-for-hmac")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    OrderSessionFactory.configure(bind=engine)
    buyer_token = create_access_token(51, "buyer@example.com", "buyer")

    def catalog_response(method, url, **kwargs):
        product_id = int(url.rsplit("/", 1)[-1])
        request = inventory_httpx.Request(method, url)
        return inventory_httpx.Response(
            200,
            json={
                "id": product_id,
                "seller_id": product_id + 100,
                "name": f"Item {product_id}",
                "price": "10.00",
            },
            request=request,
        )

    monkeypatch.setattr(order_service_module, "call_service", catalog_response)
    with TestClient(order_app) as client:
        response = client.post(
            "/orders",
            headers={"Authorization": f"Bearer {buyer_token}"},
            json={
                "delivery_address": "123 Market Street, Boston",
                "items": [
                    {"product_id": 1, "quantity": 1},
                    {"product_id": 2, "quantity": 1},
                ],
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "Each order can contain products from one seller only."
    engine.dispose()


def test_payment_and_notification_internal_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-secret-that-is-long-enough-for-hmac")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "test-internal-token")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    engines = []
    for session_factory in (PaymentSessionFactory, NotificationSessionFactory):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        session_factory.configure(bind=engine)
        engines.append(engine)

    with TestClient(payment_app) as payment_client:
        payment = payment_client.post(
            "/internal/payments",
            headers={"X-Internal-Token": "test-internal-token"},
            json={
                "order_id": 73,
                "buyer_id": 12,
                "buyer_email": "buyer@example.com",
                "amount": "25.00",
                "method": "COD",
            },
        )
        assert payment.status_code == 201
        assert payment.json()["status"] == "pending_collection"
        assert payment.json()["method"] == "COD"

    with TestClient(notification_app) as notification_client:
        notification = notification_client.post(
            "/internal/order-updates",
            headers={"X-Internal-Token": "test-internal-token"},
            json={
                "user_id": 12,
                "email": "buyer@example.com",
                "order_id": 73,
                "status": "placed",
                "message": "Your COD order has been placed.",
            },
        )
        assert notification.status_code == 201
        assert notification.json()["delivery_status"] == "queued"

    for engine in engines:
        engine.dispose()


def test_all_service_health_routes_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-secret-that-is-long-enough-for-hmac")
    services = [
        (UserSessionFactory, user_app),
        (ProductSessionFactory, product_app),
        (InventorySessionFactory, inventory_app),
        (OrderSessionFactory, order_app),
        (PaymentSessionFactory, payment_app),
        (NotificationSessionFactory, notification_app),
    ]

    for session_factory, service_app in services:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        session_factory.configure(bind=engine)
        with TestClient(service_app) as client:
            assert client.get("/health").status_code == 200
        engine.dispose()
