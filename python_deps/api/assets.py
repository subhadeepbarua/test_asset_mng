"""
Asset listing API — /api/assets/mine and /api/admin/assets

This file owns and registers all its own routes directly.
backend.py does NOT route these — this file does it via @router decorators.

Routes registered here:
  GET /api/assets/mine
  GET /api/admin/assets
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import mysql.connector

_deps_dir = str(Path(__file__).resolve().parent.parent)
if _deps_dir not in sys.path:
    sys.path.insert(0, _deps_dir)

from router import router
from db import db_connection, ensure_asset_schema, ensure_purchase_schema, ensure_vendor_schema


def _build_assets_response(handler, route: str):
    """Shared logic for both /api/assets/mine and /api/admin/assets."""
    try:
        user = handler.current_user()
        if not user:
            return handler.send_json(401, {"error": "Sign in required."})
        admin_only = route == "/api/admin/assets"
        if admin_only and user.get("designation") != "admin":
            return handler.send_json(403, {"error": "Administrator access required."})
        connection = db_connection()
        try:
            cursor = connection.cursor(dictionary=True)
            ensure_vendor_schema(cursor)
            ensure_asset_schema(cursor)
            ensure_purchase_schema(cursor)
            query = """SELECT b.*,p.invoice_number,p.tax_invoice_number,p.purchase_date_time,p.unit_price,p.pay_method,
                p.total_purchase_amount,p.residual_val AS purchase_residual,p.depricable_val AS purchase_depreciable,
                p.vendor_id,p.contact_person_name,p.contact_number,p.contact_email,p.gst_number,p.pan_number,
                p.address,p.pincode,p.district,p.country,l.warrenty_st,l.warrenty_end,l.life_st,l.life_end,
                l.total_life,l.residual_val AS life_residual,l.depricable_val AS life_depreciable,
                v.vendor_type,v.vendor_name
                FROM asset_basic b
                LEFT JOIN asset_purchase p ON p.asset_id=b.asset_id
                LEFT JOIN asset_life l ON l.asset_id=b.asset_id
                LEFT JOIN vendor_details v ON v.vendor_id=p.vendor_id"""
            if not admin_only:
                query += " WHERE b.created_by=%s"
            query += " ORDER BY b.created_at DESC,b.asset_id"
            cursor.execute(query, () if admin_only else (user["user_id"],))
            rows = cursor.fetchall()
        finally:
            connection.close()

        assets = []
        for row in rows:
            assets.append({
                "assetId": row["asset_id"],
                "assetIds": [row["asset_id"]],
                "quantity": row["quantity"],
                "uom": row.get("uom") or "",
                "productName": row["product_name"],
                "brand": row["brand"],
                "model": row["model"],
                "category": row["asset_category"],
                "assetAssignment": row["asset_assign"],
                "status": row["status"],
                "isActive": bool(row["is_active"]),
                "createdBy": row["created_by"],
                "createdAt": str(row["created_at"]),
                "purchase": {
                    "invoiceNumber": row.get("invoice_number"),
                    "taxInvoiceNumber": row.get("tax_invoice_number"),
                    "purchaseDate": str(row["purchase_date_time"] or "")[:10],
                    "unitPrice": float(row["unit_price"] or 0),
                    "totalPrice": float(row["total_purchase_amount"] or 0),
                    "paymentMethod": row["pay_method"],
                    "residualValue": float(row["purchase_residual"] or 0),
                    "depreciableAmount": float(row["purchase_depreciable"] or 0),
                } if row.get("invoice_number") or row.get("purchase_date_time") else None,
                "lifecycle": {
                    "warrantyStart": str(row["warrenty_st"] or ""),
                    "warrantyEnd": str(row["warrenty_end"] or ""),
                    "lifetimeStart": str(row["life_st"] or "")[:4],
                    "lifetimeEnd": str(row["life_end"] or "")[:4],
                    "usefulLife": row["total_life"],
                    "residualValue": float(row["life_residual"] or 0),
                    "depreciableAmount": float(row["life_depreciable"] or 0),
                } if row["warrenty_st"] else None,
                "vendor": {
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
                } if row["vendor_id"] else None,
            })
        by_id = {asset["assetId"]: asset for asset in assets}
        grouped = []
        product_groups = {}
        for asset in assets:
            purchase = asset.get("purchase") or {}
            invoice_number = purchase.get("invoiceNumber")
            if invoice_number:
                group_key = (
                    invoice_number,
                    asset.get("productName"),
                    asset.get("brand"),
                    asset.get("model"),
                    asset.get("category"),
                    asset.get("uom"),
                    purchase.get("unitPrice"),
                    (asset.get("vendor") or {}).get("vendorId"),
                )
                if group_key in product_groups:
                    parent = product_groups[group_key]
                    parent["assetIds"].append(asset["assetId"])
                    parent["quantity"] += 1
                    parent["purchase"]["totalPrice"] += purchase.get("totalPrice", 0)
                    continue
                product_groups[group_key] = asset
                asset["assetIds"] = [asset["assetId"]]
                asset["quantity"] = 1
            quantity = int(asset.get("quantity") or 0)
            if quantity == 0:
                continue
            if quantity > 1:
                match = re.match(r"^(.*?)(\d+)$", asset["assetId"])
                if match:
                    first_number = int(match.group(2))
                    width = len(match.group(2))
                    ids = [match.group(1) + str(first_number + offset).zfill(width) for offset in range(quantity)]
                    asset["assetIds"] = [asset_id for asset_id in ids if asset_id in by_id]
                    asset["quantity"] = len(asset["assetIds"])
            grouped.append(asset)
        return handler.send_json(200, {"assets": grouped})
    except mysql.connector.Error as error:
        print(f"[api/assets] Database error on {route}: {error}")
        return handler.send_json(500, {"error": "Could not load asset records."})


# ── GET /api/assets/mine ──────────────────────────────────────────────────────

@router.get("/api/assets/mine")
def handle_assets_mine(handler):
    """Return assets belonging to the signed-in user."""
    return _build_assets_response(handler, "/api/assets/mine")


# ── GET /api/admin/assets ─────────────────────────────────────────────────────

@router.get("/api/admin/assets")
def handle_admin_assets(handler):
    """Return all assets across all users (admin only)."""
    return _build_assets_response(handler, "/api/admin/assets")
