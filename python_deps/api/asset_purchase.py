"""Handler for /api/assets/purchase."""
from __future__ import annotations

from router import router
from db import db_connection, ensure_asset_schema, ensure_purchase_schema, ensure_vendor_schema


@router.post("/api/assets/purchase")
def handle_asset_purchase_post(handler):
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

        date = str(data.get("purchaseDate", ""))
        method = str(data.get("paymentMethod", "")).strip().lower().replace(" ", "_")
        method = {"credit_card": "card", "debit_card": "card"}.get(method, method)
        if method not in {"cash", "card", "upi", "bank_transfer", "cheque", "other"}:
            method = "other"
        try:
            dt = date + " 00:00:00"
            purchase_groups = data.get("productPurchases")
            if not isinstance(purchase_groups, list) or not purchase_groups:
                purchase_groups = [{"assetIds": ids, "unitPrice": data.get("unitPrice", 0)}]
            purchase_rows = []
            seen_purchase_ids = set()
            for group in purchase_groups:
                price = float(group.get("unitPrice", 0))
                group_ids = [str(value).strip() for value in group.get("assetIds", []) if str(value).strip()]
                if price < 0 or not group_ids or seen_purchase_ids.intersection(group_ids):
                    raise ValueError
                seen_purchase_ids.update(group_ids)
                group_quantity = int(group.get("quantity") or len(group_ids))
                if group_quantity != len(group_ids):
                    raise ValueError
                group_total = price * group_quantity
                purchase_rows.extend((asset_id, price, index == 0, group_total) for index, asset_id in enumerate(group_ids))
            if seen_purchase_ids != set(ids):
                raise ValueError
        except (TypeError, ValueError):
            return handler.send_json(422, {"error": "Enter a valid unit price for every product."})

        invoice_number = str(data.get("invoiceNumber") or "").strip()[:120]
        tax_invoice_number = str(data.get("taxInvoiceNumber") or "").strip()[:120] or None
        for i, (asset_id, unit, is_group_lead, group_total) in enumerate(purchase_rows):
            cursor.execute("SELECT sl_no FROM asset_purchase WHERE asset_id=%s ORDER BY sl_no DESC LIMIT 1", (asset_id,))
            old = cursor.fetchone()
            stored_total = group_total if is_group_lead else unit
            if old:
                cursor.execute(
                    "UPDATE asset_purchase SET purchase_date_time=%s,unit_price=%s,pay_method=%s,total_purchase_amount=%s,invoice_number=COALESCE(NULLIF(invoice_number,''),NULLIF(%s,'')),tax_invoice_number=%s,is_update=1 WHERE asset_id=%s",
                    (dt, unit, method, stored_total, invoice_number, tax_invoice_number, asset_id),
                )
            else:
                cursor.execute(
                    "INSERT INTO asset_purchase (asset_id,purchase_date_time,unit_price,pay_method,total_purchase_amount,residual_val,depricable_val,invoice_number,tax_invoice_number,is_update,is_active) VALUES (%s,%s,%s,%s,%s,0,%s,%s,%s,0,1)",
                    (asset_id, dt, unit, method, stored_total, unit, invoice_number or None, tax_invoice_number),
                )
        connection.commit()
        handler.send_json(200, {"message": "Asset details saved.", "assetIds": ids})
    finally:
        connection.close()
