from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import String, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from shared.common.app import create_service_app
from shared.common.database import Base, get_db, make_session_factory
from shared.common.security import current_user, create_access_token, hash_password, verify_password

SessionFactory = make_session_factory("users")
app = create_service_app("User Service", SessionFactory)
DbSession = Annotated[Session, Depends(get_db(SessionFactory))]


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))


class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    role: Literal["buyer", "seller"] = "buyer"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


def user_response(user: User) -> dict:
    return {
        "id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "role": user.role,
        "created_at": user.created_at,
    }


@app.post("/auth/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: DbSession) -> dict:
    email = str(payload.email).lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    user = User(
        full_name=payload.full_name.strip(),
        email=email,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists.",
        ) from None
    db.refresh(user)
    return user_response(user)


@app.post("/auth/login")
def login(payload: LoginRequest, db: DbSession) -> dict:
    user = db.scalar(select(User).where(User.email == str(payload.email).lower()))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    return {
        "access_token": create_access_token(user.id, user.email, user.role),
        "token_type": "bearer",
        "user": user_response(user),
    }


@app.get("/users/me")
def profile(
    db: DbSession,
    user: Annotated[dict, Depends(current_user)],
) -> dict:
    account = db.get(User, user["id"])
    if account is None:
        raise HTTPException(status_code=404, detail="User account was not found.")
    return user_response(account)
