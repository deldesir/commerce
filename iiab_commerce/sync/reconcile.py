"""
Nightly reconciliation: full sync of all published Website Items.

Runs via Frappe scheduler (daily) or manually via:
  bench execute iiab_commerce.sync.reconcile.full_sync
"""
import frappe
from iiab_commerce.sync import client
from iiab_commerce.sync.product import _do_push_product
from iiab_commerce.sync.inventory import _do_push_stock
from iiab_commerce.sync.category import _do_push_category
from iiab_commerce.sync.cache import invalidate_storefront
from iiab_commerce.sync.utils import log_sync


def full_sync():
    """Full reconciliation of ERPNext → Bagisto catalog."""
    frappe.logger("iiab_commerce").info("Starting full catalog reconciliation...")

    stats = {"products": 0, "categories": 0, "inventory": 0, "deactivated": 0, "errors": 0}

    # 1. Sync all Item Groups (categories)
    item_groups = frappe.get_all(
        "Item Group",
        filters={"name": ["!=", "All Item Groups"]},
        fields=["name"],
        order_by="lft asc",  # Parent-first ordering via nested set
    )
    for ig in item_groups:
        try:
            _do_push_category(ig.name)
            stats["categories"] += 1
        except Exception as e:
            stats["errors"] += 1
            frappe.logger("iiab_commerce").error(f"Category sync error: {ig.name}: {e}")

    # 2. Sync all published Website Items (products)
    website_items = frappe.get_all(
        "Website Item",
        filters={"published": 1},
        fields=["name", "item_code"],
    )
    published_skus = set()

    for wi in website_items:
        try:
            _do_push_product(wi.name)
            published_skus.add(wi.item_code)
            stats["products"] += 1
        except Exception as e:
            stats["errors"] += 1
            frappe.logger("iiab_commerce").error(f"Product sync error: {wi.name}: {e}")

    # 3. Sync inventory for all published items
    for sku in published_skus:
        try:
            _do_push_stock(sku)
            stats["inventory"] += 1
        except Exception as e:
            stats["errors"] += 1
            frappe.logger("iiab_commerce").error(f"Inventory sync error: {sku}: {e}")

    # 4. Deactivate Bagisto products that are no longer published
    try:
        all_bagisto_products = client.get_all_products()
        for bp in all_bagisto_products:
            sku = bp.get("sku")
            if sku and sku not in published_skus and bp.get("status"):
                try:
                    client.deactivate_product(bp["id"])
                    stats["deactivated"] += 1
                    frappe.logger("iiab_commerce").info(
                        f"Deactivated orphan product: {sku}"
                    )
                except Exception:
                    stats["errors"] += 1
    except Exception as e:
        frappe.logger("iiab_commerce").error(f"Deactivation sweep error: {e}")

    log_sync(
        sync_type="reconcile",
        direction="push",
        status="success",
        payload=stats,
    )
    frappe.logger("iiab_commerce").info(
        f"Reconciliation complete: {stats}"
    )

    # Invalidate storefront cache once for all changes
    if stats["products"] > 0 or stats["categories"] > 0 or stats["deactivated"] > 0:
        invalidate_storefront()
        frappe.logger("iiab_commerce").info("Storefront cache invalidated")

    return stats
