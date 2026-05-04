"""
Category sync: Item Group → Bagisto Category.

Triggered by Item Group DocEvent on_update.
Uses direct MySQL access to Bagisto's database.
"""
import frappe
from iiab_commerce.sync import client
from iiab_commerce.sync.utils import log_sync


def push_category(doc, method=None):
    """Push an Item Group to Bagisto as a category. Runs in background."""
    if isinstance(doc, str):
        doc = frappe.get_doc("Item Group", doc)

    # Skip the root "All Item Groups"
    if doc.name == "All Item Groups":
        return

    frappe.enqueue(
        _do_push_category,
        queue="default",
        timeout=60,
        item_group_name=doc.name,
    )


def _do_push_category(item_group_name):
    """Background job: push a single Item Group to Bagisto."""
    try:
        ig = frappe.get_doc("Item Group", item_group_name)

        # Resolve parent category ID in Bagisto
        parent_id = 1  # Bagisto root category
        if ig.parent_item_group and ig.parent_item_group != "All Item Groups":
            parent_id = _get_bagisto_category_id(ig.parent_item_group) or 1

        slug = ig.name.lower().replace(" ", "-").replace("/", "-")
        
        logo_path = None
        if ig.image:
            logo_path = _download_category_image(ig.name, ig.image)

        category_id = client.upsert_category(
            name=ig.item_group_name,
            slug=slug,
            parent_id=parent_id,
            description=ig.description or "",
            status=1,
            logo_path=logo_path
        )

        # Save the mapping
        _save_category_mapping(ig.name, category_id)

        log_sync(
            sync_type="category",
            direction="push",
            item_code=ig.name,
            status="success",
            payload={"name": ig.item_group_name, "slug": slug, "parent_id": parent_id},
            response={"category_id": category_id},
        )
        frappe.logger("iiab_commerce").info(
            f"Category synced: {ig.name} → Bagisto (id={category_id})"
        )

    except Exception as e:
        log_sync(
            sync_type="category",
            direction="push",
            item_code=item_group_name,
            status="failed",
            error=str(e),
        )
        frappe.logger("iiab_commerce").error(
            f"Category push failed for {item_group_name}: {e}"
        )

def _download_category_image(category_name, image_url):
    """Download category image to Bagisto local storage."""
    import subprocess
    import os
    import hashlib

    STORAGE_BASE = "/library/bagisto/storage/app/public"

    try:
        slug = category_name.lower().replace(" ", "-").replace("/", "-")
        img_dir = f"{STORAGE_BASE}/category/{slug}"
        os.makedirs(img_dir, exist_ok=True)

        fname = hashlib.md5(image_url.encode()).hexdigest()[:20] + ".jpg"
        fpath = f"{img_dir}/{fname}"
        relative_path = f"category/{slug}/{fname}"

        if image_url.startswith(("http://", "https://")):
            subprocess.run(
                ["curl", "-sL", "-o", fpath, "-A", "Mozilla/5.0", image_url],
                timeout=30, capture_output=True,
            )
            if not os.path.exists(fpath) or os.path.getsize(fpath) < 1000:
                return None
        else:
            return None # Skip internal ERPNext files for now, or copy them if needed

        # Ensure files are group-readable (frappe user is in www-data group)
        import os as _os
        _os.chmod(img_dir, 0o775)
        _os.chmod(fpath, 0o664)

        return relative_path
    except Exception as e:
        frappe.logger("iiab_commerce").error(f"Category image download error: {e}")
        return None


def _get_bagisto_category_id(item_group_name):
    """Look up a cached Bagisto category ID from our mapping doctype."""
    try:
        mapping = frappe.get_all(
            "Bagisto Category Map",
            filters={"item_group": item_group_name},
            fields=["bagisto_category_id"],
            limit=1,
        )
        if mapping:
            return mapping[0].bagisto_category_id
    except Exception:
        pass
    return None


def _save_category_mapping(item_group_name, bagisto_id):
    """Save an Item Group → Bagisto category ID mapping."""
    try:
        existing = frappe.get_all(
            "Bagisto Category Map",
            filters={"item_group": item_group_name},
            limit=1,
        )
        if existing:
            frappe.db.set_value(
                "Bagisto Category Map", existing[0].name,
                "bagisto_category_id", bagisto_id
            )
        else:
            doc = frappe.get_doc({
                "doctype": "Bagisto Category Map",
                "item_group": item_group_name,
                "bagisto_category_id": bagisto_id,
            })
            doc.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        frappe.logger("iiab_commerce").warning(
            f"Could not save category mapping for {item_group_name} → {bagisto_id}"
        )


def get_bagisto_category_id_for_item(item_code):
    """Given an item_code, return the Bagisto category ID for its Item Group."""
    item_group = frappe.db.get_value("Item", item_code, "item_group")
    if not item_group:
        return None
    return _get_bagisto_category_id(item_group)
