"""
Variant sync: Item Variants → Bagisto Configurable + Simple Products.

Triggered by Item DocEvent on_update when variant_of is set,
or when has_variants is True (template item).

NOTE: Variant sync is currently a no-op stub. The full implementation
requires configurable product support in the direct DB client, which
is not yet available. Simple products are synced via Website Item hooks.
"""
import frappe
from iiab_commerce.sync.utils import log_sync


def push_variant(doc, method=None):
    """Push variant changes to Bagisto.

    Currently a no-op — variant support requires configurable product
    attributes in the DB client. Individual variant items are synced
    as simple products when they have a Website Item.
    """
    if isinstance(doc, str):
        doc = frappe.get_doc("Item", doc)

    # Only process if this is a variant or a template item
    if not doc.variant_of and not doc.has_variants:
        return

    # Log that we received the event but skip actual sync
    frappe.logger("iiab_commerce").debug(
        f"Variant event received for {doc.name} — "
        "skipped (variant sync not yet implemented, use Website Item)"
    )
