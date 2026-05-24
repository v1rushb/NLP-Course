import os
import time

import jwt
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from db.database import get_db
from db.models import User


ALGORITHM = "HS256"
ACCESS_TOKEN_TTL_SECONDS = int(os.getenv("ACCESS_TOKEN_TTL_SECONDS", "86400"))


def _secret() -> str:
    return os.getenv("JWT_SECRET") or "ppu-local-development-secret"


def create_access_token(user: User) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL_SECONDS,
    }
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM)


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Authentication is required.")

    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(token, _secret(), algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
    except Exception:
        raise HTTPException(401, "Invalid or expired session.")

    user = db.get(User, user_id)
    if not user:
        raise HTTPException(401, "Invalid or expired session.")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Admin access is required.")
    return user
