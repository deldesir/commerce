"""
Sync utilities: logging and shared helpers.
"""
import frappe
import json


def log_sync(sync_type, direction, item_code="", status="success",
             payload=None, response=None, error=""):
    """Create a Bagisto Sync Log entry.

    Falls back to frappe.logger if the doctype doesn't exist yet
    (e.g. during initial development before migrations).
    """
    try:
        doc = frappe.get_doc({
            "doctype": "Bagisto Sync Log",
            "sync_type": sync_type,
            "direction": direction,
            "item_code": item_code,
            "status": status,
            "payload": json.dumps(payload, default=str) if payload else "",
            "response": json.dumps(response, default=str) if response else "",
            "error": error or "",
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        # Doctype may not exist yet — fall back to logger
        frappe.logger("iiab_commerce").info(
            f"SYNC_LOG: {sync_type} {direction} {item_code} {status} {error}"
        )
