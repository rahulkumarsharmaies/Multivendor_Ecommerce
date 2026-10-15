# Multivendor Ecommerce

A separate marketplace project with an Angular storefront and six Python/FastAPI microservices. The user and order flows use JWT bearer tokens; each service owns its schema and its own PostgreSQL database. Orders support **cash on delivery only**.

## Services

| Service | Port | Own database | Main responsibilities |
|---|---:|---|---|
| User Service | 8001 | `users_db` | Buyer/seller registration, login, profile, JWT issuance |
| Product Service | 8002 | `products_db` | Search, seller-owned listings, descriptions, pricing |
| Inventory Service | 8003 | `inventory_db` | Stock registration, adjustment, availability, atomic reservation |
| Order Service | 8004 | `orders_db` | Checkout, order history, seller fulfilment status, cancellation |
| Payment Service | 8005 | `payments_db` | COD payment records and collection status; no card/online gateway |
| Notification Service | 8006 | `notifications_db` | Order-update inbox and optional SMTP email |
| Angular storefront | 4200 | — | Product browse/search, buyer basket/checkout, seller listing, order activity |

The six databases are separate PostgreSQL databases on the local Compose PostgreSQL instance. Each service connects as its own database role, and cross-service database connections are revoked. The shared Python package contains infrastructure helpers only; service-specific models and data stay inside each service.

## Run the complete stack

Requirements: Docker Desktop with Docker Compose.

```powershell
Copy-Item .env.example .env
# Edit .env and replace JWT_SECRET and INTERNAL_API_TOKEN with long random values.
docker compose up --build
```

Open **http://localhost:4200**. FastAPI OpenAPI/Swagger pages are at `http://localhost:8001/docs` through `http://localhost:8006/docs`. Health checks are available at each service's `/health`.

The example values in `.env.example` are for local development only. Never deploy them. Use an alphanumeric PostgreSQL password in this starter because Compose places it in database connection URLs without URL encoding. Set SMTP variables in `.env` to deliver actual email; without SMTP, notifications remain visible in the in-app order-update inbox with delivery status `queued`.

PostgreSQL creates each service database on first startup. To re-run database initialization after changing the init script, stop Compose and remove its local database volume with `docker compose down -v` (this permanently deletes local development data).

## Features included

- Buyers and sellers register separately. Registration cannot grant admin access.
- JWT access tokens expire after two hours. Passwords are stored as salted PBKDF2-HMAC-SHA256 hashes; protected requests use `Authorization: Bearer <token>`.
- Sellers can create, edit, and remove their own products and register/adjust their stock.
- Checkout reads current catalog prices, atomically reserves stock, creates a COD record, and returns a placed order. Each order belongs to one seller; the storefront splits a mixed basket into separate seller orders. If a downstream step fails, the order flow attempts to release stock and exposes the failure.
- Order status transitions are checked. A seller can update fulfilment only for orders containing solely that seller's products. Delivery records COD collection; cancellation releases reserved inventory.
- Order updates are retained in the notification database. Configure SMTP to send them by email as well.
- Inter-service operations use the shared `INTERNAL_API_TOKEN` header. The browser cannot invoke internal endpoints without it.

## API outline

| Service | Endpoint | Purpose |
|---|---|---|
| User | `POST /auth/register`, `POST /auth/login` | Create an account or receive JWT |
| User | `GET /users/me` | Read authenticated profile |
| Product | `GET /products?search=...` | Search/list catalog |
| Product | `POST /products`, `PATCH /products/{id}`, `DELETE /products/{id}` | Seller listing management |
| Inventory | `POST /inventory`, `GET /inventory/{product_id}`, `PATCH /inventory/{product_id}` | Register, inspect, and add stock |
| Order | `POST /orders`, `GET /orders/me`, `GET /orders/{id}` | Place and review orders |
| Order | `PATCH /orders/{id}/status` | Seller/admin fulfilment; buyer cancellation |
| Payment | `GET /payments/me` | Review COD payment records |
| Notification | `GET /notifications/me` | Review order-update messages |

Request bodies and response models are documented by Swagger at each service's `/docs`.

## Angular-only development server

After installing the frontend dependencies, the Angular dev server proxies `/api/*` to the local service ports:

```powershell
cd frontend
npm ci
npm start
```

For separate local service processes, create the Python environment and install runtime requirements:

```powershell
py -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
```

Then run each service from the project root. In each service terminal, set the shared secrets before running its command:

```powershell
$env:JWT_SECRET = "use-a-random-secret-of-at-least-32-bytes"
$env:INTERNAL_API_TOKEN = "use-a-separate-random-internal-token"
& ".\.venv\Scripts\python.exe" -m uvicorn services.user_service.main:app --port 8001
```

Repeat the command with the matching module/port: `product_service`/8002, `inventory_service`/8003, `order_service`/8004, `payment_service`/8005, and `notification_service`/8006. Each service defaults to its own SQLite file (`users.db`, `products.db`, `inventory.db`, `orders.db`, `payments.db`, or `notifications.db`) when `DATABASE_URL` is omitted. The order service defaults to the local service ports; the inventory service defaults to the local product-service port. Compose configures all settings for the normal full-stack workflow.

## Tests

If you have not created `.venv` yet, create it with `py -m venv .venv`. Install the development test dependency and run:

```powershell
& ".\.venv\Scripts\python.exe" -m pip install -r requirements-dev.txt
& ".\.venv\Scripts\python.exe" -m pytest
```

The tests cover password hashing, JWT identity and profile routes, seller product ownership, inventory registration/reservation, single-seller order constraints, COD payment/notification routes, all six service startup/health checks, and allowed order-state transitions.

## Production notes

This is a runnable starter, not a hardened commerce deployment. Before production, add database migrations, rate limits and abuse controls, a durable outbox/retry worker for inter-service events, secret management, TLS, structured observability, and appropriate account verification/recovery. SMTP delivery is synchronous for this starter. No real payment processing or online payment credentials are implemented.
