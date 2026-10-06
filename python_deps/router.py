"""
Route registry for AssetFlow API.

Each dedicated file in python_deps/api/ registers its own routes using @router.get / @router.post / @router.delete
main.py does NOT define any routes — it only runs the HTTP server and delegates dispatch to this router.
"""
from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from typing import Callable
import mysql.connector
from mysql.connector import IntegrityError


class Router:
    """URL route registry with method-aware dispatch and centralized error handling."""

    def __init__(self):
        self._routes: dict[tuple[str, str], Callable] = {}
        self._prefix_routes: dict[tuple[str, str], Callable] = {}

    # ── Decorators ────────────────────────────────────────────────────────────

    def get(self, *paths: str):
        """Register a GET handler for one or more paths."""
        def decorator(func: Callable) -> Callable:
            for path in paths:
                self._routes[("GET", path)] = func
            return func
        return decorator

    def post(self, *paths: str):
        """Register a POST handler for one or more paths."""
        def decorator(func: Callable) -> Callable:
            for path in paths:
                self._routes[("POST", path)] = func
            return func
        return decorator

    def delete(self, *paths: str):
        """Register a DELETE handler for one or more paths."""
        def decorator(func: Callable) -> Callable:
            for path in paths:
                self._routes[("DELETE", path)] = func
            return func
        return decorator

    def delete_prefix(self, prefix: str):
        """Register a DELETE handler for all paths starting with prefix."""
        def decorator(func: Callable) -> Callable:
            self._prefix_routes[("DELETE", prefix)] = func
            return func
        return decorator

    # ── Dispatch ──────────────────────────────────────────────────────────────

    def dispatch(self, method: str, path: str, handler) -> bool:
        """
        Dispatch the request to the matching API module handler.
        Includes database error handling.
        Returns True if a handler was matched and invoked, False otherwise.
        """
        matched_func = None
        matched_arg = None

        # 1. Exact match
        if (method, path) in self._routes:
            matched_func = self._routes[(method, path)]
        else:
            # 2. Prefix match (e.g. DELETE /api/arrivals/<id>)
            for (reg_method, prefix), func in self._prefix_routes.items():
                if reg_method == method and path.startswith(prefix):
                    matched_func = func
                    matched_arg = path[len(prefix):]
                    break

        if not matched_func:
            return False

        module_name = matched_func.__module__
        func_name = matched_func.__name__
        print(f"[{method}] {path} -> {module_name}.{func_name}")

        try:
            if matched_arg is not None:
                matched_func(handler, matched_arg)
            else:
                matched_func(handler)
        except IntegrityError as err:
            print(f"IntegrityError on {path}: {err}")
            if path in {"/api/auth/signup", "/api/signup", "/api/auth/admins", "/api/admins"}:
                message = "An account with this email already exists."
            elif path in {"/api/auth/profile", "/api/profile"}:
                message = "That email address is already used by another account."
            elif path == "/api/warehouses":
                message = "A warehouse with this name already exists."
            elif path == "/api/vendors":
                message = "This vendor name or ID already exists."
            elif path == "/api/employees":
                message = "An employee ID already exists in your employee list."
            elif path == "/api/assignments":
                message = "This product assignment conflicts with an existing database record. Reload and try again."
            elif path in {
                "/api/assets/basic",
                "/api/assets/purchase",
                "/api/assets/invoice",
                "/api/assets/invoice/update",
                "/api/assets/lifecycle",
                "/api/assets/vendor",
                "/api/vendors/assign",
            }:
                message = "A generated asset or purchase record conflicts with an existing database record. Reload the form and try again."
            else:
                message = "This record already exists."
            handler.send_json(409, {"error": message})
        except mysql.connector.Error as error:
            print(f"Database error on {path}: {error}")
            if path == "/api/assignments":
                error_message = "Could not save product assignment to MySQL. Check database connection."
            elif path in {"/api/maintenance", "/api/maintenance/update"}:
                error_message = "Could not save maintenance entries to MySQL. Check database connection."
            elif path in {
                "/api/assets/basic",
                "/api/assets/purchase",
                "/api/assets/invoice",
                "/api/assets/invoice/update",
                "/api/assets/lifecycle",
                "/api/assets/vendor",
                "/api/vendors",
                "/api/vendors/assign",
                "/api/arrivals",
                "/api/warehouses",
                "/api/employees",
            }:
                error_message = "Could not save asset details to MySQL. Check database connection."
            else:
                error_message = "Database service error. Check database connection."
            handler.send_json(500, {"error": error_message})
        except Exception as error:
            print(f"Server exception on {path}: {error}")
            handler.send_json(500, {"error": "Server error processing request."})

        return True


# Singleton router instance — imported by every api/*.py file
router = Router()


def load_all_api_routes():
    """Dynamically import all modules in python_deps/api to register their routes."""
    import api
    package_dir = Path(__file__).resolve().parent / "api"
    for _, module_name, is_pkg in pkgutil.iter_modules([str(package_dir)]):
        if not is_pkg and not module_name.startswith("_"):
            importlib.import_module(f"api.{module_name}")
    print(f"Loaded API routes: {len(router._routes)} exact endpoints, {len(router._prefix_routes)} prefix patterns.")
