import frappe
from iiab_commerce.sync.category import _do_push_category

def execute():
    frappe.init(site="site.local")
    frappe.connect()

    print("Starting category migration...")
    item_groups = frappe.get_all(
        "Item Group",
        filters={"name": ["!=", "All Item Groups"]},
        fields=["name"],
        order_by="lft asc"
    )

    for ig in item_groups:
        print(f"Syncing {ig.name}...")
        _do_push_category(ig.name)

    print("Migration complete!")
    frappe.destroy()

if __name__ == "__main__":
    execute()
