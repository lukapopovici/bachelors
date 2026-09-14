from typing import Optional
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from src.config import ADMIN_USERNAME, API_SECRET, JWT_SECRET

security = HTTPBearer(auto_error=False)
ALGORITHM = "HS256"


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
):
    if credentials is None:
        raise HTTPException(status_code=401, detail="Invalid or missing token")

    if credentials.credentials == API_SECRET and API_SECRET:
        from src.routers.auth import User
        from src.database import SessionLocal

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == ADMIN_USERNAME).first()
            if user is None or not user.is_active:
                raise HTTPException(status_code=401, detail="Invalid or inactive user")
            return user
        finally:
            db.close()

    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise JWTError
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    from src.routers.auth import User
    from src.database import SessionLocal

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user is None or not user.is_active:
            raise HTTPException(status_code=401, detail="Invalid or inactive user")
        return user
    finally:
        db.close()


def require_role(role: str):
    def role_dependency(current_user=Depends(get_current_user)):
        if current_user.role != role:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return current_user

    return role_dependency
