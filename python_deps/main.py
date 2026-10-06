"""AssetFlow Backend API HTTP Server.

This server is a pure backend API server. It does not serve frontend files.
All API requests are handled by dedicated modules in python_deps/api/*.py
via the route registry in router.py.
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

# Ensure python_deps directory is in sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import mysql.connector
from db import (
    DB_CONFIG,
    db_connection,
    ensure_assigned_product_schema,
    ensure_employee_schema,
    ensure_maintenance_schema,
    ensure_overall_place_schema,
)
from api.auth import (
    SESSION_COOKIE,
    SESSION_SECONDS,
    get_current_user,
    get_session_id,
    require_asset_user,
)

# Import router and api package — this auto-registers all endpoint handlers from dedicated api/*.py files
from router import router
import api

# Server Host & Port (0.0.0.0 binds to all interfaces for live cloud / Docker / local hosting)
HOST = os.environ.get("HOST", os.environ.get("ASSETFLOW_HOST", "0.0.0.0"))
PORT = int(os.environ.get("PORT", os.environ.get("ASSETFLOW_PORT", "8000")))


class Handler(BaseHTTPRequestHandler):
    server_version = "AssetFlowAPIServer/2.0"

    def log_message(self, fmt, *args):
        # Clean request logging
        print(f"[{self.command}] {urlsplit(self.path).path} {args[1] if len(args) > 1 else ''}")

    def send_cors_headers(self):
        origin = self.headers.get("Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        else:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS, PUT, PATCH")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, Cookie, X-Requested-With, Accept, Origin")
        self.send_header("Access-Control-Allow-Credentials", "true")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors_headers()
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def send_json(self, status: int, body: dict, cookie: str | None = None, clear_cookie: bool = False):
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.send_cors_headers()

        origin = self.headers.get("Origin", "")
        is_https = (
            self.headers.get("X-Forwarded-Proto", "").lower() == "https"
            or origin.startswith("https://")
            or os.environ.get("COOKIE_SECURE", "false").lower() in ("true", "1", "yes")
        )
        same_site = "SameSite=None; Secure" if is_https else "SameSite=Lax"

        if cookie:
            self.send_header("Set-Cookie", f"{SESSION_COOKIE}={cookie}; Path=/; HttpOnly; {same_site}; Max-Age={SESSION_SECONDS}")
        if clear_cookie:
            self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; Path=/; HttpOnly; {same_site}; Max-Age=0")
        self.end_headers()
        self.wfile.write(payload)

    def read_json(self) -> dict:
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 16_384)
            data = json.loads(self.rfile.read(length) or b"{}")
            return data if isinstance(data, dict) else {}
        except (ValueError, json.JSONDecodeError):
            return {}

    def session_id(self) -> str | None:
        return get_session_id(self)

    def current_user(self):
        return get_current_user(self)

    def require_asset_user(self):
        return require_asset_user(self)

    @staticmethod
    def asset_ids(data):
        ids = data.get("assetIds")
        if not isinstance(ids, list):
            ids = [data.get("assetId", "")]
        return list(dict.fromkeys(str(value).strip() for value in ids if str(value).strip()))

    # ── Method Handlers: Pure API dispatch ────────────────────────────────────

    def do_POST(self):
        route = urlsplit(self.path).path
        if not router.dispatch("POST", route, self):
            self.send_json(404, {"error": "Unknown API endpoint.", "path": route})

    def do_GET(self):
        route = urlsplit(self.path).path

        # Root and health check endpoints for cloud/live hosting monitoring
        if route in ("/", "/health", "/api/health"):
            return self.send_json(200, {
                "status": "online",
                "service": "AssetFlow Backend API",
                "version": "2.0",
                "message": "AssetFlow API server is live and operational.",
                "endpoints": "/api/*"
            })

        # Dispatch API route to dedicated api/*.py module
        if router.dispatch("GET", route, self):
            return

        self.send_json(404, {"error": "API route not found.", "path": route})

    def do_DELETE(self):
        route = urlsplit(self.path).path
        if not router.dispatch("DELETE", route, self):
            self.send_json(404, {"error": "Unknown API endpoint.", "path": route})


def start_server():
    try:
        connection = db_connection()
        try:
            cursor = connection.cursor(dictionary=True)
            ensure_employee_schema(cursor)
            ensure_overall_place_schema(cursor)
            ensure_assigned_product_schema(cursor)
            ensure_maintenance_schema(cursor)
            connection.commit()
        finally:
            connection.close()
    except mysql.connector.Error as error:
        print(f"[Warning] Could not connect to {DB_CONFIG['database']} at startup: {error}")
        print("Backend server will continue running and retry connection on incoming requests.")

    print(f"\n=======================================================")
    print(f" AssetFlow Backend API Server is running live!")
    print(f" Bind Address: {HOST}:{PORT}")
    print(f" API Routing: Handled by dedicated modules in python_deps/api/")
    print(f" Total Registered Endpoints: {len(router._routes)} routes, {len(router._prefix_routes)} prefix routes")
    print(f" Mode: Pure Decoupled Backend API Server")
    print(f" Health Check: http://{HOST}:{PORT}/health")
    print(f"=======================================================\n")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    start_server()
