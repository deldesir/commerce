"""
Price sync: Item Price → Bagisto Product Price.

Triggered by Item Price DocEvent on_update.
Only syncs prices from the "Standard Selling" price list.
"""
import frappe
from iiab_commerce.sync import client
from iiab_commerce.sync.utils import log_sync

# Only sync from selling price lists
SELLING_PRICE_LISTS = ["Standard Selling", "Standard Selling (HTG)"]


def push_price(doc, method=None):
    """Push an Item Price to Bagisto. Runs in background."""
    if isinstance(doc, str):
        doc = frappe.get_doc("Item Price", doc)

    # Only sync selling price list entries
    if doc.price_list not in SELLING_PRICE_LISTS:
        return

    frappe.enqueue(
        _do_push_price,
        queue="default",
        timeout=60,
        item_code=doc.item_code,
        price_rate=float(doc.price_list_rate or 0),
    )


def _do_push_price(item_code, price_rate):
    """Background job: update a product's price in Bagisto."""
    try:
        existing = client.find_product_by_sku(item_code)
        if not existing:
            frappe.logger("iiab_commerce").warning(
                f"Price push skipped: product {item_code} not found in Bagisto"
            )
            return

        client.update_product_price(existing["id"], price_rate)

        log_sync(
            sync_type="price",
            direction="push",
            item_code=item_code,
            status="success",
            payload={"price": price_rate, "product_id": existing["id"]},
        )
        frappe.logger("iiab_commerce").info(
            f"Price updated: {item_code} → {price_rate} HTG"
        )

    except Exception as e:
        log_sync(
            sync_type="price",
            direction="push",
            item_code=item_code,
            status="failed",
            error=str(e),
        )
        frappe.logger("iiab_commerce").error(
            f"Price push failed for {item_code}: {e}"
        )
