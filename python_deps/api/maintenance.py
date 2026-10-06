"""Maintenance management API handlers."""
from __future__ import annotations

import math
from datetime import date
from urllib.parse import parse_qs, urlsplit

import mysql.connector
from router import router
from db import (
    db_connection,
    ensure_assigned_product_schema,
    ensure_employee_schema,
    ensure_maintenance_schema,
    ensure_overall_place_schema,
    maintenance_date_value,
    maintenance_record,
)


@router.get("/api/maintenance/asset")
def handle_maintenance_asset_lookup(handler):
    user = handler.require_asset_user()
    if not user:
        return
    asset_id = parse_qs(urlsplit(handler.path).query).get("assetId", [""])[0].strip()[:100]
    if not asset_id:
        return handler.send_json(422, {"error": "Enter an asset ID."})
    connection = db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        ensure_employee_schema(cursor)
        ensure_assigned_product_schema(cursor)
        ensure_overall_place_schema(cursor)
        connection.commit()
        cursor.execute(
            """SELECT b.asset_id,b.product_name,e.emp_id,e.emp_name,p.place_name AS place
            FROM asset_basic b JOIN assigned_product a ON a.asset_id=b.asset_id
            LEFT JOIN employee e ON e.emp_id=a.emp_id AND e.created_by=a.assigned_by
            LEFT JOIN overall_place p ON LOWER(TRIM(p.place_name))=LOWER(TRIM(a.place)) AND p.is_active=1
            WHERE b.asset_id=%s AND b.created_by=%s AND b.is_active=1
            AND a.assigned_by=%s AND a.is_active=1
            AND ((a.emp_id IS NOT NULL AND e.emp_id IS NOT NULL) OR (a.place IS NOT NULL AND p.place_name IS NOT NULL))
            ORDER BY a.sl_no DESC LIMIT 1""",
            (asset_id, user["user_id"], user["user_id"]),
        )
        asset = cursor.fetchone()
        if not asset:
            return handler.send_json(404, {"error": "Asset not found or not assigned to an employee or place in your account."})
        return handler.send_json(200, {"asset": {"assetId": asset["asset_id"], "productName": asset["product_name"], "empId": asset["emp_id"], "empName": asset["emp_name"], "place": asset["place"]}})
    except mysql.connector.Error as error:
        print(f"Database error looking up maintenance asset: {error}")
        return handler.send_json(500, {"error": "Could not look up this asset in MySQL."})
    finally:
        connection.close()


@router.get("/api/admin/maintenance")
def handle_admin_maintenance_get(handler):
    user = handler.current_user()
    if not user:
        return handler.send_json(401, {"error": "Sign in required."})
    if user.get("designation") != "admin":
        return handler.send_json(403, {"error": "Administrator access required."})
    connection = db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        ensure_maintenance_schema(cursor)
        connection.commit()
        cursor.execute(
            """SELECT m.sl_no,m.asset_id,m.product_name,m.emp_id,m.place,m.repair_assign_date,m.issue_type,
            m.issue_explain,m.assign_company_name,m.contact_person_name,m.contact_person_number,m.expected_date_of_return,
            m.expected_cost,m.maintenance_status,m.is_active,m.is_update,m.created_at,m.updated_at,
            u.user_name AS asset_owner,u.user_mail AS asset_owner_email
            FROM maintenance m JOIN asset_basic b ON b.asset_id=m.asset_id
            LEFT JOIN user_login u ON u.user_id=b.created_by
            ORDER BY m.sl_no DESC"""
        )
        entries = []
        for row in cursor.fetchall():
            entry = maintenance_record(row)
            entry["assetOwner"] = row.get("asset_owner") or ""
            entry["assetOwnerEmail"] = row.get("asset_owner_email") or ""
            entries.append(entry)
        return handler.send_json(200, {"entries": entries})
    except mysql.connector.Error as error:
        print(f"Database error loading admin maintenance entries: {error}")
        return handler.send_json(500, {"error": "Could not load maintenance entries from MySQL."})
    finally:
        connection.close()


@router.get("/api/maintenance")
def handle_maintenance_get(handler):
    user = handler.require_asset_user()
    if not user:
        return
    connection = db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        ensure_maintenance_schema(cursor)
        connection.commit()
        cursor.execute(
            """SELECT m.sl_no,m.asset_id,m.product_name,m.emp_id,m.place,m.repair_assign_date,m.issue_type,
            m.issue_explain,m.assign_company_name,m.contact_person_name,m.contact_person_number,
            m.expected_date_of_return,m.original_expected_date,m.expected_cost,m.maintenance_status,
            m.done_date,m.is_active,m.is_update,m.created_at,m.updated_at
            FROM maintenance m JOIN asset_basic b ON b.asset_id=m.asset_id
            WHERE b.created_by=%s ORDER BY m.sl_no DESC""",
            (user["user_id"],),
        )
        return handler.send_json(200, {"entries": [maintenance_record(row) for row in cursor.fetchall()]})
    except mysql.connector.Error as error:
        print(f"Database error loading maintenance entries: {error}")
        return handler.send_json(500, {"error": "Could not load maintenance entries from MySQL."})
    finally:
        connection.close()


def update_maintenance_entry(handler, user, data):
    try:
        sl_no = int(data.get("slNo") or 0)
    except (TypeError, ValueError):
        return handler.send_json(422, {"error": "Invalid maintenance entry ID."})
    try:
        repair_date_raw = data.get("repairAssignDate")
        repair_date = maintenance_date_value(repair_date_raw) if repair_date_raw and str(repair_date_raw).strip() else date.today().isoformat()
    except (TypeError, ValueError):
        return handler.send_json(422, {"error": "Enter a valid repair assigned date (YYYY-MM-DD)."})
    try:
        return_date = maintenance_date_value(data.get("expectedDateOfReturn"))
    except (TypeError, ValueError):
        return handler.send_json(422, {"error": "Enter a valid expected date of return (YYYY-MM-DD)."})
    try:
        cost_raw = data.get("expectedCost")
        expected_cost = float(cost_raw) if cost_raw is not None and str(cost_raw).strip() != "" else 0.0
    except (TypeError, ValueError):
        return handler.send_json(422, {"error": "Enter a valid expected cost (number)."})
    issue_type = str(data.get("issueType") or "").strip()[:120]
    issue_explain = str(data.get("issueExplain") or "").strip()
    company = str(data.get("assignCompanyName") or "").strip()[:200]
    contact_name = str(data.get("contactPersonName") or "").strip()[:150]
    contact_number = str(data.get("contactPersonNumber") or "").strip()[:50]
    if (
        sl_no < 1
        or not issue_type
        or not issue_explain
        or not company
        or not contact_name
        or not contact_number
        or len(issue_explain) > 4000
        or not math.isfinite(expected_cost)
        or expected_cost < 0
    ):
        return handler.send_json(422, {"error": "Complete all required maintenance fields with valid values."})
    connection = db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        ensure_maintenance_schema(cursor)
        cursor.execute(
            """SELECT m.sl_no FROM maintenance m JOIN asset_basic b ON b.asset_id=m.asset_id
            WHERE m.sl_no=%s AND b.created_by=%s AND COALESCE(m.is_active,1)!=0 LIMIT 1""",
            (sl_no, user["user_id"]),
        )
        if not cursor.fetchone():
            connection.rollback()
            return handler.send_json(404, {"error": "Maintenance entry was not found in your account."})
        cursor.execute(
            """UPDATE maintenance SET repair_assign_date=%s,issue_type=%s,issue_explain=%s,
            assign_company_name=%s,contact_person_name=%s,contact_person_number=%s,
            expected_date_of_return=%s,expected_cost=%s,is_update=1 WHERE sl_no=%s""",
            (repair_date, issue_type, issue_explain, company, contact_name, contact_number, return_date, expected_cost, sl_no),
        )
        connection.commit()
        cursor.execute(
            """SELECT m.sl_no,m.asset_id,m.product_name,m.emp_id,m.place,m.repair_assign_date,m.issue_type,
            m.issue_explain,m.assign_company_name,m.contact_person_name,m.contact_person_number,
            m.expected_date_of_return,m.original_expected_date,m.expected_cost,m.maintenance_status,
            m.done_date,m.is_active,m.is_update,m.created_at,m.updated_at
            FROM maintenance m JOIN asset_basic b ON b.asset_id=m.asset_id
            WHERE m.sl_no=%s AND b.created_by=%s LIMIT 1""",
            (sl_no, user["user_id"]),
        )
        saved = cursor.fetchone()
        if not saved:
            return handler.send_json(404, {"error": "Updated maintenance entry could not be reloaded."})
        return handler.send_json(200, {"message": "Maintenance entry updated.", "entry": maintenance_record(saved)})
    except mysql.connector.Error as error:
        connection.rollback()
        print(f"Database error updating maintenance: {error}")
        return handler.send_json(500, {"error": "Could not update the maintenance entry in MySQL."})
    finally:
        connection.close()


def update_maintenance_status(handler, user, data):
    try:
        sl_no = int(data.get("slNo") or 0)
        status = str(data.get("maintenanceStatus", "")).strip().lower()
    except (TypeError, ValueError):
        return handler.send_json(422, {"error": "Choose Done, Not done, or Pending for this maintenance entry."})
    if sl_no < 1 or status not in {"done", "not done", "pending"}:
        return handler.send_json(422, {"error": "Choose Done, Not done, or Pending for this maintenance entry."})
    done_date = None
    new_expected_date = None
    if status == "done":
        try:
            raw = data.get("doneDate")
            done_date = maintenance_date_value(raw) if raw and str(raw).strip() else date.today().isoformat()
        except (TypeError, ValueError):
            return handler.send_json(422, {"error": "Enter a valid done date (YYYY-MM-DD)."})
    elif status == "not done":
        try:
            raw = data.get("newExpectedDate")
            if raw and str(raw).strip():
                new_expected_date = maintenance_date_value(raw)
        except (TypeError, ValueError):
            return handler.send_json(422, {"error": "Enter a valid new expected return date (YYYY-MM-DD)."})
    connection = db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        ensure_maintenance_schema(cursor)
        cursor.execute(
            """SELECT m.sl_no, m.expected_date_of_return, m.original_expected_date
            FROM maintenance m JOIN asset_basic b ON b.asset_id=m.asset_id
            WHERE m.sl_no=%s AND b.created_by=%s AND m.is_active=1 LIMIT 1""",
            (sl_no, user["user_id"]),
        )
        existing = cursor.fetchone()
        if not existing:
            connection.rollback()
            return handler.send_json(404, {"error": "Maintenance entry was not found in your account."})
        if status == "done":
            cursor.execute(
                "UPDATE maintenance SET maintenance_status=%s, done_date=%s, is_update=1 WHERE sl_no=%s",
                (status, done_date, sl_no),
            )
        elif status == "not done" and new_expected_date:
            cursor.execute(
                "UPDATE maintenance SET maintenance_status=%s, done_date=NULL, expected_date_of_return=%s, is_update=1 WHERE sl_no=%s",
                (status, new_expected_date, sl_no),
            )
        else:
            cursor.execute(
                "UPDATE maintenance SET maintenance_status=%s, done_date=NULL, is_update=1 WHERE sl_no=%s",
                (status, sl_no),
            )
        connection.commit()
        cursor.execute(
            """SELECT m.sl_no,m.asset_id,m.product_name,m.emp_id,m.place,m.repair_assign_date,m.issue_type,
            m.issue_explain,m.assign_company_name,m.contact_person_name,m.contact_person_number,
            m.expected_date_of_return,m.original_expected_date,m.expected_cost,m.maintenance_status,
            m.done_date,m.is_active,m.is_update,m.created_at,m.updated_at
            FROM maintenance m JOIN asset_basic b ON b.asset_id=m.asset_id
            WHERE m.sl_no=%s AND b.created_by=%s LIMIT 1""",
            (sl_no, user["user_id"]),
        )
        saved = cursor.fetchone()
        if not saved:
            return handler.send_json(404, {"error": "Updated maintenance entry could not be reloaded."})
        return handler.send_json(200, {"message": "Maintenance status updated.", "entry": maintenance_record(saved)})
    except mysql.connector.Error as error:
        connection.rollback()
        print(f"Database error updating maintenance status: {error}")
        return handler.send_json(500, {"error": "Could not update the maintenance status in MySQL."})
    finally:
        connection.close()


@router.post("/api/maintenance/update")
def handle_maintenance_update(handler):
    user = handler.require_asset_user()
    if not user:
        return
    data = handler.read_json()
    return update_maintenance_entry(handler, user, data)


@router.post("/api/maintenance")
def handle_maintenance_post(handler):
    user = handler.require_asset_user()
    if not user:
        return
    data = handler.read_json()
    if data.get("action") == "status":
        return update_maintenance_status(handler, user, data)
    if data.get("action") == "update" or data.get("slNo") is not None:
        return update_maintenance_entry(handler, user, data)
    rows = data.get("entries")
    if not isinstance(rows, list) or not rows or len(rows) > 100:
        return handler.send_json(422, {"error": "Submit between 1 and 100 maintenance rows."})
    normalized = []
    seen_asset_ids = set()
    for entry in rows:
        if not isinstance(entry, dict):
            return handler.send_json(422, {"error": "Enter a valid maintenance row."})
        asset_id = str(entry.get("assetId", "")).strip()[:100]
        issue_type = str(entry.get("issueType", "")).strip()[:120]
        issue_explain = str(entry.get("issueExplain", "")).strip()
        company = str(entry.get("assignCompanyName", "")).strip()[:200]
        contact_name = str(entry.get("contactPersonName", "")).strip()[:150]
        contact_number = str(entry.get("contactPersonNumber", "")).strip()[:50]
        repair_date = maintenance_date_value(entry.get("repairAssignDate"))
        return_date = maintenance_date_value(entry.get("expectedDateOfReturn"))
        try:
            expected_cost = float(entry.get("expectedCost"))
        except (TypeError, ValueError):
            return handler.send_json(422, {"error": "Enter valid repair and expected return dates, and a valid expected cost."})
        if (
            not asset_id
            or not issue_type
            or not issue_explain
            or not company
            or not contact_name
            or not contact_number
            or len(issue_explain) > 4000
            or not math.isfinite(expected_cost)
            or expected_cost < 0
        ):
            return handler.send_json(422, {"error": "Complete all required maintenance fields with valid values."})
        if asset_id in seen_asset_ids:
            return handler.send_json(422, {"error": f"Asset ID {asset_id} appears more than once in this submission."})
        seen_asset_ids.add(asset_id)
        normalized.append({
            "assetId": asset_id,
            "issueType": issue_type,
            "issueExplain": issue_explain,
            "assignCompanyName": company,
            "contactPersonName": contact_name,
            "contactPersonNumber": contact_number,
            "repairAssignDate": repair_date,
            "expectedDateOfReturn": return_date,
            "expectedCost": expected_cost,
        })
    connection = db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        ensure_employee_schema(cursor)
        ensure_assigned_product_schema(cursor)
        ensure_overall_place_schema(cursor)
        ensure_maintenance_schema(cursor)
        for entry in normalized:
            cursor.execute(
                """SELECT b.product_name,e.emp_id,p.place_name AS place
                FROM asset_basic b JOIN assigned_product a ON a.asset_id=b.asset_id
                LEFT JOIN employee e ON e.emp_id=a.emp_id AND e.created_by=a.assigned_by AND e.is_active=1
                LEFT JOIN overall_place p ON LOWER(TRIM(p.place_name))=LOWER(TRIM(a.place)) AND p.is_active=1
                WHERE b.asset_id=%s AND b.created_by=%s AND b.is_active=1
                AND a.assigned_by=%s AND a.is_active=1
                AND ((a.emp_id IS NOT NULL AND e.emp_id IS NOT NULL) OR (a.place IS NOT NULL AND p.place_name IS NOT NULL))
                ORDER BY a.sl_no DESC LIMIT 1""",
                (entry["assetId"], user["user_id"], user["user_id"]),
            )
            assigned = cursor.fetchone()
            if not assigned:
                connection.rollback()
                return handler.send_json(422, {"error": f"Asset {entry['assetId']} is not assigned to an employee or place in your account."})
            entry["productName"] = assigned["product_name"]
            entry["empId"] = assigned["emp_id"]
            entry["place"] = assigned["place"]
        cursor.executemany(
            """INSERT INTO maintenance
            (asset_id,product_name,emp_id,place,repair_assign_date,issue_type,issue_explain,assign_company_name,
             contact_person_name,contact_person_number,expected_date_of_return,original_expected_date,expected_cost,is_active,is_update)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,0)""",
            [
                (
                    e["assetId"],
                    e["productName"],
                    e["empId"],
                    e["place"],
                    e["repairAssignDate"],
                    e["issueType"],
                    e["issueExplain"],
                    e["assignCompanyName"],
                    e["contactPersonName"],
                    e["contactPersonNumber"],
                    e["expectedDateOfReturn"],
                    e["expectedDateOfReturn"],
                    e["expectedCost"],
                )
                for e in normalized
            ],
        )
        connection.commit()
        ids = [e["assetId"] for e in normalized]
        cursor.execute(
            """SELECT m.sl_no,m.asset_id,m.product_name,m.emp_id,m.place,m.repair_assign_date,m.issue_type,
            m.issue_explain,m.assign_company_name,m.contact_person_name,m.contact_person_number,
            m.expected_date_of_return,m.original_expected_date,m.expected_cost,m.maintenance_status,
            m.done_date,m.is_active,m.is_update,m.created_at,m.updated_at
            FROM maintenance m JOIN asset_basic b ON b.asset_id=m.asset_id
            WHERE b.created_by=%s AND m.asset_id IN ("""
            + ",".join(["%s"] * len(ids))
            + ") ORDER BY m.sl_no DESC LIMIT "
            + str(len(ids)),
            (user["user_id"], *ids),
        )
        saved = cursor.fetchall()
        return handler.send_json(201, {"message": "Maintenance entries saved.", "entries": [maintenance_record(row) for row in saved]})
    finally:
        connection.close()
