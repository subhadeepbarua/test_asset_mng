"""
Vendor API — /api/vendors and /api/vendors/assign

This file owns and registers all its own routes directly.
backend.py does NOT route vendors — this file does it via @router decorators.

Routes registered here:
  GET  /api/vendors
  POST /api/vendors
  POST /api/vendors/assign
"""
from __future__ import annotations

import sys
from pathlib import Path

import mysql.connector

_deps_dir = str(Path(__file__).resolve().parent.parent)
if _deps_dir not in sys.path:
    sys.path.insert(0, _deps_dir)

from router import router
from db import db_connection, ensure_vendor_schema


# ── GET /api/vendors ──────────────────────────────────────────────────────────

@router.get("/api/vendors")
def handle_vendors_get(handler):
    """Return all active vendors."""
    try:
        user = handler.current_user()
        if not user:
            return handler.send_json(401, {"error": "Sign in required."})
        connection = db_connection()
        try:
            cursor = connection.cursor(dictionary=True)
            ensure_vendor_schema(cursor)
            cursor.execute(
                """SELECT v.vendor_id,v.vendor_type,v.vendor_name,p.contact_person_name,p.contact_number,
                p.contact_email,p.gst_number,p.pan_number,p.address,p.pincode,p.district,p.country
                FROM vendor_details v
                LEFT JOIN asset_purchase p ON p.sl_no=(SELECT MAX(p2.sl_no) FROM asset_purchase p2 WHERE p2.vendor_id=v.vendor_id)
                WHERE v.is_active=1 ORDER BY v.vendor_name,v.vendor_id"""
            )
            rows = cursor.fetchall()
        finally:
            connection.close()
        vendors = [
            {
                "vendorId": row["vendor_id"],
                "vendorType": row["vendor_type"],
                "vendorName": row["vendor_name"],
                "contactPerson": row["contact_person_name"],
                "contactNumber": row["contact_number"],
                "email": row["contact_email"],
                "gstNumber": row["gst_number"],
                "panNumber": row["pan_number"],
                "address": row["address"],
                "pincode": row["pincode"],
                "district": row["district"],
                "country": row["country"],
            }
            for row in rows
        ]
        return handler.send_json(200, {"vendors": vendors})
    except mysql.connector.Error as error:
        print(f"[api/vendors] Database error on GET: {error}")
        return handler.send_json(500, {"error": "Could not load vendor records."})


# ── POST /api/vendors ─────────────────────────────────────────────────────────

@router.post("/api/vendors")
def handle_vendors_post(handler):
    """Create a new vendor record."""
    user = handler.require_asset_user()
    if not user:
        return
    data = handler.read_json()
    vendor_id = str(data.get("vendorId", "")).strip()
    vendor_name = str(data.get("vendorName", "")).strip()
    vendor_type = str(data.get("vendorType", "")).strip()
    if not vendor_id or not vendor_name or not vendor_type:
        return handler.send_json(422, {"error": "Vendor ID, name, and type are required."})
    connection = db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        ensure_vendor_schema(cursor)
        cursor.execute("SELECT vendor_id FROM vendor_details WHERE LOWER(TRIM(vendor_name))=LOWER(TRIM(%s)) LIMIT 1", (vendor_name,))
        if cursor.fetchone():
            connection.rollback()
            return handler.send_json(409, {"error": "A vendor with this name already exists."})
        cursor.execute("SELECT vendor_id FROM vendor_details WHERE vendor_id=%s LIMIT 1", (vendor_id,))
        if cursor.fetchone():
            connection.rollback()
            return handler.send_json(409, {"error": "This vendor ID is already in use."})
        cursor.execute("INSERT INTO vendor_details (vendor_id,vendor_type,vendor_name,is_update,is_active) VALUES (%s,%s,%s,0,1)", (vendor_id, vendor_type, vendor_name))
        connection.commit()
    finally:
        connection.close()
    return handler.send_json(
        201,
        {
            "message": "Vendor saved.",
            "vendor": {
                "vendorId": vendor_id,
                "vendorName": vendor_name,
                "vendorType": vendor_type,
                "contactPerson": str(data.get("contactPerson") or vendor_name).strip(),
                "contactNumber": data.get("contactNumber"),
                "email": data.get("email"),
                "gstNumber": data.get("gstNumber"),
                "panNumber": data.get("panNumber"),
                "address": data.get("address"),
                "pincode": data.get("pincode"),
                "district": data.get("district"),
                "country": data.get("country"),
            },
        },
    )


# ── POST /api/vendors/assign ──────────────────────────────────────────────────

@router.post("/api/vendors/assign")
def handle_vendors_assign_post(handler):
    """Assign a vendor to one or more asset IDs."""
    user = handler.require_asset_user()
    if not user:
        return
    data = handler.read_json()
    ids = handler.asset_ids(data)
    vendor_id = str(data.get("vendorId", "")).strip()
    if not ids or not vendor_id:
        return handler.send_json(422, {"error": "Choose a vendor and at least one asset."})
    connection = db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        ensure_vendor_schema(cursor)
        cursor.execute("SELECT vendor_id,vendor_type,vendor_name FROM vendor_details WHERE vendor_id=%s AND is_active=1 LIMIT 1", (vendor_id,))
        vendor = cursor.fetchone()
        if not vendor:
            return handler.send_json(404, {"error": "The selected vendor could not be found."})
        placeholders = ",".join(["%s"] * len(ids))
        cursor.execute(f"SELECT asset_id FROM asset_basic WHERE asset_id IN ({placeholders}) AND (created_by=%s OR %s='admin')", (*ids, user["user_id"], user["designation"]))
        owned = {row["asset_id"] for row in cursor.fetchall()}
        if len(owned) != len(ids):
            connection.rollback()
            return handler.send_json(403, {"error": "Complete basic details for these products before assigning a vendor."})
        cursor.execute("SELECT contact_person_name,contact_number,contact_email,gst_number,pan_number,address,pincode,district,country FROM asset_purchase WHERE vendor_id=%s ORDER BY sl_no DESC LIMIT 1", (vendor_id,))
        snapshot = cursor.fetchone() or {}
        assignments = (
            vendor_id,
            snapshot.get("contact_person_name") or vendor["vendor_name"],
            snapshot.get("contact_number"),
            snapshot.get("contact_email"),
            snapshot.get("gst_number"),
            snapshot.get("pan_number"),
            snapshot.get("address"),
            snapshot.get("pincode"),
            snapshot.get("district"),
            snapshot.get("country"),
        )
        placeholders = ",".join(["%s"] * len(ids))
        cursor.execute(f"SELECT COUNT(DISTINCT asset_id) AS total FROM asset_purchase WHERE asset_id IN ({placeholders})", tuple(ids))
        if cursor.fetchone()["total"] != len(ids):
            connection.rollback()
            return handler.send_json(409, {"error": "Save the invoice for each product before assigning its vendor."})
        cursor.execute(
            f"UPDATE asset_purchase SET vendor_id=%s,contact_person_name=%s,contact_number=%s,contact_email=%s,gst_number=%s,pan_number=%s,address=%s,pincode=%s,district=%s,country=%s,is_update=1 WHERE asset_id IN ({placeholders})",
            (*assignments, *ids),
        )
        connection.commit()
    finally:
        connection.close()
    return handler.send_json(200, {"message": "Vendor assigned to product assets.", "assetIds": ids})
