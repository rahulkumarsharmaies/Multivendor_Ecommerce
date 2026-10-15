import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

import jwt
from fastapi import Depends, Header, HTTPException, status

ALGORITHM = "HS256"
PASSWORD_ITERATIONS = 600_000


def _jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET", "")
    if len(secret.encode()) < 32:
        raise RuntimeError("JWT_SECRET must contain at least 32 bytes.")
    return secret


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return f"{PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        iterations_text, salt_text, digest_text = encoded.split("$")
        iterations = int(iterations_text)
        if iterations < 100_000 or iterations > 2_000_000:
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_text), iterations
        )
        return hmac.compare_digest(candidate.hex(), digest_text)
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: int, email: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=2),
    }
    return jwt.encode(claims, _jwt_secret(), algorithm=ALGORITHM)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Valid bearer token required.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def current_user(authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise _unauthorized()
    token = authorization[7:].strip()
    if not token:
        raise _unauthorized()
    try:
        claims = jwt.decode(token, _jwt_secret(), algorithms=[ALGORITHM])
        user_id = int(claims["sub"])
        role = claims["role"]
        if role not in {"buyer", "seller", "admin"}:
            raise _unauthorized()
        return {"id": user_id, "email": claims["email"], "role": role}
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
        raise _unauthorized() from None


def require_roles(*roles: str):
    def dependency(user: Annotated[dict[str, Any], Depends(current_user)]) -> dict[str, Any]:
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions.")
        return user

    return dependency


def require_internal_token(
    supplied: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
) -> None:
    expected = os.getenv("INTERNAL_API_TOKEN", "")
    if not expected:
        raise RuntimeError("INTERNAL_API_TOKEN must be configured for internal APIs.")
    if supplied is None or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="Invalid internal service token.")
