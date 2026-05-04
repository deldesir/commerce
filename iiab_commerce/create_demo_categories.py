import frappe, os

def run():
    SITE_FILES = "/home/frappe/frappe-bench/sites/site.local/public/files"

    def ensure_file(fname):
        path = f"/files/{fname}"
        full = os.path.join(SITE_FILES, fname)
        if not os.path.exists(full):
            print(f"  WARNING: {full} not found")
            return None
        existing = frappe.db.exists("File", {"file_url": path})
        if not existing:
            f = frappe.get_doc({
                "doctype": "File",
                "file_name": fname,
                "file_url": path,
                "is_private": 0,
            })
            f.insert(ignore_permissions=True)
        return path

    img_electronics = ensure_file("electronics.jpg")
    img_accessories = ensure_file("accessories.jpg")
    img_home = ensure_file("home_living.jpg")

    categories = [
        {"name": "Electronics", "image": img_electronics, "description": "Smartphones, laptops, tablets and more"},
        {"name": "Accessories", "image": img_accessories, "description": "Watches, earbuds, cameras and gear"},
        {"name": "Home & Living", "image": img_home, "description": "Everything for your home"},
    ]

    for cat in categories:
        if not frappe.db.exists("Item Group", cat["name"]):
            ig = frappe.get_doc({
                "doctype": "Item Group",
                "item_group_name": cat["name"],
                "parent_item_group": "All Item Groups",
                "is_group": 0,
                "show_in_website": 1,
                "image": cat["image"],
                "description": cat["description"],
            })
            ig.insert(ignore_permissions=True)
            print(f"Created: {cat['name']} (image={cat['image']})")
        else:
            ig = frappe.get_doc("Item Group", cat["name"])
            ig.show_in_website = 1
            ig.image = cat["image"]
            ig.description = cat["description"]
            ig.save(ignore_permissions=True)
            print(f"Updated: {cat['name']} (image={cat['image']})")

    frappe.db.commit()
    print("\nAll categories created/updated.")
