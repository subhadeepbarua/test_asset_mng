"""Authentication and Session API for AssetFlow.

Dedicated module registering all authentication routes:
  POST /api/auth/login,    /api/login
  POST /api/auth/signup,   /api/signup
  POST /api/auth/logout,   /api/logout
  GET  /api/auth/me,       /api/me
  POST /api/auth/profile,  /api/profile
  POST /api/auth/admins,   /api/admins

Provides session management, password hashing/verification, and user resolution.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sys
import threading
import time
from http.cookies import SimpleCookie
from pathlib import Path

import mysql.connector

_deps_dir = str(Path(__file__).resolve().parent.parent)
if _deps_dir not in sys.path:
    sys.path.insert(0, _deps_dir)

from router import router
from db import db_connection

SESSION_COOKIE = "assetflow_session"
SESSION_SECONDS = 8 * 60 * 60
PBKDF2_ROUNDS = 600_000
SESSIONS: dict[str, tuple[str, float]] = {}
SESSION_LOCK = threading.Lock()


# ── Password & Session Utilities ──────────────────────────────────────────────

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(actual.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def public_user(row: dict) -> dict:
    return {
        "userId": row["user_id"],
        "name": row["user_name"],
        "email": row["user_mail"],
        "role": row["designation"]
    }


def get_session_id(handler) -> str | None:
    cookie = SimpleCookie()
    cookie.load(handler.headers.get("Cookie", ""))
    morsel = cookie.get(SESSION_COOKIE)
    return morsel.value if morsel else None


def get_current_user(handler) -> dict | None:
    sid = get_session_id(handler)
    if not sid:
        return None
    with SESSION_LOCK:
        session = SESSIONS.get(sid)
        if not session:
            return None
        user_id, expiry = session
        if expiry < time.time():
            SESSIONS.pop(sid, None)
            return None
    connection = db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT user_id, user_name, user_mail, designation FROM user_login WHERE user_id = %s AND is_active = 1 LIMIT 1",
            (user_id,)
        )
        return cursor.fetchone()
    finally:
        connection.close()


def require_asset_user(handler) -> dict | None:
    user = get_current_user(handler)
    if not user:
        handler.send_json(401, {"error": "Sign in required."})
        return None
    if user.get("designation") not in ("user", "admin"):
        handler.send_json(403, {"error": "Account cannot manage assets."})
        return None
    return user


# ── Route Handlers ────────────────────────────────────────────────────────────

# ── POST /api/auth/login & /api/login ─────────────────────────────────────────

@router.post("/api/auth/login", "/api/login")
def handle_login(handler):
    """Authenticate user and issue session cookie."""
    data = handler.read_json()
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", "")).strip()

    if not email or not password:
        return handler.send_json(400, {"error": "Email and password are required."})

    connection = db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT user_id, user_name, user_mail, password_hash, designation, is_active "
            "FROM user_login WHERE LOWER(user_mail) = %s LIMIT 1",
            (email,)
        )
        user = cursor.fetchone()
    finally:
        connection.close()

    if not user or not user.get("is_active"):
        return handler.send_json(401, {"error": "Invalid email or password."})

    if not verify_password(password, user["password_hash"]):
        return handler.send_json(401, {"error": "Invalid email or password."})

    session_id = secrets.token_hex(24)
    with SESSION_LOCK:
        SESSIONS[session_id] = (user["user_id"], time.time() + SESSION_SECONDS)

    return handler.send_json(200, {"user": public_user(user)}, cookie=session_id)


# ── POST /api/auth/signup & /api/signup ───────────────────────────────────────

@router.post("/api/auth/signup", "/api/signup")
def handle_signup(handler):
    """Register a new standard user account."""
    data = handler.read_json()
    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", "")).strip()

    if not name or not email or not password:
        return handler.send_json(400, {"error": "Name, email, and password are required."})

    if "@" not in email or "." not in email:
        return handler.send_json(400, {"error": "Please enter a valid email address."})

    if len(password) < 4:
        return handler.send_json(400, {"error": "Password must be at least 4 characters."})

    user_id = f"USR-{secrets.token_hex(16)}"
    hashed = hash_password(password)

    connection = db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT user_id FROM user_login WHERE LOWER(user_mail) = %s LIMIT 1", (email,))
        if cursor.fetchone():
            return handler.send_json(409, {"error": "An account with this email already exists."})

        cursor.execute(
            "INSERT INTO user_login (user_id, user_name, user_mail, password_hash, designation, is_active) "
            "VALUES (%s, %s, %s, %s, 'user', 1)",
            (user_id, name, email, hashed)
        )
        connection.commit()
    finally:
        connection.close()

    session_id = secrets.token_hex(24)
    with SESSION_LOCK:
        SESSIONS[session_id] = (user_id, time.time() + SESSION_SECONDS)

    new_user = {
        "user_id": user_id,
        "user_name": name,
        "user_mail": email,
        "designation": "user"
    }
    return handler.send_json(201, {"user": public_user(new_user)}, cookie=session_id)


# ── POST /api/auth/logout & /api/logout ───────────────────────────────────────

@router.post("/api/auth/logout", "/api/logout")
def handle_logout(handler):
    """Log out the current user and destroy the session."""
    sid = get_session_id(handler)
    if sid:
        with SESSION_LOCK:
            SESSIONS.pop(sid, None)
    return handler.send_json(200, {"message": "Signed out"}, clear_cookie=True)


# ── GET /api/auth/me & /api/me ───────────────────────────────────────────────

@router.get("/api/auth/me", "/api/me")
def handle_me(handler):
    """Return the profile of the currently signed-in user."""
    user = get_current_user(handler)
    if not user:
        return handler.send_json(401, {"error": "Sign in required."})
    return handler.send_json(200, {"user": public_user(user)})


# ── POST /api/auth/profile & /api/profile ─────────────────────────────────────

@router.post("/api/auth/profile", "/api/profile")
def handle_profile(handler):
    """Update profile information for the signed-in user."""
    user = get_current_user(handler)
    if not user:
        return handler.send_json(401, {"error": "Sign in required."})

    data = handler.read_json()
    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip().lower()

    if not name or not email:
        return handler.send_json(400, {"error": "Name and email are required."})

    if "@" not in email or "." not in email:
        return handler.send_json(400, {"error": "Please enter a valid email address."})

    connection = db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT user_id FROM user_login WHERE LOWER(user_mail) = %s AND user_id != %s LIMIT 1",
            (email, user["user_id"])
        )
        if cursor.fetchone():
            return handler.send_json(409, {"error": "That email address is already used by another account."})

        cursor.execute(
            "UPDATE user_login SET user_name = %s, user_mail = %s, is_update = 1 WHERE user_id = %s",
            (name, email, user["user_id"])
        )
        connection.commit()
    finally:
        connection.close()

    updated = dict(user)
    updated["user_name"] = name
    updated["user_mail"] = email
    return handler.send_json(200, {"user": public_user(updated)})


# ── POST /api/auth/admins & /api/admins ───────────────────────────────────────

@router.post("/api/auth/admins", "/api/admins")
def handle_admins(handler):
    """Create a new administrator account (admin-only)."""
    acting_admin = get_current_user(handler)
    if not acting_admin or acting_admin.get("designation") != "admin":
        return handler.send_json(403, {"error": "Only an administrator can create administrator accounts."})

    data = handler.read_json()
    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", "")).strip()

    if not name or not email or not password:
        return handler.send_json(400, {"error": "Name, email, and password are required."})

    if "@" not in email or "." not in email:
        return handler.send_json(400, {"error": "Please enter a valid email address."})

    if len(password) < 4:
        return handler.send_json(400, {"error": "Password must be at least 4 characters."})

    admin_id = f"ADM-{secrets.token_hex(16)}"
    hashed = hash_password(password)

    connection = db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT user_id FROM user_login WHERE LOWER(user_mail) = %s LIMIT 1", (email,))
        if cursor.fetchone():
            return handler.send_json(409, {"error": "An account with this email already exists."})

        cursor.execute(
            "INSERT INTO user_login (user_id, user_name, user_mail, password_hash, designation, is_active) "
            "VALUES (%s, %s, %s, %s, 'admin', 1)",
            (admin_id, name, email, hashed)
        )
        connection.commit()
    finally:
        connection.close()

    new_admin = {
        "user_id": admin_id,
        "user_name": name,
        "user_mail": email,
        "designation": "admin"
    }
    return handler.send_json(201, {"user": public_user(new_admin)})
