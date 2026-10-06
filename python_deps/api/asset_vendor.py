"""Handler for /api/assets/vendor."""
from __future__ import annotations

from router import router
from db import db_connection, ensure_asset_schema, ensure_purchase_schema, ensure_vendor_schema


@router.post("/api/assets/vendor")
def handle_asset_vendor_post(handler):
    user = handler.require_asset_user()
    if not user:
        return
    data = handler.read_json()
    ids = handler.asset_ids(data)
    if not ids or len(ids) > 500:
        return handler.send_json(422, {"error": "Valid generated asset IDs are required."})
    connection = db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        ensure_vendor_schema(cursor)
        ensure_asset_schema(cursor)
        ensure_purchase_schema(cursor)

        cursor.execute(
            "SELECT asset_id FROM asset_basic WHERE asset_id IN (" + ",".join(["%s"] * len(ids)) + ") AND (created_by=%s OR %s='admin')",
            (*ids, user["user_id"], user["designation"]),
        )
        owned = {row["asset_id"] for row in cursor.fetchall()}
        if len(owned) != len(ids):
            connection.rollback()
            return handler.send_json(403, {"error": "Complete basic details for these assets before continuing."})

        vendor_name = str(data.get("vendorName", "")).strip()
        vendor_type = str(data.get("vendorType", "")).strip()
        vendor_id_input = str(data.get("vendorId", "")).strip()
        if not vendor_name or not vendor_type or not vendor_id_input:
            return handler.send_json(422, {"error": "Vendor ID, name, and type are required."})
        cursor.execute("SELECT vendor_id FROM vendor_details WHERE LOWER(TRIM(vendor_name))=LOWER(TRIM(%s)) OR vendor_id=%s LIMIT 1", (vendor_name, vendor_id_input))
        if cursor.fetchone():
            connection.rollback()
            return handler.send_json(409, {"error": "Vendor name or ID already exists."})
        cursor.execute("INSERT INTO vendor_details (vendor_id,vendor_type,vendor_name,is_update,is_active) VALUES (%s,%s,%s,0,1)", (vendor_id_input, vendor_type, vendor_name))
        values = (
            vendor_id_input,
            (data.get("contactPerson") or vendor_name),
            data.get("contactNumber") or None,
            data.get("email") or None,
            data.get("gstNumber") or None,
            data.get("panNumber") or None,
            data.get("address") or None,
            data.get("pincode") or None,
            data.get("district") or None,
            data.get("country") or None,
            *ids,
        )
        placeholders = ",".join(["%s"] * len(ids))
        cursor.execute(f"SELECT COUNT(DISTINCT asset_id) AS total FROM asset_purchase WHERE asset_id IN ({placeholders})", tuple(ids))
        if cursor.fetchone()["total"] != len(ids):
            connection.rollback()
            return handler.send_json(409, {"error": "Save the invoice for each product before assigning its vendor."})
        cursor.execute(
            f"UPDATE asset_purchase SET vendor_id=%s,contact_person_name=%s,contact_number=%s,contact_email=%s,gst_number=%s,pan_number=%s,address=%s,pincode=%s,district=%s,country=%s,is_update=1 WHERE asset_id IN ({placeholders})",
            values,
        )
        connection.commit()
        handler.send_json(200, {"message": "Asset details saved.", "assetIds": ids})
    finally:
        connection.close()
