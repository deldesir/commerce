"""
Product sync: Website Item → Bagisto Product.

Triggered by Website Item DocEvent on_update.
Uses direct MySQL access to Bagisto's database.
"""
import frappe
from iiab_commerce.sync import client
from iiab_commerce.sync.utils import log_sync
from iiab_commerce.sync.category import get_bagisto_category_id_for_item
import re


def push_product(doc, method=None):
    """Push a Website Item to Bagisto as a product. Runs in background."""
    if isinstance(doc, str):
        doc = frappe.get_doc("Website Item", doc)

    frappe.enqueue(
        _do_push_product,
        queue="default",
        timeout=120,
        website_item_name=doc.name,
    )


def _do_push_product(website_item_name):
    """Background job: push a single Website Item to Bagisto."""
    try:
        wi = frappe.get_doc("Website Item", website_item_name)
        item = frappe.get_doc("Item", wi.item_code)

        # Build the image URLs — try slideshow first, then website_image, then item.image
        image_urls = []
        if wi.slideshow:
            slideshow_items = frappe.get_all(
                "Website Slideshow Item",
                filters={"parent": wi.slideshow},
                fields=["image"],
                order_by="idx asc"
            )
            for s in slideshow_items:
                url = _resolve_image_url(s.image)
                if url:
                    image_urls.append(url)

        if not image_urls:
            fallback = _resolve_image_url(wi.website_image) or _resolve_image_url(item.image)
            if fallback:
                image_urls.append(fallback)

        # Determine product type
        product_type = "configurable" if item.has_variants else "simple"

        # Determine price: prefer Item Price (selling), fall back to standard_rate
        price = float(item.standard_rate or 0)
        if price == 0:
            price_entry = frappe.db.get_value(
                "Item Price",
                {"item_code": item.item_code, "selling": 1},
                "price_list_rate",
            )
            if price_entry:
                price = float(price_entry)

        # Build url_key: must be a flat slug (no slashes)
        raw_key = wi.route or item.item_code
        url_key = raw_key.rsplit("/", 1)[-1]  # take only last segment
        url_key = re.sub(r"[^a-z0-9-]", "-", url_key.lower()).strip("-")

        # Build data for product_flat
        data = {
            "name": wi.web_item_name or item.item_name,
            "short_description": wi.short_description or item.description or "",
            "description": wi.web_long_description or item.description or wi.short_description or "",
            "price": price,
            "weight": float(item.weight_per_unit or 1),
            "status": 1 if wi.published else 0,
            "visible_individually": 1,
            "url_key": url_key,
            "new": 1,
            "featured": 0,
        }

        # Upsert product
        product_id = client.upsert_product(item.item_code, product_type, data)

        # Assign category if available
        category_id = get_bagisto_category_id_for_item(item.item_code)
        if category_id:
            client.assign_category(product_id, category_id)

        # Handle images
        if image_urls:
            _save_product_images(product_id, image_urls)

        # Note: Shop category assignment is handled by client.upsert_product()

        existing = client.find_product_by_sku(item.item_code)
        action = "updated" if existing and existing.get("name") else "created"

        log_sync(
            sync_type="product",
            direction="push",
            item_code=item.item_code,
            status="success",
            payload=data,
            response={"product_id": product_id},
        )
        frappe.logger("iiab_commerce").info(
            f"Product {action}: {item.item_code} → Bagisto (id={product_id})"
        )

    except Exception as e:
        log_sync(
            sync_type="product",
            direction="push",
            item_code=website_item_name,
            status="failed",
            error=str(e),
        )
        frappe.logger("iiab_commerce").error(
            f"Product push failed for {website_item_name}: {e}"
        )


def delete_product(doc, method=None):
    """Deactivate a product in Bagisto when Website Item is trashed."""
    if isinstance(doc, str):
        doc = frappe.get_doc("Website Item", doc)

    frappe.enqueue(
        _do_delete_product,
        queue="default",
        timeout=60,
        item_code=doc.item_code,
    )


def _do_delete_product(item_code):
    """Background job: deactivate product in Bagisto."""
    try:
        existing = client.find_product_by_sku(item_code)
        if existing:
            client.deactivate_product(existing["id"])
            log_sync(
                sync_type="product",
                direction="push",
                item_code=item_code,
                status="success",
                payload={"action": "deactivate"},
            )
    except Exception as e:
        log_sync(
            sync_type="product",
            direction="push",
            item_code=item_code,
            status="failed",
            error=str(e),
        )


def _resolve_image_url(image_path):
    """Resolve an image path to a full URL.

    - External URLs (https://...) pass through unchanged.
    - Local files (/files/...) get verified on disk first.
    - Returns None if file doesn't exist locally.
    """
    if not image_path:
        return None
    if image_path.startswith(("http://", "https://")):
        return image_path
    # Local file — verify it exists on disk before using
    import os
    local_path = frappe.get_site_path("public", image_path.lstrip("/"))
    if not os.path.exists(local_path):
        return None
    return f"/erpnext{image_path}"


def _save_product_images(product_id, image_urls):
    """Download and save product images to Bagisto's local storage.

    Bagisto serves images from storage/app/public/product/{id}/.
    External URLs are downloaded; local ERPNext paths are copied.
    Always overwrites to ensure image replacements propagate.

    Uses a stable filename (main.ext) for the first image, and
    image-1.ext, image-2.ext for subsequent images.
    """
    import subprocess
    import os
    import time

    STORAGE_BASE = "/library/bagisto/storage/app/public"

    try:
        img_dir = f"{STORAGE_BASE}/product/{product_id}"
        os.makedirs(img_dir, exist_ok=True)

        # Clear existing images in DB for this product to prevent orphaned DB entries
        db = client.get_db()
        with db.cursor() as cursor:
            cursor.execute(
                "DELETE FROM product_images WHERE product_id = %s",
                (product_id,)
            )

        for idx, image_url in enumerate(image_urls):
            # Determine extension from source
            ext = os.path.splitext(image_url)[-1] or ".jpg"
            if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
                ext = ".jpg"

            fname = f"main{ext}" if idx == 0 else f"image-{idx}{ext}"
            fpath = f"{img_dir}/{fname}"
            relative_path = f"product/{product_id}/{fname}"

            # Download if external URL
            if image_url.startswith(("http://", "https://")):
                subprocess.run(
                    ["curl", "-sL", "-o", fpath, "-A", "Mozilla/5.0", image_url],
                    timeout=30, capture_output=True,
                )
                if not os.path.exists(fpath) or os.path.getsize(fpath) < 1000:
                    continue  # Download failed
            elif image_url.startswith("/erpnext"):
                # Local ERPNext file — copy from the site files directory
                src = frappe.get_site_path("public", image_url.replace("/erpnext/", ""))
                if os.path.exists(src):
                    import shutil
                    shutil.copy2(src, fpath)
                else:
                    continue

            # Ensure files are group-readable (frappe user is in www-data group)
            os.chmod(img_dir, 0o775)
            os.chmod(fpath, 0o664)

            # Insert into product_images
            with db.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO product_images (product_id, path, type, position) "
                    "VALUES (%s, %s, 'images', %s)",
                    (product_id, relative_path, idx),
                )

        db.commit()

        # Clean up any old files from previous sync logic that aren't mapped anymore
        import glob
        expected_files = []
        for i, u in enumerate(image_urls):
            ext = os.path.splitext(u)[-1] or '.jpg'
            if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
                ext = ".jpg"
            expected_files.append(f"main{ext}" if i == 0 else f"image-{i}{ext}")
            
        for old_file in glob.glob(f"{img_dir}/*"):
            if os.path.basename(old_file) not in expected_files:
                try:
                    os.remove(old_file)
                except OSError:
                    pass

    except Exception as e:
        frappe.logger("iiab_commerce").error(f"Failed to save images for product {product_id}: {e}")



