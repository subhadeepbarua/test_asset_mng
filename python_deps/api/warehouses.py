"""
Warehouse API — /api/warehouses

This file owns and registers all its own routes directly.
backend.py does NOT route warehouses — this file does it via @router decorators.

Routes registered here:
  GET  /api/warehouses
  POST /api/warehouses
"""
from __future__ import annotations

import sys
from pathlib import Path

import mysql.connector

_deps_dir = str(Path(__file__).resolve().parent.parent)
if _deps_dir not in sys.path:
    sys.path.insert(0, _deps_dir)

from router import router
from db import (
    db_connection,
    ensure_warehouse_schema,
    warehouse_identifier_column,
    warehouse_records,
)


# ── GET /api/warehouses ───────────────────────────────────────────────────────

@router.get("/api/warehouses")
def handle_warehouses_get(handler):
    """Return all active warehouses."""
    user = handler.require_asset_user()
    if not user:
        return
    try:
        connection = db_connection()
        try:
            cursor = connection.cursor(dictionary=True)
            ensure_warehouse_schema(cursor)
            return handler.send_json(200, {"warehouses": warehouse_records(cursor)})
        finally:
            connection.close()
    except mysql.connector.Error as error:
        print(f"[api/warehouses] Database error: {error}")
        return handler.send_json(500, {"error": "Could not load warehouses from MySQL."})


# ── POST /api/warehouses ──────────────────────────────────────────────────────

@router.post("/api/warehouses")
def handle_warehouses_post(handler):
    """Create a new warehouse (admin only)."""
    acting_admin = handler.current_user()
    if not acting_admin or acting_admin.get("designation") != "admin":
        return handler.send_json(403, {"error": "Only an administrator can create warehouses."})
    data = handler.read_json()
    fields = {
        "warehouse_name": str(data.get("warehouseName", "")).strip(),
        "address": str(data.get("address", "")).strip(),
        "pincode": str(data.get("pincode", "")).strip(),
        "city": str(data.get("city", "")).strip(),
        "district": str(data.get("district", "")).strip(),
        "country": str(data.get("country", "")).strip(),
        "state": str(data.get("state", "")).strip(),
    }
    limits = {"warehouse_name": 150, "pincode": 20, "city": 120, "district": 120, "country": 120, "state": 120}
    if any(not value for value in fields.values()) or any(len(fields[key]) > limit for key, limit in limits.items()):
        return handler.send_json(422, {"error": "Enter the warehouse name, address, pincode, city, district, state, and country."})
    connection = db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        ensure_warehouse_schema(cursor)
        warehouse_key = warehouse_identifier_column(cursor)
        cursor.execute("SHOW COLUMNS FROM warehouse")
        warehouse_column_rows = cursor.fetchall()
        warehouse_columns = {row["Field"] for row in warehouse_column_rows}
        insert_columns = ["warehouse_name", "address", "pincode", "city", "district", "country", "state", "user_id"]
        insert_values = [
            fields["warehouse_name"], fields["address"], fields["pincode"],
            fields["city"], fields["district"], fields["country"], fields["state"],
            acting_admin["user_id"],
        ]
        if "created_by" in warehouse_columns:
            created_by_type = next((str(row["Type"]).lower() for row in warehouse_column_rows if row["Field"] == "created_by"), "")
            creator_value = acting_admin["user_id"]
            if "int" in created_by_type:
                cursor.execute("SELECT sl_no FROM user_login WHERE user_id=%s LIMIT 1", (acting_admin["user_id"],))
                creator = cursor.fetchone()
                if not creator:
                    return handler.send_json(401, {"error": "The signed-in administrator record could not be matched in MySQL. Sign in again."})
                creator_value = creator["sl_no"]
            insert_columns.append("created_by")
            insert_values.append(creator_value)
        cursor.execute(
            f"INSERT INTO warehouse ({','.join(insert_columns)},is_active) VALUES ({','.join(['%s']*len(insert_values))},1)",
            tuple(insert_values),
        )
        warehouse_id = cursor.lastrowid
        connection.commit()
        cursor.execute(
            f"SELECT {warehouse_key} AS warehouse_id,warehouse_name,address,pincode,city,district,country,state,user_id,created_at FROM warehouse WHERE {warehouse_key}=%s",
            (warehouse_id,),
        )
        created = cursor.fetchone()
        warehouse = {
            "warehouseId": created["warehouse_id"],
            "warehouseName": created["warehouse_name"],
            "address": created["address"],
            "pincode": created["pincode"],
            "city": created["city"],
            "district": created["district"],
            "country": created["country"],
            "state": created["state"],
            "userId": created["user_id"],
            "createdAt": str(created["created_at"] or ""),
        }
    finally:
        connection.close()
    return handler.send_json(201, {"message": "Warehouse created successfully.", "warehouse": warehouse})
