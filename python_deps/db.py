"""Database connection and schema management for AssetFlow."""
from __future__ import annotations

import os
import re
from datetime import date, datetime
import mysql.connector

DB_CONFIG = {
    "host": os.environ.get("ASSETFLOW_DB_HOST", "193.203.184.53"),
    "user": os.environ.get("ASSETFLOW_DB_USER", "u114727550_asset_mng"),
    "password": os.environ.get("ASSETFLOW_DB_PASSWORD", "Sbe@123!"),
    "database": os.environ.get("ASSETFLOW_DB_NAME", "u114727550_asset_mng"),
    "charset": "utf8mb4",
    "connection_timeout": 10,
}


def db_connection():
    return mysql.connector.connect(**DB_CONFIG)


def ensure_employee_schema(cursor):
    """Create the employee register in the active fixed_asset database."""
    cursor.execute("""CREATE TABLE IF NOT EXISTS employee (
        sl_no INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        emp_id VARCHAR(100) NOT NULL,
        emp_name VARCHAR(150) NOT NULL,
        emp_dept VARCHAR(150) NOT NULL,
        emp_role VARCHAR(150) NOT NULL,
        created_by VARCHAR(64) NOT NULL,
        is_update TINYINT(1) NOT NULL DEFAULT 0,
        is_active TINYINT(1) NOT NULL DEFAULT 1,
        created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_employee_creator_emp_id (created_by,emp_id),
        KEY idx_employee_created_by (created_by)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
    cursor.execute("SHOW COLUMNS FROM employee")
    columns = {row["Field"] for row in cursor.fetchall()}
    if "sl_no" not in columns:
        cursor.execute("ALTER TABLE employee ADD COLUMN sl_no INT NOT NULL AUTO_INCREMENT UNIQUE FIRST")
    additions = {
        "emp_id": "VARCHAR(100) NULL",
        "emp_name": "VARCHAR(150) NULL",
        "emp_dept": "VARCHAR(150) NULL",
        "emp_role": "VARCHAR(150) NULL",
        "created_by": "VARCHAR(64) NULL",
        "is_update": "TINYINT(1) NOT NULL DEFAULT 0",
        "is_active": "TINYINT(1) NOT NULL DEFAULT 1",
        "created_at": "TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP",
        "updated_at": "TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
    }
    for column, definition in additions.items():
        if column not in columns:
            cursor.execute(f"ALTER TABLE employee ADD COLUMN {column} {definition}")


def next_employee_sequence(cursor):
    cursor.execute("SELECT emp_id FROM employee")
    numbers = [int(match.group(1)) for row in cursor.fetchall()
               if (match := re.fullmatch(r"EMP-(\d+)", str(row["emp_id"] or ""), re.IGNORECASE))]
    return max(numbers, default=0) + 1


def employee_id_for(number):
    return "EMP-" + str(number).zfill(3)


def ensure_overall_place_schema(cursor):
    cursor.execute("""CREATE TABLE IF NOT EXISTS overall_place (
        sl_no INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        place_name VARCHAR(150) NOT NULL,
        address VARCHAR(255) NOT NULL,
        is_active TINYINT(1) NOT NULL DEFAULT 1,
        is_update TINYINT(1) NOT NULL DEFAULT 0,
        created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_overall_place_name (place_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
    cursor.execute("SHOW COLUMNS FROM overall_place")
    columns = {row["Field"] for row in cursor.fetchall()}
    additions = {
        "sl_no": "INT NOT NULL AUTO_INCREMENT UNIQUE",
        "place_name": "VARCHAR(150) NOT NULL DEFAULT ''",
        "address": "VARCHAR(255) NOT NULL DEFAULT ''",
        "is_active": "TINYINT(1) NOT NULL DEFAULT 1",
        "is_update": "TINYINT(1) NOT NULL DEFAULT 0",
        "created_at": "TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP",
        "updated_at": "TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
    }
    for column, definition in additions.items():
        if column not in columns:
            cursor.execute(f"ALTER TABLE overall_place ADD COLUMN {column} {definition}")
    test_places = [(f"Test Place {number:02d}", f"Test address {number:02d}, Demo Street") for number in range(1, 11)]
    for place_name, address in test_places:
        cursor.execute("SELECT sl_no FROM overall_place WHERE LOWER(TRIM(place_name))=LOWER(TRIM(%s)) LIMIT 1", (place_name,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO overall_place (place_name,address,is_active,is_update) VALUES (%s,%s,1,0)", (place_name, address))


def ensure_assigned_product_schema(cursor):
    cursor.execute("""CREATE TABLE IF NOT EXISTS assigned_product (
        sl_no INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        asset_id VARCHAR(100) NOT NULL,
        assign_quantity INT NOT NULL,
        invoice_number VARCHAR(120) NOT NULL,
        product_name VARCHAR(255) NOT NULL,
        emp_id VARCHAR(100) NULL,
        asset_assign VARCHAR(20) NOT NULL DEFAULT 'individual',
        place VARCHAR(150) NULL,
        assigned_by VARCHAR(64) NOT NULL,
        is_update TINYINT(1) NOT NULL DEFAULT 0,
        is_active TINYINT(1) NOT NULL DEFAULT 1,
        created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        KEY idx_assigned_product_by (assigned_by),
        KEY idx_assigned_product_invoice_name (invoice_number,product_name),
        KEY idx_assigned_product_emp (emp_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
    cursor.execute("SHOW COLUMNS FROM assigned_product")
    columns = {row["Field"] for row in cursor.fetchall()}
    if "sl_no" not in columns:
        cursor.execute("ALTER TABLE assigned_product ADD COLUMN sl_no INT NOT NULL AUTO_INCREMENT UNIQUE FIRST")


def ensure_maintenance_schema(cursor):
    cursor.execute("""CREATE TABLE IF NOT EXISTS maintenance (
        sl_no INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        asset_id VARCHAR(100) NOT NULL,
        product_name VARCHAR(255) NOT NULL,
        emp_id VARCHAR(100) NULL,
        place VARCHAR(150) NULL,
        repair_assign_date DATE NOT NULL,
        issue_type VARCHAR(120) NOT NULL,
        issue_explain TEXT NOT NULL,
        assign_company_name VARCHAR(200) NOT NULL,
        contact_person_name VARCHAR(150) NOT NULL,
        contact_person_number VARCHAR(50) NOT NULL,
        expected_date_of_return DATE NOT NULL,
        original_expected_date DATE NULL,
        expected_cost DECIMAL(14,2) NOT NULL DEFAULT 0,
        maintenance_status VARCHAR(20) NOT NULL DEFAULT 'pending',
        done_date DATE NULL,
        is_active TINYINT(1) NOT NULL DEFAULT 1,
        is_update TINYINT(1) NOT NULL DEFAULT 0,
        created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        KEY idx_maintenance_asset_active (asset_id,is_active),
        KEY idx_maintenance_emp (emp_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
    cursor.execute("SHOW COLUMNS FROM maintenance")
    columns = {row["Field"] for row in cursor.fetchall()}
    if "maintenance_status" not in columns:
        cursor.execute("ALTER TABLE maintenance ADD COLUMN maintenance_status VARCHAR(20) NOT NULL DEFAULT 'pending' AFTER expected_cost")
    if "done_date" not in columns:
        cursor.execute("ALTER TABLE maintenance ADD COLUMN done_date DATE NULL AFTER maintenance_status")
    if "original_expected_date" not in columns:
        cursor.execute("ALTER TABLE maintenance ADD COLUMN original_expected_date DATE NULL AFTER expected_date_of_return")
        cursor.execute("UPDATE maintenance SET original_expected_date=expected_date_of_return WHERE original_expected_date IS NULL")


def maintenance_date_value(value):
    """Normalize dates from date inputs and older stored timestamp formats."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if not text:
        raise ValueError("A maintenance date is required.")
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        for date_format in ("%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(text, date_format).date().isoformat()
            except ValueError:
                continue
    raise ValueError("Enter a valid maintenance date.")


def maintenance_record(row):
    return {
        "slNo": row["sl_no"],
        "assetId": row["asset_id"],
        "productName": row["product_name"],
        "empId": row["emp_id"],
        "place": row["place"],
        "repairAssignDate": str(row["repair_assign_date"] or ""),
        "issueType": row["issue_type"],
        "issueExplain": row["issue_explain"],
        "assignCompanyName": row["assign_company_name"],
        "contactPersonName": row["contact_person_name"],
        "contactPersonNumber": row["contact_person_number"],
        "expectedDateOfReturn": str(row["expected_date_of_return"] or ""),
        "originalExpectedDate": str(row.get("original_expected_date") or row["expected_date_of_return"] or ""),
        "doneDate": str(row.get("done_date") or ""),
        "expectedCost": float(row["expected_cost"] or 0),
        "maintenanceStatus": row.get("maintenance_status") or "pending",
        "isActive": bool(row["is_active"]),
        "isUpdate": bool(row["is_update"]),
        "createdAt": str(row["created_at"] or ""),
        "updatedAt": str(row["updated_at"] or ""),
    }


def ensure_vendor_schema(cursor):
    """Keep vendor identity normalized and migrate per-asset vendor snapshots to asset_purchase."""
    cursor.execute("SHOW TABLES LIKE 'vendor_details'")
    normalized_exists = cursor.fetchone() is not None
    cursor.execute("SHOW TABLES LIKE 'vendor_detials'")
    legacy_exists = cursor.fetchone() is not None
    if not normalized_exists and legacy_exists:
        cursor.execute("RENAME TABLE vendor_detials TO vendor_details")
        normalized_exists = True
        legacy_exists = False
    if not normalized_exists:
        cursor.execute("""CREATE TABLE vendor_details (
            sl_no INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            vendor_id VARCHAR(50) NOT NULL,
            vendor_type VARCHAR(100) NOT NULL,
            vendor_name VARCHAR(150) NOT NULL,
            created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            is_active TINYINT(1) NOT NULL DEFAULT 1,
            is_update TINYINT(1) NOT NULL DEFAULT 0,
            UNIQUE KEY unique_vendor_id (vendor_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")

    cursor.execute("SHOW COLUMNS FROM vendor_details")
    columns = {row["Field"]: row for row in cursor.fetchall()}
    identity_columns = {
        "sl_no": "INT NOT NULL AUTO_INCREMENT PRIMARY KEY FIRST",
        "vendor_id": "VARCHAR(50) NOT NULL",
        "vendor_type": "VARCHAR(100) NOT NULL DEFAULT 'Supplier'",
        "vendor_name": "VARCHAR(150) NOT NULL DEFAULT ''",
        "created_at": "TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP",
        "updated_at": "TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
        "is_active": "TINYINT(1) NOT NULL DEFAULT 1",
        "is_update": "TINYINT(1) NOT NULL DEFAULT 0",
    }
    for column, definition in identity_columns.items():
        if column not in columns:
            cursor.execute(f"ALTER TABLE vendor_details ADD COLUMN {column} {definition}")
    ordered_columns = list(columns)
    if "is_update" in ordered_columns and "is_active" in ordered_columns and ordered_columns.index("is_active") > ordered_columns.index("is_update"):
        cursor.execute("ALTER TABLE vendor_details MODIFY COLUMN is_active TINYINT(1) NOT NULL DEFAULT 1 AFTER updated_at, MODIFY COLUMN is_update TINYINT(1) NOT NULL DEFAULT 0 AFTER is_active")

    cursor.execute("SHOW COLUMNS FROM asset_purchase")
    purchase_columns = {row["Field"] for row in cursor.fetchall()}
    purchase_additions = {
        "vendor_id": "VARCHAR(50) NULL",
        "contact_person_name": "VARCHAR(150) NULL",
        "contact_number": "VARCHAR(20) NULL",
        "contact_email": "VARCHAR(150) NULL",
        "gst_number": "VARCHAR(64) NULL",
        "pan_number": "VARCHAR(32) NULL",
        "address": "TEXT NULL",
        "pincode": "VARCHAR(20) NULL",
        "district": "VARCHAR(120) NULL",
        "country": "VARCHAR(120) NULL",
    }
    for column, definition in purchase_additions.items():
        if column not in purchase_columns:
            cursor.execute(f"ALTER TABLE asset_purchase ADD COLUMN {column} {definition}")

    cursor.execute("SHOW COLUMNS FROM vendor_details")
    columns = {row["Field"] for row in cursor.fetchall()}
    legacy_fields = {"asset_id", "vendor_master_id", "contact_person_name", "contact_number", "contact_email", "gst_number", "pan_number", "address", "pincode", "district", "country"}
    if legacy_fields.issubset(columns):
        cursor.execute("""UPDATE asset_purchase p
            JOIN vendor_details a ON a.asset_id=p.asset_id
            LEFT JOIN vendor_details m ON m.vendor_id=COALESCE(NULLIF(a.vendor_master_id,''),a.vendor_id)
            SET p.vendor_id=COALESCE(NULLIF(a.vendor_master_id,''),a.vendor_id),
                p.contact_person_name=COALESCE(a.contact_person_name,m.contact_person_name),
                p.contact_number=COALESCE(a.contact_number,m.contact_number),
                p.contact_email=COALESCE(a.contact_email,m.contact_email),
                p.gst_number=COALESCE(a.gst_number,m.gst_number),
                p.pan_number=COALESCE(a.pan_number,m.pan_number),
                p.address=COALESCE(a.address,m.address),
                p.pincode=COALESCE(a.pincode,m.pincode),
                p.district=COALESCE(a.district,m.district),
                p.country=COALESCE(a.country,m.country)""")
        cursor.execute("DELETE FROM vendor_details WHERE asset_id IS NOT NULL OR vendor_master_id IS NOT NULL")
        cursor.execute("""SELECT DISTINCT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='vendor_details' AND COLUMN_NAME='asset_id' AND REFERENCED_TABLE_NAME IS NOT NULL""")
        for row in cursor.fetchall():
            cursor.execute(f"ALTER TABLE vendor_details DROP FOREIGN KEY `{row['CONSTRAINT_NAME']}`")
        for column in ("asset_id", "vendor_master_id", "contact_person_name", "contact_number", "contact_email", "gst_number", "pan_number", "address", "pincode", "district", "country"):
            cursor.execute(f"ALTER TABLE vendor_details DROP COLUMN {column}")

    cursor.execute("SHOW INDEX FROM asset_purchase")
    indexes = cursor.fetchall()
    if not any(row["Column_name"] == "vendor_id" for row in indexes):
        cursor.execute("CREATE INDEX idx_asset_purchase_vendor_id ON asset_purchase (vendor_id)")
    cursor.execute("""SELECT COUNT(*) AS total FROM information_schema.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='asset_purchase' AND COLUMN_NAME='vendor_id' AND REFERENCED_TABLE_NAME IS NOT NULL""")
    if cursor.fetchone()["total"] == 0:
        cursor.execute("ALTER TABLE asset_purchase ADD CONSTRAINT fk_asset_purchase_vendor FOREIGN KEY (vendor_id) REFERENCES vendor_details (vendor_id) ON UPDATE CASCADE ON DELETE RESTRICT")


def ensure_receive_product_schema(cursor):
    """Keep the existing misspelled legacy table name used by the application database."""
    cursor.execute("SHOW TABLES LIKE 'receieve_product'")
    if cursor.fetchone() is None:
        cursor.execute("""CREATE TABLE receieve_product (
            sl_no INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            arrival_id VARCHAR(50) NULL,
            invoice_number VARCHAR(100) NULL,
            order_product VARCHAR(255) NULL,
            order_qty INT NULL,
            received_qty INT NULL DEFAULT 0,
            received_totalamount DECIMAL(15,2) NULL DEFAULT 0.00,
            product_remain INT NULL DEFAULT 0,
            status VARCHAR(20) NULL,
            is_active TINYINT(1) NULL DEFAULT 1,
            is_update TINYINT(1) NULL DEFAULT 0,
            created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
    cursor.execute("SHOW COLUMNS FROM receieve_product")
    columns = {row["Field"] for row in cursor.fetchall()}
    if "arrival_id" not in columns:
        cursor.execute("ALTER TABLE receieve_product ADD COLUMN arrival_id VARCHAR(50) NULL AFTER sl_no")
    if "status" not in columns:
        cursor.execute("ALTER TABLE receieve_product ADD COLUMN status VARCHAR(20) NULL AFTER product_remain")
    if "warehouse" not in columns:
        cursor.execute("ALTER TABLE receieve_product ADD COLUMN warehouse VARCHAR(150) NULL AFTER status")
    if "asset_category" not in columns:
        cursor.execute("ALTER TABLE receieve_product ADD COLUMN asset_category VARCHAR(150) NULL AFTER order_product")
    if "uom" not in columns:
        cursor.execute("ALTER TABLE receieve_product ADD COLUMN uom VARCHAR(50) NULL DEFAULT '' AFTER order_product")

    cursor.execute("SHOW TABLES LIKE 'asset_arrival_verification'")
    if cursor.fetchone() is not None:
        cursor.execute("DROP TABLE asset_arrival_verification")
    cursor.execute("SHOW COLUMNS FROM receieve_product")
    columns = {row["Field"] for row in cursor.fetchall()}
    metadata_columns = {"arrival_user_id", "arrival_date", "arrival_data", "arrival_status", "actual_json", "receiving_json", "lines_json", "verified_at"}
    present_metadata = sorted(columns & metadata_columns)
    if "arrival_date" in present_metadata:
        cursor.execute("UPDATE receieve_product SET created_at=TIMESTAMP(arrival_date) WHERE arrival_date IS NOT NULL")
    if present_metadata:
        cursor.execute("ALTER TABLE receieve_product DROP COLUMN " + ", DROP COLUMN ".join(present_metadata))

    cursor.execute("SELECT arrival_id FROM receieve_product WHERE arrival_id IS NOT NULL GROUP BY arrival_id ORDER BY MIN(sl_no)")
    existing_ids = [row["arrival_id"] for row in cursor.fetchall()]
    numeric_ids = [int(match.group(1)) for value in existing_ids
                   if (match := re.fullmatch(r"ARR-(\d+)", str(value)))]
    next_number = 110
    if numeric_ids and min(numeric_ids) >= 111:
        next_number = max(numeric_ids)
    else:
        low_ids = sorted(numeric_ids)
        for index, old_number in enumerate(low_ids, start=1):
            cursor.execute("UPDATE receieve_product SET arrival_id=%s WHERE arrival_id=%s",
                           (f"ARR-TMP-{index}", f"ARR-{old_number}"))
        for index in range(1, len(low_ids) + 1):
            cursor.execute("UPDATE receieve_product SET arrival_id=%s WHERE arrival_id=%s",
                           (f"ARR-{110 + index}", f"ARR-TMP-{index}"))
            next_number = 110 + index
    legacy_ids = [value for value in existing_ids
                  if not re.fullmatch(r"ARR-\d+", str(value))]
    for old_id in legacy_ids:
        next_number += 1
        cursor.execute("UPDATE receieve_product SET arrival_id=%s WHERE arrival_id=%s",
                       (f"ARR-{next_number}", old_id))


def ensure_warehouse_schema(cursor):
    cursor.execute("""CREATE TABLE IF NOT EXISTS warehouse (
        warehouse_id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        warehouse_name VARCHAR(150) NOT NULL,
        address TEXT NOT NULL,
        pincode VARCHAR(20) NOT NULL,
        city VARCHAR(120) NOT NULL,
        district VARCHAR(120) NOT NULL,
        country VARCHAR(120) NOT NULL,
        state VARCHAR(120) NOT NULL,
        user_id VARCHAR(100) NOT NULL,
        created_by VARCHAR(100) NULL,
        created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        is_active TINYINT(1) NOT NULL DEFAULT 1,
        UNIQUE KEY uq_warehouse_name (warehouse_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
    cursor.execute("SHOW COLUMNS FROM warehouse")
    warehouse_column_rows = cursor.fetchall()
    columns = {row["Field"] for row in warehouse_column_rows}
    if "warehouse_id" not in columns and "sl_no" not in columns:
        raise RuntimeError("Existing warehouse table has no supported identifier column.")
    additions = {
        "warehouse_name": "VARCHAR(150) NULL",
        "address": "TEXT NULL",
        "pincode": "VARCHAR(20) NULL",
        "city": "VARCHAR(120) NULL",
        "district": "VARCHAR(120) NOT NULL DEFAULT ''",
        "country": "VARCHAR(120) NULL",
        "state": "VARCHAR(120) NULL",
        "user_id": "VARCHAR(100) NULL",
        "created_by": "VARCHAR(100) NULL",
        "created_at": "TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP",
        "updated_at": "TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
        "is_active": "TINYINT(1) NOT NULL DEFAULT 1",
    }
    for column, definition in additions.items():
        if column not in columns:
            cursor.execute(f"ALTER TABLE warehouse ADD COLUMN {column} {definition}")
            columns.add(column)
    created_by_type = next((str(row["Type"]).lower() for row in warehouse_column_rows if row["Field"] == "created_by"), "")
    if "created_by" in columns and ("int" in created_by_type):
        cursor.execute("UPDATE warehouse w JOIN user_login u ON u.sl_no=w.created_by SET w.user_id=u.user_id WHERE w.user_id IS NULL OR w.user_id='' OR w.user_id REGEXP '^[0-9]+$'")
    elif "created_by" in columns:
        cursor.execute("UPDATE warehouse SET user_id=created_by WHERE (user_id IS NULL OR user_id='') AND created_by IS NOT NULL")


def warehouse_identifier_column(cursor):
    cursor.execute("SHOW COLUMNS FROM warehouse")
    columns = {row["Field"] for row in cursor.fetchall()}
    if "warehouse_id" in columns:
        return "warehouse_id"
    if "sl_no" in columns:
        return "sl_no"
    raise RuntimeError("Warehouse table has no identifier column.")


def warehouse_records(cursor, active_only=True):
    where = " WHERE is_active=1" if active_only else ""
    identifier = warehouse_identifier_column(cursor)
    cursor.execute(f"SELECT {identifier} AS warehouse_id,warehouse_name,address,pincode,city,district,country,state,user_id,created_at FROM warehouse" + where + " ORDER BY warehouse_name")
    return [{"warehouseId": row["warehouse_id"], "warehouseName": row["warehouse_name"], "address": row["address"],
        "pincode": row["pincode"], "city": row["city"], "district": row["district"], "country": row["country"],
        "state": row["state"], "userId": row["user_id"], "createdAt": str(row["created_at"] or "")} for row in cursor.fetchall()]


def receive_product_records(cursor, user_id):
    query = """SELECT r.sl_no,r.arrival_id,r.invoice_number,r.order_product,r.uom,r.order_qty,r.received_qty,
        r.received_totalamount,r.product_remain,r.status,r.warehouse FROM receieve_product r"""
    params = ()
    if user_id:
        query += """ WHERE EXISTS (SELECT 1 FROM asset_purchase p JOIN asset_basic b ON b.asset_id=p.asset_id
            WHERE p.invoice_number=r.invoice_number AND b.created_by=%s)"""
        params = (user_id,)
    cursor.execute(query + " ORDER BY r.invoice_number,r.sl_no", params)
    return [{"slNo": row["sl_no"], "arrivalId": row["arrival_id"], "invoiceNumber": row["invoice_number"], "orderProduct": row["order_product"], "uom": row.get("uom") or "",
        "orderQty": int(row["order_qty"] or 0), "receivedQty": int(row["received_qty"] or 0),
        "receivedTotalAmount": float(row["received_totalamount"] or 0), "productRemain": int(row["product_remain"] or 0), "status": row["status"], "warehouse": row["warehouse"]}
        for row in cursor.fetchall()]


def ensure_purchase_schema(cursor):
    """Use the invoice number as the sole purchase reference in the existing table."""
    cursor.execute("SHOW COLUMNS FROM asset_purchase")
    columns = {str(row["Field"]).lower() for row in cursor.fetchall()}
    for column in ("invoice_number", "tax_invoice_number", "uom"):
        if column not in columns:
            cursor.execute(f"ALTER TABLE asset_purchase ADD COLUMN {column} VARCHAR(120) NULL")
    if "purchase_id" in columns:
        cursor.execute("UPDATE asset_purchase SET invoice_number=CONCAT('INV-',SUBSTRING(purchase_id,5,8),'-',SUBSTRING_INDEX(SUBSTRING(purchase_id,14),'-',1)) WHERE (invoice_number IS NULL OR invoice_number='' OR invoice_number=purchase_id) AND purchase_id REGEXP '^PUR-[0-9]{8}-[0-9]+(-[[:xdigit:]]+)?$'")
        cursor.execute("UPDATE asset_purchase SET invoice_number=CONCAT('INV-',SUBSTRING(purchase_id,5,8),'-',SUBSTRING_INDEX(SUBSTRING(purchase_id,14),'-',1)) WHERE invoice_number REGEXP '^INV-[0-9]{8}-[[:xdigit:]]{8}$' AND purchase_id REGEXP '^PUR-[0-9]{8}-[0-9]+-[[:xdigit:]]+$'")
        cursor.execute("SHOW INDEX FROM asset_purchase")
        indexes = {}
        for row in cursor.fetchall():
            indexes.setdefault(row["Key_name"], []).append(row)
        for name, rows in indexes.items():
            if name != "PRIMARY" and any(row["Column_name"] == "purchase_id" for row in rows):
                cursor.execute(f"ALTER TABLE asset_purchase DROP INDEX `{name}`")
        cursor.execute("ALTER TABLE asset_purchase DROP COLUMN purchase_id")


def ensure_asset_schema(cursor):
    """Keep the asset table compatible with current product-entry fields."""
    cursor.execute("SHOW COLUMNS FROM asset_basic")
    column_rows = cursor.fetchall()
    columns = {row["Field"] for row in column_rows}
    if "product_group_id" in columns:
        cursor.execute("ALTER TABLE asset_basic DROP COLUMN product_group_id")
    if "uom" not in columns:
        cursor.execute("ALTER TABLE asset_basic ADD COLUMN uom VARCHAR(50) NOT NULL DEFAULT '' AFTER asset_assign")
        uom_column = None
    else:
        uom_column = next(row for row in column_rows if row["Field"] == "uom")
    if uom_column is None or uom_column.get("Default") is not None:
        cursor.execute("ALTER TABLE asset_basic MODIFY COLUMN uom VARCHAR(50) NOT NULL AFTER asset_assign")
