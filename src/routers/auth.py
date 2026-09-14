import uuid
from datetime import datetime, timedelta

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from jose import jwt
from pydantic import BaseModel
from sqlalchemy import Boolean, Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID

from src.auth import ALGORITHM, require_role
from src.config import ADMIN_PASSWORD, ADMIN_USERNAME, JWT_EXPIRE_MINUTES, JWT_SECRET
from src.database import Base, SessionLocal
from src.models import TokenResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _hash_password(password: str) -> str:
	return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, hashed_password: str) -> bool:
	return bcrypt.checkpw(password.encode("utf-8"), hashed_password.encode("utf-8"))


class User(Base):
	__tablename__ = "users"

	id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
	username = Column(String, unique=True, nullable=False, index=True)
	hashed_password = Column(String, nullable=False)
	role = Column(String, nullable=False, default="user")
	is_active = Column(Boolean, nullable=False, default=True)
	created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class LoginRequest(BaseModel):
	username: str
	password: str


class UserCreateRequest(BaseModel):
	username: str
	password: str
	role: str = "user"


def _create_token(user: User) -> str:
	expires = datetime.utcnow() + timedelta(minutes=JWT_EXPIRE_MINUTES)
	return jwt.encode(
		{"sub": user.username, "role": user.role, "exp": expires},
		JWT_SECRET,
		algorithm=ALGORITHM,
	)


def _user_dict(user: User) -> dict:
	return {
		"id": str(user.id),
		"username": user.username,
		"role": user.role,
		"is_active": user.is_active,
		"created_at": user.created_at.isoformat(),
	}


def ensure_admin_user() -> None:
	if not ADMIN_PASSWORD:
		raise RuntimeError("ADMIN_PASSWORD is required to bootstrap the admin user")
	db = SessionLocal()
	try:
		if db.query(User).filter(User.username == ADMIN_USERNAME).first() is None:
			db.add(User(
				username=ADMIN_USERNAME,
				hashed_password=_hash_password(ADMIN_PASSWORD),
				role="admin",
			))
			db.commit()
	except Exception:
		db.rollback()
		raise
	finally:
		db.close()


@router.post("/login", response_model=TokenResponse)
def login(request: LoginRequest):
	db = SessionLocal()
	try:
		user = db.query(User).filter(User.username == request.username).first()
		if user is None or not user.is_active or not _verify_password(
			request.password, user.hashed_password
		):
			raise HTTPException(
				status_code=status.HTTP_401_UNAUTHORIZED,
				detail="Invalid username or password",
			)
		return {"access_token": _create_token(user), "token_type": "bearer"}
	finally:
		db.close()


@router.post("/users", status_code=status.HTTP_201_CREATED, response_model=UserResponse)
def create_user(
	request: UserCreateRequest,
	_admin=Depends(require_role("admin")),
):
	if request.role not in {"admin", "user"}:
		raise HTTPException(status_code=400, detail="Role must be 'admin' or 'user'")

	db = SessionLocal()
	try:
		if db.query(User).filter(User.username == request.username).first():
			raise HTTPException(status_code=409, detail="Username already exists")
		user = User(
			username=request.username,
			hashed_password=_hash_password(request.password),
			role=request.role,
		)
		db.add(user)
		db.commit()
		db.refresh(user)
		return _user_dict(user)
	except HTTPException:
		db.rollback()
		raise
	except Exception:
		db.rollback()
		raise
	finally:
		db.close()