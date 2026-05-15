"""
Auth router — login, current user, user management (admin only).
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from pymongo.database import Database

from ..auth_deps import (
    create_access_token,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)
from ..db import get_db, COLL_USERS, next_id
from ..models import User

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Schemas ────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "viewer"
    full_access: bool = False


class UpdateUserRequest(BaseModel):
    role: str | None = None
    full_access: bool | None = None
    is_active: bool | None = None
    password: str | None = None


# ── Public ─────────────────────────────────────────────────────────────────────

@router.post("/login")
def login(body: LoginRequest, db: Database = Depends(get_db)):
    doc = db[COLL_USERS].find_one({"username": body.username})
    user = User.from_doc(doc) if doc else None
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")
    token = create_access_token(user.id, user.username, user.role, user.full_access)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "full_access": user.full_access,
        },
    }


# ── Authenticated ─────────────────────────────────────────────────────────────

@router.get("/me")
def get_me(user: User = Depends(get_current_user)):
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "full_access": user.full_access,
        "is_active": user.is_active,
        "created_at": user.created_at,
    }


# ── Admin only ────────────────────────────────────────────────────────────────

@router.get("/users")
def list_users(admin: User = Depends(require_admin), db: Database = Depends(get_db)):
    docs = db[COLL_USERS].find().sort("created_at", 1)
    return [
        {
            "id": str(d["_id"]),
            "username": d["username"],
            "role": d.get("role", "viewer"),
            "full_access": d.get("full_access", False),
            "is_active": d.get("is_active", True),
            "created_at": d.get("created_at"),
        }
        for d in docs
    ]


@router.post("/users")
def create_user(
    body: CreateUserRequest,
    admin: User = Depends(require_admin),
    db: Database = Depends(get_db),
):
    if db[COLL_USERS].find_one({"username": body.username}):
        raise HTTPException(status_code=400, detail="Username already exists")
    if body.role not in ("admin", "viewer"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'viewer'")
    doc = {
        "_id": next_id(db, COLL_USERS),
        "username": body.username,
        "password_hash": hash_password(body.password),
        "role": body.role,
        "full_access": body.full_access,
        "is_active": True,
        "created_at": datetime.utcnow(),
    }
    db[COLL_USERS].insert_one(doc)
    return {
        "id": str(doc["_id"]),
        "username": doc["username"],
        "role": doc["role"],
        "full_access": doc["full_access"],
        "is_active": doc["is_active"],
    }


@router.put("/users/{user_id}")
def update_user(
    user_id: int,
    body: UpdateUserRequest,
    admin: User = Depends(require_admin),
    db: Database = Depends(get_db),
):
    doc = db[COLL_USERS].find_one({"_id": user_id})
    if not doc:
        raise HTTPException(status_code=404, detail="User not found")
    update: dict = {}
    if body.role is not None:
        if body.role not in ("admin", "viewer"):
            raise HTTPException(status_code=400, detail="Role must be 'admin' or 'viewer'")
        update["role"] = body.role
    if body.full_access is not None:
        update["full_access"] = body.full_access
    if body.is_active is not None:
        update["is_active"] = body.is_active
    if body.password:
        update["password_hash"] = hash_password(body.password)
    if update:
        doc = db[COLL_USERS].find_one_and_update(
            {"_id": user_id}, {"$set": update}, return_document=True
        )
    return {
        "id": str(doc["_id"]),
        "username": doc["username"],
        "role": doc.get("role", "viewer"),
        "full_access": doc.get("full_access", False),
        "is_active": doc.get("is_active", True),
    }


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Database = Depends(get_db),
):
    doc = db[COLL_USERS].find_one({"_id": user_id})
    if not doc:
        raise HTTPException(status_code=404, detail="User not found")
    if str(doc["_id"]) == str(admin.id):
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    db[COLL_USERS].delete_one({"_id": user_id})
    return {"deleted": True, "id": user_id}