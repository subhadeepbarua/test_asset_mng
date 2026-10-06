"""
Asset Basic API — /api/assets/basic

This file owns and registers its own route directly.
backend.py does NOT route this — this file does it via @router decorators.

Routes registered here:
  POST /api/assets/basic
"""
from __future__ import annotations

import sys
from pathlib import Path

_deps_dir = str(Path(__file__).resolve().parent.parent)
if _deps_dir not in sys.path:
    sys.path.insert(0, _deps_dir)

from router import router
from db import db_connection, ensure_asset_schema, ensure_purchase_schema, ensure_vendor_schema


# ── POST /api/assets/basic ─────────────────────────────────────────────────────

@router.post("/api/assets/basic")
def handle_asset_basic_post(handler):
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

        products = data.get("products") or [
            {
                "productName": data.get("productName"),
                "brand": data.get("brand"),
                "model": data.get("model"),
                "category": data.get("category"),
                "assetAssignment": data.get("assetAssignment"),
                "uom": data.get("uom"),
                "assetIds": ids,
            }
        ]
        if not isinstance(products, list) or not products:
            return handler.send_json(422, {"error": "Add at least one product."})
        seen_ids = set()
        for product in products:
            name = str(product.get("productName", "")).strip()
            category = str(product.get("category", "")).strip()
            assignment = str(product.get("assetAssignment", "")).lower()
            uom = str(product.get("uom") or "").strip()
            product_ids = [str(value).strip() for value in product.get("assetIds", []) if str(value).strip()]
            if not name or not category or assignment not in ("individual", "overall") or not product_ids or seen_ids.intersection(product_ids):
                return handler.send_json(422, {"error": "Each product needs a name, category, assignment, and unique generated asset IDs."})
            seen_ids.update(product_ids)
            if len(seen_ids) > 500:
                return handler.send_json(422, {"error": "Too many asset IDs."})
            for index, asset_id in enumerate(product_ids):
                unit_quantity = len(product_ids) if index == 0 else 0
                cursor.execute("SELECT created_by FROM asset_basic WHERE asset_id=%s", (asset_id,))
                old = cursor.fetchone()
                if old and old["created_by"] != user["user_id"] and user["designation"] != "admin":
                    connection.rollback()
                    return handler.send_json(409, {"error": "One of these asset IDs already belongs to another user."})
                values = (name, product.get("model") or None, product.get("brand") or None, category, assignment, uom, unit_quantity, asset_id)
                if old:
                    cursor.execute(
                        "UPDATE asset_basic SET product_name=%s,model=%s,brand=%s,asset_category=%s,asset_assign=%s,uom=%s,quantity=%s,status='active',is_update=1 WHERE asset_id=%s",
                        values,
                    )
                else:
                    cursor.execute(
                        "INSERT INTO asset_basic (product_name,model,brand,asset_category,asset_assign,uom,quantity,asset_id,created_by,status,is_update,is_active) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',0,1)",
                        (*values, user["user_id"]),
                    )
        if set(ids) != seen_ids:
            return handler.send_json(422, {"error": "Asset IDs do not match the submitted products."})
        connection.commit()
        handler.send_json(200, {"message": "Asset details saved.", "assetIds": ids})
    finally:
        connection.close()
