"""AssetFlow HTTP Server.

This file serves static frontend files and delegates ALL API calls to the
dedicated API handlers registered in python_deps/api/*.py via the router.
main.py does NOT handle or route any APIs directly; all routes are managed
in their dedicated files in python_deps/api/.
"""
from __future__ import annotations

import json
import mimetypes
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

ROOT = Path(__file__).resolve().parent.parent / "fixed_asset_frontend"
HOST = os.environ.get("ASSETFLOW_HOST", "127.0.0.1")
PORT = int(os.environ.get("ASSETFLOW_PORT", "8000"))


class Handler(BaseHTTPRequestHandler):
    server_version = "AssetFlowServer/2.0"

    def log_message(self, fmt, *args):
        # Clean request logging
        print(f"[{self.command}] {urlsplit(self.path).path} {args[1] if len(args) > 1 else ''}")

    def send_cors_headers(self):
        origin = self.headers.get("Origin", "*")
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, Cookie")
        self.send_header("Access-Control-Allow-Credentials", "true")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors_headers()
        self.end_headers()

    def send_json(self, status: int, body: dict, cookie: str | None = None, clear_cookie: bool = False):
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.send_cors_headers()
        if cookie:
            self.send_header("Set-Cookie", f"{SESSION_COOKIE}={cookie}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_SECONDS}")
        if clear_cookie:
            self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")
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

    # ── Method Handlers: Delegate ALL API routing to dedicated api/*.py files ──

    def do_POST(self):
        route = urlsplit(self.path).path
        if not router.dispatch("POST", route, self):
            self.send_json(404, {"error": "Unknown API endpoint."})

    def do_GET(self):
        route = urlsplit(self.path).path
        # If matched by an API route handler in api/*.py, dispatch handled it
        if router.dispatch("GET", route, self):
            return
        # Otherwise, serve static frontend assets
        self.serve_static(route)

    def do_DELETE(self):
        route = urlsplit(self.path).path
        if not router.dispatch("DELETE", route, self):
            self.send_json(404, {"error": "Unknown API endpoint."})

    def serve_static(self, route: str):
        relative = "index.html" if route == "/" else route.lstrip("/")
        target = (ROOT / relative).resolve()
        allowed_extensions = {".html", ".css", ".js", ".png", ".jpg", ".jpeg", ".svg", ".ico", ".webp"}
        if ROOT not in target.parents or target.suffix.lower() not in allowed_extensions or "python_deps" in target.parts or not target.is_file():
            self.send_error(404)
            return
        content = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(content)


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
        raise SystemExit(f"Could not connect to {DB_CONFIG['database']}. Check MySQL settings: {error}")

    print(f"\n=======================================================")
    print(f" AssetFlow Server is running live!")
    print(f" URL: http://{HOST}:{PORT}")
    print(f" API Routing: Handled by dedicated modules in python_deps/api/")
    print(f" Total Registered Endpoints: {len(router._routes)} routes, {len(router._prefix_routes)} prefix routes")
    print(f" Frontend: Serving files from {ROOT}")
    print(f" Entrypoint: main.py")
    print(f"=======================================================\n")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    start_server()
