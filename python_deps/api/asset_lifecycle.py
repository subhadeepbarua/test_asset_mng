"""Handler for /api/assets/lifecycle."""
from __future__ import annotations

from datetime import date

from router import router
from db import db_connection, ensure_asset_schema, ensure_purchase_schema, ensure_vendor_schema


@router.post("/api/assets/lifecycle")
def handle_asset_lifecycle_post(handler):
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

        submitted_lifecycles = data.get("productLifecycles")
        groups = submitted_lifecycles if isinstance(submitted_lifecycles, list) else [{**data, "assetIds": ids}]
        normalized_lifecycles = []
        seen_lifecycle_ids = set()
        try:
            for group in groups:
                group_ids = [str(value).strip() for value in group.get("assetIds", []) if str(value).strip()]
                ws, we = str(group.get("warrantyStart", "")), str(group.get("warrantyEnd", ""))
                ls, le = int(group.get("lifetimeStart")), int(group.get("lifetimeEnd"))
                total_cost = float(group.get("totalPurchaseCost", 0))
                life = int(group.get("usefulLife") or (le - ls + 1))
                if not group_ids or seen_lifecycle_ids.intersection(group_ids) or le < ls or life <= 0 or total_cost < 0:
                    raise ValueError
                date.fromisoformat(ws)
                date.fromisoformat(we)
                seen_lifecycle_ids.update(group_ids)
                depreciable = total_cost / life
                residual = max(0, total_cost - depreciable)
                normalized_lifecycles.append((group_ids, ws, we, ls, le, life, residual, depreciable))
            if seen_lifecycle_ids != set(ids):
                raise ValueError
        except (TypeError, ValueError):
            return handler.send_json(422, {"error": "Enter valid warranty dates, useful life years, residual values, and matching asset IDs."})

        # Lifecycle entry is available only after the matching invoice
        # product has been fully received in Product Arrived.
        placeholders = ",".join(["%s"] * len(ids))
        cursor.execute(
            f"SELECT DISTINCT b.asset_id,b.product_name,p.invoice_number FROM asset_basic b JOIN asset_purchase p ON p.asset_id=b.asset_id WHERE b.asset_id IN ({placeholders})",
            tuple(ids),
        )
        invoice_products = cursor.fetchall()
        if {item["asset_id"] for item in invoice_products} != set(ids):
            connection.rollback()
            return handler.send_json(409, {"error": "Complete Product Arrived for every selected invoice product before starting its asset lifecycle."})
        checked_products = set()
        for item in invoice_products:
            invoice = str(item["invoice_number"] or "").strip()
            product_name = str(item["product_name"] or "").strip()
            key = (invoice.casefold(), product_name.casefold())
            if key in checked_products:
                continue
            checked_products.add(key)
            cursor.execute(
                "SELECT COALESCE(SUM(received_qty),0) AS received,COALESCE(MAX(order_qty),0) AS ordered,COUNT(*) AS entries FROM receieve_product WHERE LOWER(TRIM(invoice_number))=LOWER(TRIM(%s)) AND LOWER(TRIM(order_product))=LOWER(TRIM(%s))",
                (invoice, product_name),
            )
            receipt = cursor.fetchone()
            if not receipt or not receipt["entries"] or not receipt["ordered"] or receipt["received"] < receipt["ordered"]:
                connection.rollback()
                return handler.send_json(409, {"error": f"Complete Product Arrived for {product_name} on invoice {invoice} before starting its asset lifecycle."})
        for group_ids, ws, we, ls, le, life, residual, depreciable in normalized_lifecycles:
            for asset_id in group_ids:
                cursor.execute("SELECT sl_no FROM asset_life WHERE asset_id=%s", (asset_id,))
                old = cursor.fetchone()
                if old:
                    cursor.execute(
                        "UPDATE asset_life SET warrenty_st=%s,warrenty_end=%s,life_st=%s,life_end=%s,total_life=%s,residual_val=%s,depricable_val=%s,is_update=1 WHERE asset_id=%s",
                        (ws, we, str(ls) + "-01-01", str(le) + "-12-31", life, residual, depreciable, asset_id),
                    )
                else:
                    cursor.execute(
                        "INSERT INTO asset_life (asset_id,warrenty_st,warrenty_end,life_st,life_end,total_life,residual_val,depricable_val,is_update,is_active) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,1)",
                        (asset_id, ws, we, str(ls) + "-01-01", str(le) + "-12-31", life, residual, depreciable),
                    )
                cursor.execute("UPDATE asset_purchase SET residual_val=%s,depricable_val=%s WHERE asset_id=%s", (residual, depreciable, asset_id))
        connection.commit()
        handler.send_json(200, {"message": "Asset details saved.", "assetIds": ids})
    finally:
        connection.close()
