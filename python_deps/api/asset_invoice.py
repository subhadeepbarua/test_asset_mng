"""Invoice API handlers for AssetFlow (create, update, and next-number preview)."""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime

import mysql.connector
from router import router
from db import db_connection, ensure_asset_schema, ensure_purchase_schema, ensure_vendor_schema


@router.get("/api/invoices/next-number")
def handle_next_invoice_number(handler):
    user = handler.require_asset_user()
    if not user:
        return
    stamp = datetime.now().strftime("%Y%m%d")
    connection = db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT DISTINCT invoice_number FROM asset_purchase WHERE invoice_number LIKE %s", (f"INV-{stamp}-%",))
        sequences = [
            int(str(row["invoice_number"]).rsplit("-", 1)[-1])
            for row in cursor.fetchall()
            if str(row["invoice_number"] or "").rsplit("-", 1)[-1].isdigit()
        ]
        return handler.send_json(200, {"invoiceNumber": f"INV-{stamp}-{max(sequences, default=0) + 1}"})
    except mysql.connector.Error as error:
        print(f"Database error generating invoice preview: {error}")
        return handler.send_json(500, {"error": "Could not generate the next invoice number from MySQL."})
    finally:
        connection.close()


@router.post("/api/assets/invoice")
def handle_invoice_post(handler):
    user = handler.require_asset_user()
    if not user:
        return
    data = handler.read_json()
    products = data.get("products")
    ids = handler.asset_ids(data)
    vendor_id = str(data.get("vendorId", "")).strip()
    invoice_number = str(data.get("invoiceNumber", "")).strip()[:120]
    tax_invoice_number = str(data.get("taxInvoiceNumber", "")).strip()[:120]
    purchase_date = str(data.get("purchaseDate", "")).strip()
    if not isinstance(products, list) or not products or not vendor_id or not tax_invoice_number or not purchase_date:
        return handler.send_json(422, {"error": "Supplier, tax invoice number, invoice date, and at least one product are required."})
    product_count = 0
    normalized_products = []
    product_combinations = set()
    for product in products:
        name = str(product.get("productName", "")).strip()
        category = str(product.get("category", "")).strip()
        uom = str(product.get("uom") or "").strip()
        brand = str(product.get("brand") or "").strip()
        model = str(product.get("model") or "").strip()
        assignment = str(product.get("assetAssignment", "individual")).strip().lower()
        try:
            quantity = int(product.get("quantity", 1))
            unit_price = float(product.get("unitPrice", 0))
        except (TypeError, ValueError):
            return handler.send_json(422, {"error": "Enter a valid quantity and rate for every product."})
        if not name or not category or not uom or len(uom) > 50 or assignment not in ("individual", "overall") or quantity < 1 or unit_price < 0:
            return handler.send_json(422, {"error": "Each product needs a name, category, unit of measure, quantity, and rate."})
        combination = tuple(re.sub(r"\s+", " ", value).strip().casefold() for value in (name, category, brand, model))
        if combination in product_combinations:
            return handler.send_json(422, {"error": "This invoice has duplicate product rows with the same product name, category, brand, and model."})
        product_combinations.add(combination)
        product_count += quantity
        normalized_products.append((product, name, category, assignment, quantity, unit_price))
    if product_count > 500:
        return handler.send_json(422, {"error": "An invoice can contain at most 500 assets."})
    try:
        date_value = purchase_date + " 00:00:00"
        date.fromisoformat(purchase_date)
    except ValueError:
        return handler.send_json(422, {"error": "Enter a valid invoice date."})
    method = str(data.get("paymentMethod", "")).strip().lower().replace(" ", "_")
    method = {"credit_card": "card", "debit_card": "card"}.get(method, method)
    if method not in {"cash", "card", "upi", "bank_transfer", "cheque", "other"}:
        method = "other"

    connection = db_connection()
    tax_lock_name = "assetflow_tax_" + hashlib.sha256(tax_invoice_number.casefold().encode("utf-8")).hexdigest()[:50]
    tax_lock_acquired = False
    lock_acquired = False
    try:
        cursor = connection.cursor(dictionary=True)
        ensure_vendor_schema(cursor)
        ensure_asset_schema(cursor)
        ensure_purchase_schema(cursor)
        cursor.execute("SELECT GET_LOCK(%s,10) AS acquired", (tax_lock_name,))
        tax_lock_acquired = bool(cursor.fetchone()["acquired"])
        if not tax_lock_acquired:
            connection.rollback()
            return handler.send_json(503, {"error": "Could not verify the tax invoice number. Please try again."})
        cursor.execute(
            "SELECT invoice_number FROM asset_purchase WHERE tax_invoice_number IS NOT NULL AND TRIM(tax_invoice_number)<>'' AND LOWER(TRIM(tax_invoice_number))=LOWER(TRIM(%s)) LIMIT 1",
            (tax_invoice_number,),
        )
        duplicate_tax_invoice = cursor.fetchone()
        if duplicate_tax_invoice:
            connection.rollback()
            return handler.send_json(409, {"error": "This tax invoice number is already used. Enter a unique tax invoice number."})
        cursor.execute("SELECT GET_LOCK(%s,10) AS acquired", ("assetflow_daily_asset_purchase_seq",))
        lock_acquired = bool(cursor.fetchone()["acquired"])
        if not lock_acquired:
            connection.rollback()
            return handler.send_json(503, {"error": "Could not reserve invoice and asset numbers. Please try again."})
        stamp = datetime.now().strftime("%Y%m%d")
        cursor.execute("SELECT asset_id FROM asset_basic WHERE asset_id LIKE %s", (f"AST-{stamp}-%",))
        asset_numbers = [int(row["asset_id"].rsplit("-", 1)[-1]) for row in cursor.fetchall() if str(row["asset_id"]).rsplit("-", 1)[-1].isdigit()]
        next_asset_number = max(asset_numbers, default=0) + 1
        cursor.execute("SELECT DISTINCT invoice_number FROM asset_purchase WHERE invoice_number LIKE %s", (f"INV-{stamp}-%",))
        invoice_numbers = [int(row["invoice_number"].rsplit("-", 1)[-1]) for row in cursor.fetchall() if str(row["invoice_number"]).rsplit("-", 1)[-1].isdigit()]
        next_invoice_sequence = max(invoice_numbers, default=0) + 1
        invoice_number = f"INV-{stamp}-{next_invoice_sequence}"
        ids = []
        generated_products = []
        for product, name, category, assignment, quantity, unit_price in normalized_products:
            product_ids = [f"AST-{stamp}-{next_asset_number + offset}" for offset in range(quantity)]
            next_asset_number += quantity
            ids.extend(product_ids)
            generated_products.append((product, name, category, assignment, product_ids, quantity, unit_price))
        normalized_products = generated_products
        cursor.execute("SELECT vendor_id,vendor_type,vendor_name FROM vendor_details WHERE vendor_id=%s AND is_active=1 LIMIT 1", (vendor_id,))
        vendor = cursor.fetchone()
        if not vendor:
            connection.rollback()
            return handler.send_json(404, {"error": "The selected supplier could not be found."})

        supplied_vendor = data.get("vendorDetails") if isinstance(data.get("vendorDetails"), dict) else {}
        vendor_values = (
            str(supplied_vendor.get("contactPerson") or supplied_vendor.get("contactPersonName") or vendor["vendor_name"]).strip() or vendor["vendor_name"],
            supplied_vendor.get("contactNumber") or None,
            supplied_vendor.get("email") or supplied_vendor.get("contactEmail") or None,
            supplied_vendor.get("gstNumber") or None,
            supplied_vendor.get("panNumber") or None,
            supplied_vendor.get("address") or None,
            supplied_vendor.get("pincode") or None,
            supplied_vendor.get("district") or None,
            supplied_vendor.get("country") or None,
        )
        for product_index, (product, name, category, assignment, product_ids, quantity, unit_price) in enumerate(normalized_products):
            for index, asset_id in enumerate(product_ids):
                cursor.execute("SELECT created_by FROM asset_basic WHERE asset_id=%s", (asset_id,))
                old = cursor.fetchone()
                if old and old["created_by"] != user["user_id"] and user["designation"] != "admin":
                    connection.rollback()
                    return handler.send_json(409, {"error": "One of these generated asset IDs already belongs to another user. Submit again to generate a fresh ID."})
                uom = str(product.get("uom") or "").strip()
                values = (name, product.get("model") or None, product.get("brand") or None, category, assignment, uom, 1, asset_id)
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

                total_for_product = unit_price
                cursor.execute("SELECT sl_no FROM asset_purchase WHERE asset_id=%s AND invoice_number=%s ORDER BY sl_no DESC LIMIT 1", (asset_id, invoice_number))
                old_purchase = cursor.fetchone()
                if old_purchase:
                    cursor.execute(
                        "UPDATE asset_purchase SET vendor_id=%s,contact_person_name=%s,contact_number=%s,contact_email=%s,gst_number=%s,pan_number=%s,address=%s,pincode=%s,district=%s,country=%s,purchase_date_time=%s,unit_price=%s,pay_method=%s,total_purchase_amount=%s,tax_invoice_number=%s,is_update=1 WHERE asset_id=%s AND invoice_number=%s",
                        (vendor_id, *vendor_values, date_value, unit_price, method, total_for_product, tax_invoice_number, asset_id, invoice_number),
                    )
                else:
                    cursor.execute(
                        "INSERT INTO asset_purchase (asset_id,vendor_id,contact_person_name,contact_number,contact_email,gst_number,pan_number,address,pincode,district,country,purchase_date_time,unit_price,pay_method,total_purchase_amount,residual_val,depricable_val,invoice_number,tax_invoice_number,is_update,is_active) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s,0,1)",
                        (asset_id, vendor_id, *vendor_values, date_value, unit_price, method, total_for_product, unit_price, invoice_number, tax_invoice_number),
                    )

        connection.commit()
        return handler.send_json(201, {"message": "Supplier invoice and all products saved.", "invoiceNumber": invoice_number, "assetIds": ids, "productAssetIds": [entry[4] for entry in normalized_products], "productCount": len(normalized_products)})
    except Exception:
        connection.rollback()
        raise
    finally:
        if tax_lock_acquired:
            try:
                cursor.execute("SELECT RELEASE_LOCK(%s)", (tax_lock_name,))
            except Exception:
                pass
        if lock_acquired:
            try:
                cursor.execute("SELECT RELEASE_LOCK(%s)", ("assetflow_daily_asset_purchase_seq",))
            except Exception:
                pass
        connection.close()


@router.post("/api/assets/invoice/update")
def handle_invoice_update(handler):
    user = handler.require_asset_user()
    if not user:
        return
    data = handler.read_json()
    invoice_number = str(data.get("invoiceNumber", "")).strip()
    tax_invoice_number = str(data.get("taxInvoiceNumber", "")).strip()[:120]
    purchase_date = str(data.get("purchaseDate", "")).strip()
    products = data.get("products")
    requested_delete_ids = {str(value).strip() for value in data.get("deleteAssetIds", []) if str(value).strip()} if isinstance(data.get("deleteAssetIds", []), list) else set()
    vendor_id = str(data.get("vendorId", "")).strip()
    if not invoice_number or not tax_invoice_number or not purchase_date or not vendor_id or not isinstance(products, list) or not products:
        return handler.send_json(422, {"error": "Invoice, supplier, date, and product details are required."})
    try:
        date_value = purchase_date + " 00:00:00"
        date.fromisoformat(purchase_date)
    except ValueError:
        return handler.send_json(422, {"error": "Enter a valid invoice date."})
    method = str(data.get("paymentMethod", "")).strip().lower().replace(" ", "_")
    method = {"credit_card": "card", "debit_card": "card"}.get(method, method)
    if method not in {"cash", "card", "upi", "bank_transfer", "cheque", "other"}:
        method = "other"
    normalized = []
    requested_ids = set()
    requested_total = 0
    for product in products:
        product_ids = [str(value).strip() for value in product.get("assetIds", []) if str(value).strip()]
        name = str(product.get("productName", "")).strip()
        category = str(product.get("category", "")).strip()
        uom = str(product.get("uom") or "").strip()
        try:
            quantity = int(product.get("quantity", len(product_ids) or 1))
            rate = float(product.get("unitPrice", 0))
        except (TypeError, ValueError):
            return handler.send_json(422, {"error": "Enter valid product quantities and rates."})
        if not name or not category or not uom or len(uom) > 50 or quantity < 1 or len(product_ids) > quantity or rate < 0 or requested_ids.intersection(product_ids):
            return handler.send_json(422, {"error": "Product details or asset IDs are invalid."})
        requested_ids.update(product_ids)
        requested_total += quantity
        normalized.append((product, product_ids, quantity, rate))
    if requested_total > 500:
        return handler.send_json(422, {"error": "An invoice can contain at most 500 assets."})

    connection = db_connection()
    lock_acquired = False
    try:
        cursor = connection.cursor(dictionary=True)
        ensure_vendor_schema(cursor)
        ensure_asset_schema(cursor)
        ensure_purchase_schema(cursor)
        cursor.execute("SELECT vendor_id,vendor_type,vendor_name FROM vendor_details WHERE vendor_id=%s AND is_active=1 LIMIT 1", (vendor_id,))
        vendor = cursor.fetchone()
        if not vendor:
            connection.rollback()
            return handler.send_json(404, {"error": "The selected supplier could not be found."})
        cursor.execute(
            "SELECT b.asset_id FROM asset_basic b JOIN asset_purchase p ON p.asset_id=b.asset_id WHERE p.invoice_number=%s AND (b.created_by=%s OR %s='admin')",
            (invoice_number, user["user_id"], user["designation"]),
        )
        invoice_ids = {row["asset_id"] for row in cursor.fetchall()}
        if not invoice_ids:
            connection.rollback()
            return handler.send_json(409, {"error": "The invoice asset list changed. Reload the dashboard and edit again."})
        cursor.execute("SELECT GET_LOCK(%s,10) AS acquired", ("assetflow_daily_asset_purchase_seq",))
        lock_acquired = bool(cursor.fetchone()["acquired"])
        if not lock_acquired:
            connection.rollback()
            return handler.send_json(503, {"error": "Could not reserve asset numbers. Please try again."})
        stamp = datetime.now().strftime("%Y%m%d")
        cursor.execute("SELECT asset_id FROM asset_basic WHERE asset_id LIKE %s", (f"AST-{stamp}-%",))
        asset_numbers = [int(row["asset_id"].rsplit("-", 1)[-1]) for row in cursor.fetchall() if str(row["asset_id"]).rsplit("-", 1)[-1].isdigit()]
        next_asset_number = max(asset_numbers, default=0) + 1
        assigned_products = []
        submitted_ids = set()
        for product, product_ids, quantity, rate in normalized:
            retained_ids = [asset_id for asset_id in product_ids if asset_id in invoice_ids][:quantity]
            for asset_id in product_ids:
                if asset_id in invoice_ids:
                    continue
                cursor.execute("SELECT asset_id FROM asset_basic WHERE asset_id=%s", (asset_id,))
                if cursor.fetchone():
                    connection.rollback()
                    return handler.send_json(409, {"error": "One of the selected asset IDs belongs to a different invoice."})
            while len(retained_ids) < quantity:
                candidate = f"AST-{stamp}-{next_asset_number}"
                next_asset_number += 1
                cursor.execute("SELECT asset_id FROM asset_basic WHERE asset_id=%s", (candidate,))
                if cursor.fetchone():
                    continue
                retained_ids.append(candidate)
            submitted_ids.update(retained_ids)
            assigned_products.append((product, retained_ids, quantity, rate))
        normalized = assigned_products
        removed_ids = (invoice_ids - submitted_ids) | (requested_delete_ids & (invoice_ids - submitted_ids))
        added_ids = submitted_ids - invoice_ids
        for asset_id in added_ids:
            cursor.execute("SELECT asset_id FROM asset_basic WHERE asset_id=%s", (asset_id,))
            if cursor.fetchone():
                connection.rollback()
                return handler.send_json(409, {"error": "A generated asset ID is already in use. Change the quantity and try again."})

        supplied_vendor = data.get("vendorDetails") if isinstance(data.get("vendorDetails"), dict) else {}
        if not supplied_vendor:
            cursor.execute(
                "SELECT contact_person_name,contact_number,contact_email,gst_number,pan_number,address,pincode,district,country FROM asset_purchase WHERE vendor_id=%s ORDER BY sl_no DESC LIMIT 1",
                (vendor_id,),
            )
            saved_vendor = cursor.fetchone() or {}
        else:
            saved_vendor = {}
        vendor_values = (
            str(supplied_vendor.get("contactPerson") or supplied_vendor.get("contactPersonName") or saved_vendor.get("contact_person_name") or vendor["vendor_name"]).strip() or vendor["vendor_name"],
            supplied_vendor.get("contactNumber") or saved_vendor.get("contact_number"),
            supplied_vendor.get("email") or supplied_vendor.get("contactEmail") or saved_vendor.get("contact_email"),
            supplied_vendor.get("gstNumber") or saved_vendor.get("gst_number"),
            supplied_vendor.get("panNumber") or saved_vendor.get("pan_number"),
            supplied_vendor.get("address") or saved_vendor.get("address"),
            supplied_vendor.get("pincode") or saved_vendor.get("pincode"),
            supplied_vendor.get("district") or saved_vendor.get("district"),
            supplied_vendor.get("country") or saved_vendor.get("country"),
        )
        for product_index, (product, product_ids, quantity, rate) in enumerate(normalized):
            name = str(product.get("productName", "")).strip()
            category = str(product.get("category", "")).strip()
            uom = str(product.get("uom") or "").strip()
            for index, asset_id in enumerate(product_ids):
                if asset_id in added_ids:
                    cursor.execute(
                        "INSERT INTO asset_basic (product_name,model,brand,asset_category,asset_assign,uom,quantity,asset_id,created_by,status,is_update,is_active) VALUES (%s,%s,%s,%s,'individual',%s,1,%s,%s,'active',0,1)",
                        (name, product.get("model") or None, product.get("brand") or None, category, uom, asset_id, user["user_id"]),
                    )
                else:
                    cursor.execute(
                        "UPDATE asset_basic SET product_name=%s,brand=%s,model=%s,asset_category=%s,uom=%s,quantity=1,is_update=1 WHERE asset_id=%s AND created_by=%s",
                        (name, product.get("brand") or None, product.get("model") or None, category, uom, asset_id, user["user_id"]),
                    )
                cursor.execute("SELECT sl_no FROM asset_purchase WHERE asset_id=%s AND invoice_number=%s ORDER BY sl_no DESC LIMIT 1", (asset_id, invoice_number))
                existing_purchase = cursor.fetchone()
                row_total = rate
                if existing_purchase:
                    cursor.execute(
                        "UPDATE asset_purchase SET vendor_id=%s,contact_person_name=%s,contact_number=%s,contact_email=%s,gst_number=%s,pan_number=%s,address=%s,pincode=%s,district=%s,country=%s,purchase_date_time=%s,unit_price=%s,pay_method=%s,total_purchase_amount=%s,tax_invoice_number=%s,is_update=1 WHERE asset_id=%s AND invoice_number=%s",
                        (vendor_id, *vendor_values, date_value, rate, method, row_total, tax_invoice_number, asset_id, invoice_number),
                    )
                else:
                    cursor.execute(
                        "INSERT INTO asset_purchase (asset_id,vendor_id,contact_person_name,contact_number,contact_email,gst_number,pan_number,address,pincode,district,country,purchase_date_time,unit_price,pay_method,total_purchase_amount,residual_val,depricable_val,invoice_number,tax_invoice_number,is_update,is_active) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s,0,1)",
                        (asset_id, vendor_id, *vendor_values, date_value, rate, method, row_total, rate, invoice_number, tax_invoice_number),
                    )

        if removed_ids:
            placeholders = ",".join(["%s"] * len(removed_ids))
            removed_values = tuple(removed_ids)
            for table in ("asset_life", "asset_purchase", "asset_basic"):
                cursor.execute(f"DELETE FROM {table} WHERE asset_id IN ({placeholders})", removed_values)
            cursor.execute(f"SELECT asset_id FROM asset_basic WHERE asset_id IN ({placeholders})", removed_values)
            if cursor.fetchall():
                connection.rollback()
                return handler.send_json(500, {"error": "The removed product assets could not be deleted from MySQL."})
        connection.commit()
        return handler.send_json(200, {"message": "Invoice details updated.", "invoiceNumber": invoice_number, "deletedAssetIds": sorted(removed_ids)})
    except Exception:
        connection.rollback()
        raise
    finally:
        if lock_acquired:
            try:
                cursor.execute("SELECT RELEASE_LOCK(%s)", ("assetflow_daily_asset_purchase_seq",))
            except Exception:
                pass
        connection.close()
