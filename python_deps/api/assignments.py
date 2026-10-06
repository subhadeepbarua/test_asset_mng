"""
Assignment and overall-places API — /api/assignments and /api/overall-places

This file owns and registers all its own routes directly.
backend.py does NOT route assignments — this file does it via @router decorators.

Routes registered here:
  GET  /api/overall-places
  GET  /api/assignments
  POST /api/assignments
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import mysql.connector

_deps_dir = str(Path(__file__).resolve().parent.parent)
if _deps_dir not in sys.path:
    sys.path.insert(0, _deps_dir)

from router import router
from db import (
    db_connection,
    ensure_assigned_product_schema,
    ensure_employee_schema,
    ensure_overall_place_schema,
    ensure_receive_product_schema,
)


# ── GET /api/overall-places ───────────────────────────────────────────────────

@router.get("/api/overall-places")
def handle_overall_places_get(handler):
    user = handler.require_asset_user()
    if not user:
        return
    connection = db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        ensure_overall_place_schema(cursor)
        connection.commit()
        cursor.execute("SELECT sl_no,place_name,address,is_active,is_update,created_at,updated_at FROM overall_place WHERE is_active=1 ORDER BY place_name")
        rows = cursor.fetchall()
        return handler.send_json(
            200,
            {
                "places": [
                    {
                        "slNo": row["sl_no"],
                        "placeName": row["place_name"],
                        "address": row["address"],
                        "isActive": bool(row["is_active"]),
                        "isUpdate": bool(row["is_update"]),
                        "createdAt": str(row["created_at"] or ""),
                        "updatedAt": str(row["updated_at"] or ""),
                    }
                    for row in rows
                ]
            },
        )
    except mysql.connector.Error as error:
        print(f"Database error loading overall places: {error}")
        return handler.send_json(500, {"error": "Could not load places from MySQL."})
    finally:
        connection.close()


# ── GET /api/assignments ──────────────────────────────────────────────────────

@router.get("/api/assignments")
def handle_assignments_get(handler):
    user = handler.require_asset_user()
    if not user:
        return
    connection = db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        ensure_employee_schema(cursor)
        ensure_assigned_product_schema(cursor)
        cursor.execute(
            """SELECT a.sl_no,a.asset_id,a.assign_quantity,a.invoice_number,a.product_name,a.emp_id,a.asset_assign,a.place,
            a.assigned_by,a.is_update,a.is_active,a.created_at,a.updated_at,e.emp_name
            FROM assigned_product a LEFT JOIN employee e ON e.emp_id=a.emp_id AND e.created_by=a.assigned_by
            WHERE a.assigned_by=%s ORDER BY a.sl_no DESC""",
            (user["user_id"],),
        )
        rows = cursor.fetchall()
        assignments = [
            {
                "slNo": row["sl_no"],
                "assetId": row["asset_id"],
                "quantity": int(row["assign_quantity"] or 0),
                "invoiceNumber": row["invoice_number"],
                "productName": row["product_name"],
                "employeeId": row["emp_id"],
                "employeeName": row["emp_name"],
                "assetAssign": row["asset_assign"],
                "place": row["place"],
                "assignedBy": row["assigned_by"],
                "isUpdate": bool(row["is_update"]),
                "isActive": bool(row["is_active"]),
                "createdAt": str(row["created_at"] or ""),
                "updatedAt": str(row["updated_at"] or ""),
            }
            for row in rows
        ]
        return handler.send_json(200, {"assignments": assignments})
    except mysql.connector.Error as error:
        print(f"Database error loading assignments: {error}")
        return handler.send_json(500, {"error": "Could not load product assignments from MySQL."})
    finally:
        connection.close()


# ── POST /api/assignments ─────────────────────────────────────────────────────

@router.post("/api/assignments")
def handle_assignments_post(handler):
    user = handler.require_asset_user()
    if not user:
        return
    data = handler.read_json()
    invoice = str(data.get("invoiceNumber", "")).strip()[:120]
    product_name = str(data.get("productName", "")).strip()[:255]
    employee_id = str(data.get("employeeId", "")).strip()[:100]
    place_name = str(data.get("placeName", "")).strip()[:150]
    try:
        quantity = int(data.get("quantity"))
    except (TypeError, ValueError):
        return handler.send_json(422, {"error": "Enter a whole number quantity to assign."})
    if not invoice or not product_name or quantity < 1:
        return handler.send_json(422, {"error": "Choose an arrived product and a quantity of at least 1."})
    connection = db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        ensure_employee_schema(cursor)
        ensure_receive_product_schema(cursor)
        ensure_assigned_product_schema(cursor)
        if user.get("designation") == "admin":
            ownership_clause = "1=1"
            owner_params = ()
        else:
            ownership_clause = "b.created_by=%s"
            owner_params = (user["user_id"],)
        cursor.execute(
            f"""SELECT MIN(b.asset_id) AS asset_id,COUNT(DISTINCT b.asset_id) AS asset_quantity,
            MAX(b.asset_assign) AS asset_assign,COUNT(DISTINCT LOWER(b.asset_assign)) AS assignment_type_count
            FROM asset_basic b JOIN asset_purchase p ON p.asset_id=b.asset_id
            WHERE LOWER(TRIM(p.invoice_number))=LOWER(TRIM(%s))
            AND LOWER(TRIM(b.product_name))=LOWER(TRIM(%s)) AND b.is_active=1
            AND {ownership_clause}""",
            (invoice, product_name, *owner_params),
        )
        asset = cursor.fetchone()
        if not asset or not asset["asset_id"]:
            return handler.send_json(404, {"error": "This product and invoice are not available in your assets."})
        if int(asset["assignment_type_count"] or 0) > 1:
            return handler.send_json(409, {"error": "This invoice contains the same product under different Assigned Types. Update the product rows so this product has one type before assigning it."})
        assignment_type = str(asset.get("asset_assign") or "individual").strip().lower()
        employee = None
        if assignment_type == "individual":
            if not employee_id:
                return handler.send_json(422, {"error": "Choose an employee for an individual product."})
            cursor.execute("SELECT emp_id,emp_name FROM employee WHERE emp_id=%s AND created_by=%s AND is_active=1 LIMIT 1", (employee_id, user["user_id"]))
            employee = cursor.fetchone()
            if not employee:
                return handler.send_json(404, {"error": "Choose an active employee created by your account."})
            place_name = ""
        elif assignment_type == "overall":
            if not place_name:
                return handler.send_json(422, {"error": "Choose a place for an overall product."})
            ensure_overall_place_schema(cursor)
            cursor.execute("SELECT place_name FROM overall_place WHERE LOWER(TRIM(place_name))=LOWER(TRIM(%s)) AND is_active=1 LIMIT 1", (place_name,))
            place_row = cursor.fetchone()
            if not place_row:
                return handler.send_json(404, {"error": "Choose an active existing place."})
            place_name = place_row["place_name"]
            employee_id = ""
        else:
            return handler.send_json(409, {"error": "This product has an unsupported Assigned Type. Choose Individual or Overall in Add Asset."})

        # Start a fresh transaction after waiting for the product lock so
        # the received/assigned totals below include any prior commit.
        connection.commit()
        lock_digest = hashlib.sha256((invoice.casefold() + "\0" + product_name.casefold()).encode("utf-8")).hexdigest()[:40]
        lock_name = "fixed_asset_assign_" + lock_digest
        cursor.execute("SELECT GET_LOCK(%s,10) AS acquired", (lock_name,))
        if cursor.fetchone()["acquired"] != 1:
            connection.rollback()
            return handler.send_json(503, {"error": "Could not reserve this product quantity for assignment. Please try again."})
        cursor.execute(
            """SELECT COALESCE(SUM(received_qty),0) AS received,COALESCE(MAX(order_qty),0) AS ordered
            FROM receieve_product WHERE LOWER(TRIM(invoice_number))=LOWER(TRIM(%s))
            AND LOWER(TRIM(order_product))=LOWER(TRIM(%s))""",
            (invoice, product_name),
        )
        receipt = cursor.fetchone()
        arrived_quantity = min(int(receipt["received"] or 0), int(receipt["ordered"] or 0), int(asset["asset_quantity"] or 0))
        if arrived_quantity < 1:
            connection.rollback()
            return handler.send_json(409, {"error": "This product has no recorded Product Arrived quantity and cannot be assigned."})
        cursor.execute(
            """SELECT COALESCE(SUM(assign_quantity),0) AS assigned FROM assigned_product
            WHERE LOWER(TRIM(invoice_number))=LOWER(TRIM(%s))
            AND LOWER(TRIM(product_name))=LOWER(TRIM(%s)) AND is_active=1""",
            (invoice, product_name),
        )
        already_assigned = int(cursor.fetchone()["assigned"] or 0)
        available = max(0, arrived_quantity - already_assigned)
        if quantity > available:
            connection.rollback()
            return handler.send_json(409, {"error": f"Only {available} arrived units remain available to assign for {product_name}."})
        cursor.execute(
            f"""SELECT b.asset_id FROM asset_basic b JOIN asset_purchase p ON p.asset_id=b.asset_id
            WHERE LOWER(TRIM(p.invoice_number))=LOWER(TRIM(%s))
            AND LOWER(TRIM(b.product_name))=LOWER(TRIM(%s)) AND b.is_active=1
            AND {ownership_clause} ORDER BY b.asset_id""",
            (invoice, product_name, *owner_params),
        )
        product_asset_ids = [row["asset_id"] for row in cursor.fetchall()]
        if product_asset_ids:
            placeholders = ",".join(["%s"] * len(product_asset_ids))
            cursor.execute(f"SELECT DISTINCT asset_id FROM assigned_product WHERE is_active=1 AND asset_id IN ({placeholders})", tuple(product_asset_ids))
            previously_assigned_ids = {row["asset_id"] for row in cursor.fetchall()}
        else:
            previously_assigned_ids = set()
        available_asset_ids = [asset_id for asset_id in product_asset_ids if asset_id not in previously_assigned_ids]
        if len(available_asset_ids) < quantity:
            connection.rollback()
            return handler.send_json(409, {"error": f"Could not find {quantity} unassigned asset IDs for {product_name}."})
        selected_asset_ids = available_asset_ids[:quantity]
        cursor.executemany(
            """INSERT INTO assigned_product
            (asset_id,assign_quantity,invoice_number,product_name,emp_id,asset_assign,place,assigned_by,is_update,is_active)
            VALUES (%s,1,%s,%s,%s,%s,%s,%s,0,1)""",
            [(asset_id, invoice, product_name, employee_id or None, assignment_type, place_name or None, user["user_id"]) for asset_id in selected_asset_ids],
        )
        connection.commit()
        cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
        cursor.fetchone()
        placeholders = ",".join(["%s"] * len(selected_asset_ids))
        cursor.execute(
            f"""SELECT a.sl_no,a.asset_id,a.assign_quantity,a.invoice_number,a.product_name,a.emp_id,a.asset_assign,a.place,
            a.assigned_by,a.is_update,a.is_active,a.created_at,a.updated_at,e.emp_name
            FROM assigned_product a LEFT JOIN employee e ON e.emp_id=a.emp_id AND e.created_by=a.assigned_by
            WHERE a.asset_id IN ({placeholders}) AND a.assigned_by=%s AND a.is_active=1 ORDER BY a.sl_no DESC""",
            (*selected_asset_ids, user["user_id"]),
        )
        rows = cursor.fetchall()
        assignments = [
            {
                "slNo": row["sl_no"],
                "assetId": row["asset_id"],
                "quantity": int(row["assign_quantity"]),
                "invoiceNumber": row["invoice_number"],
                "productName": row["product_name"],
                "employeeId": row["emp_id"],
                "employeeName": row["emp_name"],
                "assetAssign": row["asset_assign"],
                "place": row["place"],
                "assignedBy": row["assigned_by"],
                "isUpdate": bool(row["is_update"]),
                "isActive": bool(row["is_active"]),
                "createdAt": str(row["created_at"] or ""),
                "updatedAt": str(row["updated_at"] or ""),
            }
            for row in rows
        ]
        return handler.send_json(201, {"message": f"{quantity} asset(s) assigned successfully.", "assignments": assignments, "availableQuantity": available - quantity})
    finally:
        connection.close()
