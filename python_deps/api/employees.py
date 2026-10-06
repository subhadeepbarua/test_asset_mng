"""
Employee API — /api/employees

This file owns and registers all its own routes directly.
backend.py does NOT route employees — this file does it via @router decorators.

Routes registered here:
  GET  /api/employees
  POST /api/employees
"""
from __future__ import annotations

import sys
from pathlib import Path

import mysql.connector

_deps_dir = str(Path(__file__).resolve().parent.parent)
if _deps_dir not in sys.path:
    sys.path.insert(0, _deps_dir)

from router import router
from db import (
    db_connection,
    employee_id_for,
    ensure_employee_schema,
    next_employee_sequence,
)


# ── GET /api/employees ────────────────────────────────────────────────────────

@router.get("/api/employees")
def handle_employees_get(handler):
    """Return all employees for the signed-in user."""
    user = handler.require_asset_user()
    if not user:
        return
    connection = db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        ensure_employee_schema(cursor)
        cursor.execute(
            "SELECT sl_no,emp_id,emp_name,emp_dept,emp_role,created_by,is_update,is_active,created_at,updated_at FROM employee WHERE created_by=%s AND is_active=1 ORDER BY sl_no DESC",
            (user["user_id"],),
        )
        rows = cursor.fetchall()
        next_id = employee_id_for(next_employee_sequence(cursor))
        return handler.send_json(
            200,
            {
                "employees": [
                    {
                        "slNo": row["sl_no"],
                        "empId": row["emp_id"],
                        "empName": row["emp_name"],
                        "empDept": row["emp_dept"],
                        "empRole": row["emp_role"],
                        "createdBy": row["created_by"],
                        "isUpdate": bool(row["is_update"]),
                        "isActive": bool(row["is_active"]),
                        "createdAt": str(row["created_at"] or ""),
                        "updatedAt": str(row["updated_at"] or ""),
                    }
                    for row in rows
                ],
                "nextEmployeeId": next_id,
            },
        )
    except mysql.connector.Error as error:
        print(f"[api/employees] Database error: {error}")
        return handler.send_json(500, {"error": "Could not load employees from MySQL."})
    finally:
        connection.close()


# ── POST /api/employees ───────────────────────────────────────────────────────

@router.post("/api/employees")
def handle_employees_post(handler):
    """Create one or more employee records."""
    user = handler.require_asset_user()
    if not user:
        return
    data = handler.read_json()
    employees = data.get("employees")
    if not isinstance(employees, list) or not employees or len(employees) > 100:
        return handler.send_json(422, {"error": "Submit between 1 and 100 employee rows."})
    normalized = []
    for employee in employees:
        if not isinstance(employee, dict):
            return handler.send_json(422, {"error": "Enter a valid row for each employee."})
        values = {
            "emp_name": str(employee.get("empName", "")).strip(),
            "emp_dept": str(employee.get("empDept", "")).strip(),
            "emp_role": str(employee.get("empRole", "")).strip(),
        }
        limits = {"emp_name": 150, "emp_dept": 150, "emp_role": 150}
        if any(not values[key] or len(values[key]) > limit for key, limit in limits.items()):
            return handler.send_json(422, {"error": "Employee name, department, and role are required and must fit their field limits."})
        normalized.append(values)
    connection = db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        ensure_employee_schema(cursor)
        cursor.execute("SELECT user_id FROM user_login WHERE user_id=%s AND is_active=1 LIMIT 1", (user["user_id"],))
        if not cursor.fetchone():
            connection.rollback()
            return handler.send_json(401, {"error": "The signed-in user could not be found in MySQL. Sign in again."})
        cursor.execute("SELECT GET_LOCK('fixed_asset_employee_id_sequence',10) AS acquired")
        if cursor.fetchone()["acquired"] != 1:
            connection.rollback()
            return handler.send_json(503, {"error": "Could not reserve employee IDs. Please try again."})
        next_number = next_employee_sequence(cursor)
        generated_ids = []
        insert_rows = []
        for employee in normalized:
            employee_id = employee_id_for(next_number)
            generated_ids.append(employee_id)
            insert_rows.append((employee_id, employee["emp_name"], employee["emp_dept"], employee["emp_role"], user["user_id"]))
            next_number += 1
        cursor.executemany("INSERT INTO employee (emp_id,emp_name,emp_dept,emp_role,created_by,is_update,is_active) VALUES (%s,%s,%s,%s,%s,0,1)", insert_rows)
        connection.commit()
        cursor.execute("SELECT RELEASE_LOCK('fixed_asset_employee_id_sequence')")
        cursor.fetchone()
        cursor.execute(
            "SELECT sl_no,emp_id,emp_name,emp_dept,emp_role,created_by,is_update,is_active,created_at,updated_at FROM employee WHERE created_by=%s AND emp_id IN ("
            + ",".join(["%s"] * len(generated_ids))
            + ") ORDER BY sl_no",
            (user["user_id"], *generated_ids),
        )
        rows = cursor.fetchall()
        return handler.send_json(
            201,
            {
                "message": "Employee(s) created successfully.",
                "nextEmployeeId": employee_id_for(next_number),
                "employees": [
                    {
                        "slNo": row["sl_no"],
                        "empId": row["emp_id"],
                        "empName": row["emp_name"],
                        "empDept": row["emp_dept"],
                        "empRole": row["emp_role"],
                        "createdBy": row["created_by"],
                        "isUpdate": bool(row["is_update"]),
                        "isActive": bool(row["is_active"]),
                        "createdAt": str(row["created_at"] or ""),
                        "updatedAt": str(row["updated_at"] or ""),
                    }
                    for row in rows
                ],
            },
        )
    finally:
        connection.close()
