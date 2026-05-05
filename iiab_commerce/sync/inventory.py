"""
Inventory sync: Stock Ledger Entry → Bagisto Inventory.

Triggered by Stock Ledger Entry DocEvent on_submit.
Recalculates actual_qty from Bin and pushes to Bagisto.
"""
import frappe
from iiab_commerce.sync import client
from iiab_commerce.sync.utils import log_sync


def push_stock(doc, method=None):
    """Push stock update to Bagisto. Runs in background."""
    if isinstance(doc, str):
        doc = frappe.get_doc("Stock Ledger Entry", doc)

    frappe.enqueue(
        _do_push_stock,
        queue="default",
        timeout=60,
        item_code=doc.item_code,
    )


def _do_push_stock(item_code):
    """Background job: push current stock for an item to Bagisto."""
    try:
        # Get actual qty from ERPNext Bin (aggregated across warehouses)
        result = frappe.db.sql("""
            SELECT COALESCE(SUM(actual_qty), 0)
            FROM `tabBin`
            WHERE item_code = %s
        """, item_code)
        total_qty = int(result[0][0]) if result else 0

        # Check if the item has ANY stock entries at all
        has_stock_entries = frappe.db.sql(
            "SELECT 1 FROM `tabBin` WHERE item_code = %s LIMIT 1",
            item_code
        )

        if not has_stock_entries:
            # No stock tracking configured — default to available (999)
            # so storefronts don't show "Out of Stock" for new items.
            # Once the user makes a stock entry, real qty takes over.
            total_qty = 999
        else:
            total_qty = max(0, total_qty)  # No negative stock in Bagisto

        existing = client.find_product_by_sku(item_code)
        if not existing:
            frappe.logger("iiab_commerce").warning(
                f"Inventory push skipped: product {item_code} not found in Bagisto"
            )
            return

        client.update_inventory(existing["id"], total_qty)

        log_sync(
            sync_type="inventory",
            direction="push",
            item_code=item_code,
            status="success",
            payload={"qty": total_qty, "product_id": existing["id"]},
        )
        frappe.logger("iiab_commerce").info(
            f"Inventory updated: {item_code} → qty={total_qty}"
        )

    except Exception as e:
        log_sync(
            sync_type="inventory",
            direction="push",
            item_code=item_code,
            status="failed",
            error=str(e),
        )
        frappe.logger("iiab_commerce").error(
            f"Inventory push failed for {item_code}: {e}"
        )


def push_stock_for_all():
    """Push stock for all published Website Items. Used by reconciliation."""
    items = frappe.get_all(
        "Website Item",
        filters={"published": 1},
        fields=["item_code"],
    )
    for item in items:
        _do_push_stock(item.item_code)
