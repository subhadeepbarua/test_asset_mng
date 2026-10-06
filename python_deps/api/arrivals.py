"""Product arrival verification API handlers."""
from __future__ import annotations

import re
from datetime import date, datetime

import mysql.connector
from router import router
from db import (
    db_connection,
    ensure_purchase_schema,
    ensure_receive_product_schema,
    ensure_vendor_schema,
    ensure_warehouse_schema,
    receive_product_records,
    warehouse_identifier_column,
)


def arrival_records(cursor, user_id=None):
    query = """SELECT r.arrival_id,r.invoice_number,MIN(DATE(r.created_at)) AS arrival_date,MIN(r.warehouse) AS warehouse,
        MAX(r.created_at) AS verified_at,MAX(r.sl_no) AS latest_sl_no
        FROM receieve_product r WHERE r.arrival_id IS NOT NULL"""
    params = ()
    if user_id:
        query += " AND EXISTS (SELECT 1 FROM asset_purchase p JOIN asset_basic b ON b.asset_id=p.asset_id WHERE p.invoice_number=r.invoice_number AND b.created_by=%s)"
        params = (user_id,)
    query += " GROUP BY r.arrival_id,r.invoice_number ORDER BY latest_sl_no DESC"
    cursor.execute(query, params)
    records = []
    arrivals = cursor.fetchall()
    for arrival in arrivals:
        cursor.execute(
            """SELECT order_product,order_qty,received_qty,received_totalamount,product_remain,status
            FROM receieve_product WHERE arrival_id=%s ORDER BY sl_no""",
            (arrival["arrival_id"],),
        )
        receipt_rows = cursor.fetchall()
        lines = []
        user_ids = []
        for receipt in receipt_rows:
            cursor.execute(
                """SELECT b.asset_id,b.created_by,p.unit_price,
                COALESCE(v.vendor_name,p.contact_person_name,'General Vendor') AS vendor_name
                FROM asset_basic b JOIN asset_purchase p ON p.asset_id=b.asset_id
                LEFT JOIN vendor_details v ON v.vendor_id=p.vendor_id
                WHERE p.invoice_number=%s AND b.product_name=%s ORDER BY b.asset_id""",
                (arrival["invoice_number"], receipt["order_product"]),
            )
            assets = cursor.fetchall()
            user_ids.extend(asset["created_by"] for asset in assets if asset["created_by"])
            actual = {
                "name": receipt["order_product"],
                "quantity": int(receipt["order_qty"] or 0),
                "totalPrice": sum(float(asset["unit_price"] or 0) for asset in assets),
                "vendor": ", ".join(dict.fromkeys(asset["vendor_name"] for asset in assets)) or "General Vendor",
            }
            receiving = {
                "name": receipt["order_product"],
                "quantity": int(receipt["received_qty"] or 0),
                "totalPrice": float(receipt["received_totalamount"] or 0),
                "vendor": actual["vendor"],
            }
            lines.append({
                "assetIds": [asset["asset_id"] for asset in assets],
                "actual": actual,
                "receiving": receiving,
                "status": receipt["status"],
                "remaining": int(receipt["product_remain"] or 0),
            })
        actual_summary = {
            "name": ", ".join(line["actual"]["name"] for line in lines),
            "quantity": sum(line["actual"]["quantity"] for line in lines),
            "totalPrice": sum(line["actual"]["totalPrice"] for line in lines),
            "vendor": ", ".join(dict.fromkeys(line["actual"]["vendor"] for line in lines)),
        }
        receiving_summary = {
            "name": ", ".join(line["receiving"]["name"] for line in lines),
            "quantity": sum(line["receiving"]["quantity"] for line in lines),
            "totalPrice": sum(line["receiving"]["totalPrice"] for line in lines),
            "vendor": ", ".join(dict.fromkeys(line["receiving"]["vendor"] for line in lines)),
        }
        records.append({
            "id": arrival["arrival_id"],
            "userId": user_ids[0] if user_ids else None,
            "invoiceNumber": arrival["invoice_number"],
            "arrivalDate": str(arrival["arrival_date"]),
            "warehouse": arrival["warehouse"],
            "status": "fulfilled" if all(line["remaining"] == 0 for line in lines) else "pending",
            "actual": actual_summary,
            "receiving": receiving_summary,
            "lines": lines,
            "verifiedAt": str(arrival["verified_at"]),
        })
    return records


@router.get("/api/arrivals/mine")
def handle_arrivals_mine_get(handler):
    user = handler.require_asset_user()
    if not user:
        return
    connection = db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        ensure_receive_product_schema(cursor)
        admin_view = user.get("designation") == "admin"
        record_user_id = None if admin_view else user["user_id"]
        return handler.send_json(
            200,
            {
                "records": arrival_records(cursor, record_user_id),
                "receiveProducts": receive_product_records(cursor, record_user_id),
            },
        )
    finally:
        connection.close()


@router.post("/api/arrivals")
def handle_arrivals_post(handler):
    user = handler.require_asset_user()
    if not user:
        return
    data = handler.read_json()
    invoice = str(data.get("invoiceNumber", "")).strip()
    arrival_date = str(data.get("arrivalDate", "")).strip()
    try:
        warehouse_id = int(data.get("warehouseId"))
    except (TypeError, ValueError):
        return handler.send_json(422, {"error": "Choose a warehouse before saving the arrival."})
    submitted = data.get("lines")
    try:
        date.fromisoformat(arrival_date)
    except ValueError:
        return handler.send_json(422, {"error": "Enter a valid arrival date."})
    if not invoice or not isinstance(submitted, list) or not submitted:
        return handler.send_json(422, {"error": "Choose an invoice and enter receiving details for its products."})
    connection = db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        ensure_vendor_schema(cursor)
        ensure_purchase_schema(cursor)
        ensure_receive_product_schema(cursor)
        ensure_warehouse_schema(cursor)
        warehouse_key = warehouse_identifier_column(cursor)
        cursor.execute(f"SELECT warehouse_name FROM warehouse WHERE {warehouse_key}=%s AND is_active=1 LIMIT 1", (warehouse_id,))
        warehouse_row = cursor.fetchone()
        if not warehouse_row:
            return handler.send_json(422, {"error": "The selected warehouse is unavailable. Refresh the page and choose an active warehouse."})
        warehouse_name = warehouse_row["warehouse_name"]
        cursor.execute(
            """SELECT b.asset_id,b.product_name,b.brand,b.model,b.asset_category,COALESCE(b.uom,'') AS uom,p.unit_price,p.vendor_id,COALESCE(v.vendor_name,p.contact_person_name,'General Vendor') AS vendor_name
            FROM asset_basic b JOIN asset_purchase p ON p.asset_id=b.asset_id
            LEFT JOIN vendor_details v ON v.vendor_id=p.vendor_id
            WHERE p.invoice_number=%s AND (b.created_by=%s OR %s='admin') AND b.is_active=1 ORDER BY b.asset_id""",
            (invoice, user["user_id"], user["designation"]),
        )
        source = cursor.fetchall()
        if not source:
            return handler.send_json(404, {"error": "That invoice does not exist or is not available to this account."})
        groups = {}
        for row in source:
            key = (row["product_name"], row["brand"], row["model"], row["asset_category"], float(row["unit_price"] or 0), row["vendor_id"])
            group = groups.setdefault(
                key,
                {"assetIds": [], "actual": {"name": row["product_name"], "category": row["asset_category"], "uom": row.get("uom") or "", "quantity": 0, "totalPrice": 0.0, "vendor": row["vendor_name"]}},
            )
            group["assetIds"].append(row["asset_id"])
            group["actual"]["quantity"] += 1
            group["actual"]["totalPrice"] += float(row["unit_price"] or 0)
        asset_group = {asset_id: key for key, group in groups.items() for asset_id in group["assetIds"]}
        submitted_by_group = {}
        used_asset_ids = set()
        for line in submitted:
            if not isinstance(line, dict):
                continue
            line_ids = line.get("assetIds") if isinstance(line.get("assetIds"), list) else []
            if line_ids:
                key = frozenset(map(str, line_ids))
                if not key or used_asset_ids.intersection(key):
                    return handler.send_json(422, {"error": "Submit each received product unit only once."})
                matching_groups = {asset_group.get(asset_id) for asset_id in key}
                if None in matching_groups or len(matching_groups) != 1:
                    return handler.send_json(422, {"error": "Receiving rows must match products from the selected invoice."})
                used_asset_ids.update(key)
                group_key = next(iter(matching_groups))
                submitted_by_group.setdefault(group_key, []).append(line)
        lines = []
        for group_key, supplied_rows in submitted_by_group.items():
            group = groups[group_key]
            actual = group["actual"]
            try:
                received_qty = 0
                received_total = 0.0
                for supplied in supplied_rows:
                    receive = supplied.get("receiving") if isinstance(supplied.get("receiving"), dict) else {}
                    row_qty = int(receive.get("quantity"))
                    row_total = float(receive.get("totalPrice"))
                    if row_qty <= 0 or row_total < 0:
                        raise ValueError
                    received_qty += row_qty
                    received_total += row_total
            except (TypeError, ValueError):
                return handler.send_json(422, {"error": "Enter valid received quantities and totals for every row."})
            received_name = actual["name"]
            received_vendor = actual["vendor"]
            cursor.execute(
                "SELECT COALESCE(SUM(received_qty),0) AS received_so_far FROM receieve_product WHERE invoice_number=%s AND order_product=%s",
                (invoice, actual["name"][:255]),
            )
            received_so_far = int(cursor.fetchone()["received_so_far"] or 0)
            remaining = max(0, int(actual["quantity"]) - received_so_far)
            if received_qty <= 0 or received_qty > remaining or received_total < 0:
                return handler.send_json(422, {"error": f"Enter a quantity from 1 to {remaining} for {actual['name']}."})
            receiving = {"name": received_name, "quantity": received_qty, "totalPrice": received_total, "vendor": received_vendor}
            status = "fulfilled" if received_qty == remaining else "pending"
            lines.append({"assetIds": group["assetIds"], "actual": actual, "receiving": receiving, "status": status, "receivedSoFar": received_so_far, "remaining": remaining - received_qty})
        if not lines:
            return handler.send_json(422, {"error": "Enter a received quantity for at least one product."})
        actual_summary = {
            "name": ", ".join(line["actual"]["name"] for line in lines),
            "quantity": sum(line["actual"]["quantity"] for line in lines),
            "totalPrice": sum(line["actual"]["totalPrice"] for line in lines),
            "vendor": ", ".join(dict.fromkeys(line["actual"]["vendor"] for line in lines)),
        }
        receive_summary = {
            "name": ", ".join(line["receiving"]["name"] for line in lines),
            "quantity": sum(line["receiving"]["quantity"] for line in lines),
            "totalPrice": sum(line["receiving"]["totalPrice"] for line in lines),
            "vendor": ", ".join(dict.fromkeys(line["receiving"]["vendor"] for line in lines)),
        }
        status = "fulfilled" if all(line["remaining"] == 0 for line in lines) else "pending"
        cursor.execute("SELECT GET_LOCK('fixed_asset_arrival_id', 10) AS acquired")
        if cursor.fetchone()["acquired"] != 1:
            return handler.send_json(503, {"error": "Could not reserve the next arrival ID. Please try again."})
        cursor.execute("SELECT arrival_id FROM receieve_product WHERE arrival_id LIKE 'ARR-%'")
        arrival_numbers = [int(match.group(1)) for row in cursor.fetchall() if (match := re.fullmatch(r"ARR-(\d+)", str(row["arrival_id"])))]
        arrival_id = f"ARR-{max(arrival_numbers, default=110) + 1}"
        arrival_timestamp = datetime.combine(date.fromisoformat(arrival_date), datetime.min.time())
        for line in lines:
            ordered = line["actual"]
            received = line["receiving"]
            cursor.execute(
                """INSERT INTO receieve_product
                (arrival_id,invoice_number,order_product,uom,asset_category,order_qty,received_qty,received_totalamount,product_remain,status,warehouse,is_active,is_update,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,1,%s)""",
                (
                    arrival_id,
                    invoice,
                    ordered["name"][:255],
                    ordered.get("uom") or "",
                    ordered["category"],
                    ordered["quantity"],
                    received["quantity"],
                    received["totalPrice"],
                    line["remaining"],
                    "fulfilled" if line["remaining"] == 0 else "pending",
                    warehouse_name,
                    arrival_timestamp,
                ),
            )
        connection.commit()
        cursor.execute("SELECT RELEASE_LOCK('fixed_asset_arrival_id')")
        cursor.fetchone()
        return handler.send_json(
            200,
            {
                "message": "Arrival verification saved.",
                "records": arrival_records(cursor, user["user_id"]),
                "receiveProducts": receive_product_records(cursor, user["user_id"]),
            },
        )
    finally:
        connection.close()


@router.delete_prefix("/api/arrivals/")
def handle_arrival_delete(handler, arrival_id: str):
    user = handler.require_asset_user()
    if not user:
        return
    connection = db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        ensure_receive_product_schema(cursor)
        cursor.execute(
            """SELECT r.invoice_number FROM receieve_product r
            WHERE r.arrival_id=%s AND EXISTS (
                SELECT 1 FROM asset_purchase p JOIN asset_basic b ON b.asset_id=p.asset_id
                WHERE p.invoice_number=r.invoice_number AND b.created_by=%s) LIMIT 1""",
            (arrival_id, user["user_id"]),
        )
        record = cursor.fetchone()
        if not record:
            return handler.send_json(404, {"error": "Arrival verification was not found."})
        invoice = record["invoice_number"]
        cursor.execute("DELETE FROM receieve_product WHERE arrival_id=%s", (arrival_id,))
        cursor.execute("SELECT sl_no,order_product,order_qty,received_qty FROM receieve_product WHERE invoice_number=%s ORDER BY sl_no", (invoice,))
        received_by_product = {}
        for product_row in cursor.fetchall():
            product_key = product_row["order_product"]
            cumulative = received_by_product.get(product_key, 0) + int(product_row["received_qty"] or 0)
            received_by_product[product_key] = cumulative
            product_remain = max(0, int(product_row["order_qty"] or 0) - cumulative)
            cursor.execute("UPDATE receieve_product SET product_remain=%s,status=%s WHERE sl_no=%s", (product_remain, "fulfilled" if product_remain == 0 else "pending", product_row["sl_no"]))
        connection.commit()
        return handler.send_json(
            200,
            {
                "message": "Arrival verification deleted.",
                "records": arrival_records(cursor, user["user_id"]),
                "receiveProducts": receive_product_records(cursor, user["user_id"]),
            },
        )
    except mysql.connector.Error as error:
        connection.rollback()
        print(f"Database error on delete arrival: {error}")
        return handler.send_json(500, {"error": "Could not delete the arrival verification."})
    finally:
        connection.close()
