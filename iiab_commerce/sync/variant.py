"""
Variant sync: Item Variants → Bagisto Configurable + Simple Products.

Triggered by Item DocEvent on_update when variant_of is set,
or when has_variants is True (template item).
"""
import frappe
from iiab_commerce.sync import client
from iiab_commerce.sync.utils import log_sync


def push_variant(doc, method=None):
    """Push variant changes to Bagisto. Runs in background."""
    if isinstance(doc, str):
        doc = frappe.get_doc("Item", doc)

    # Only process if this is a variant or a template item
    if not doc.variant_of and not doc.has_variants:
        return

    frappe.enqueue(
        _do_push_variant,
        queue="default",
        timeout=120,
        item_code=doc.name,
    )


def _do_push_variant(item_code):
    """Background job: sync variant relationships to Bagisto."""
    try:
        item = frappe.get_doc("Item", item_code)

        if item.has_variants:
            # This is a template — sync its variants
            _sync_template_variants(item)
        elif item.variant_of:
            # This is a variant — sync it as a simple product
            _sync_single_variant(item)

    except Exception as e:
        log_sync(
            sync_type="variant",
            direction="push",
            item_code=item_code,
            status="failed",
            error=str(e),
        )
        frappe.logger("iiab_commerce").error(
            f"Variant push failed for {item_code}: {e}"
        )


def _sync_template_variants(template_item):
    """Sync a template item and all its variants to Bagisto."""
    # Ensure the configurable parent exists in Bagisto
    parent = client.find_product_by_sku(template_item.item_code)
    if not parent:
        frappe.logger("iiab_commerce").warning(
            f"Template {template_item.item_code} not yet in Bagisto — "
            "will be synced when its Website Item is published"
        )
        return

    # Ensure Bagisto attributes exist for each ERPNext attribute
    for attr in template_item.attributes:
        _ensure_bagisto_attribute(attr.attribute)

    # Sync each variant
    variants = frappe.get_all(
        "Item",
        filters={"variant_of": template_item.item_code},
        fields=["name"],
    )
    for v in variants:
        variant_item = frappe.get_doc("Item", v.name)
        _sync_single_variant(variant_item)


def _sync_single_variant(variant_item):
    """Sync a single variant item to Bagisto as a simple product."""
    # Build attribute values from the variant's attributes
    attribute_values = {}
    for attr in variant_item.attributes:
        attribute_values[attr.attribute.lower().replace(" ", "_")] = attr.attribute_value

    payload = {
        "sku": variant_item.item_code,
        "type": "simple",
        "attribute_family_id": 1,
        "name": variant_item.item_name,
        "price": float(variant_item.standard_rate or 0),
        "weight": float(variant_item.weight_per_unit or 1),
        "status": 0 if variant_item.disabled else 1,
        "visible_individually": 0,  # Variants aren't browseable alone
    }
    payload.update(attribute_values)

    existing = client.find_product_by_sku(variant_item.item_code)
    if existing:
        result = client.put(f"catalog/products/{existing['id']}", payload)
        action = "updated"
    else:
        result = client.post("catalog/products", payload)
        action = "created"

    # Link to parent configurable product
    if variant_item.variant_of:
        parent = client.find_product_by_sku(variant_item.variant_of)
        variant_id = existing["id"] if existing else result.get("data", {}).get("id")
        if parent and variant_id:
            try:
                client.post(
                    f"catalog/products/{parent['id']}/variants",
                    {"variant_id": variant_id},
                )
            except Exception:
                pass  # May already be linked

    log_sync(
        sync_type="variant",
        direction="push",
        item_code=variant_item.item_code,
        status="success",
        payload=payload,
        response=result,
    )
    frappe.logger("iiab_commerce").info(
        f"Variant {action}: {variant_item.item_code} → Bagisto"
    )


def _ensure_bagisto_attribute(attribute_name):
    """Ensure a product attribute exists in Bagisto matching an ERPNext Item Attribute."""
    try:
        code = attribute_name.lower().replace(" ", "_")
        # Check if attribute exists
        attrs = client.get("catalog/attributes", params={"code": code})
        existing = None
        for a in attrs.get("data", []):
            if a.get("code") == code:
                existing = a
                break

        if not existing:
            # Create the attribute in Bagisto
            attr_doc = frappe.get_doc("Item Attribute", attribute_name)
            options = []
            for idx, val in enumerate(attr_doc.item_attribute_values):
                options.append({
                    "admin_name": val.attribute_value,
                    "sort_order": idx,
                })

            client.post("catalog/attributes", {
                "code": code,
                "admin_name": attribute_name,
                "type": "select",
                "is_required": 0,
                "is_unique": 0,
                "is_filterable": 1,
                "is_configurable": 1,
                "is_visible_on_front": 1,
                "options": options,
            })
            frappe.logger("iiab_commerce").info(
                f"Created Bagisto attribute: {code}"
            )
    except Exception as e:
        frappe.logger("iiab_commerce").warning(
            f"Could not ensure Bagisto attribute {attribute_name}: {e}"
        )
