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
    """Push variant changes to Bagisto."""
    if isinstance(doc, str):
        doc = frappe.get_doc("Item", doc)

    if not doc.variant_of:
        return

    frappe.enqueue(
        _do_push_variant,
        queue="default",
        timeout=120,
        item_code=doc.item_code,
    )

def _do_push_variant(item_code):
    from iiab_commerce.sync import client
    item = frappe.get_doc("Item", item_code)
    
    # 1. Parent must exist in Bagisto
    parent_bagisto = client.find_product_by_sku(item.variant_of)
    if not parent_bagisto:
        frappe.logger("iiab_commerce").warning(f"Parent {item.variant_of} not in Bagisto, skipping variant {item.item_code}")
        return
        
    parent_id = parent_bagisto["id"]

    # 2. Push this variant as a simple product
    data = {
        "name": item.item_name,
        "short_description": item.description or "",
        "description": item.description or "",
        "price": float(item.standard_rate or 0),
        "weight": float(item.weight_per_unit or 1),
        "status": 1,
        "visible_individually": 0, # Variants shouldn't show up in main catalog
        "url_key": item.item_code.lower().replace(" ", "-"),
        "new": 0,
        "featured": 0,
    }
    
    if data["price"] == 0:
        price_entry = frappe.db.get_value("Item Price", {"item_code": item.item_code, "selling": 1}, "price_list_rate")
        if price_entry:
            data["price"] = float(price_entry)

    child_id = client.upsert_product(item.item_code, "simple", data)

    # 3. Process attributes
    attributes = frappe.get_all(
        "Item Variant Attribute",
        filters={"parent": item.item_code},
        fields=["attribute", "attribute_value"]
    )
    
    for attr in attributes:
        attr_name = attr.attribute
        opt_value = attr.attribute_value
        
        # Ensure Bagisto has this attribute and option
        attr_id, opt_id = client.ensure_attribute_and_option(attr_name, opt_value)
        
        # Link variant to parent
        client.link_variant_to_parent(parent_id, child_id, attr_id, opt_id)
        
    log_sync("variant", "push", item.item_code, "success", response={"child_id": child_id, "parent_id": parent_id})
    
    # Invalidate cache for the parent
    from iiab_commerce.sync.cache import invalidate_storefront
    invalidate_storefront()

