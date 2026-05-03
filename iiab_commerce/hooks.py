app_name = "iiab_commerce"
app_title = "IIAB Commerce"
app_publisher = "IIAB"
app_description = "ERPNext to Bagisto sync layer for IIAB deployments"
app_email = "admin@iiab.io"
app_license = "mit"

# Required apps
required_apps = ["erpnext", "webshop"]

# Document Events
# ---------------
# Website Item is the bridge doctype: publishing an Item to the storefront
# triggers the product sync. Item hook is only for variant propagation.

doc_events = {
	"Website Item": {
		"on_update": "iiab_commerce.sync.product.push_product",
		"on_trash": "iiab_commerce.sync.product.delete_product",
	},
	"Item Group": {
		"on_update": "iiab_commerce.sync.category.push_category",
	},
	"Item Price": {
		"on_update": "iiab_commerce.sync.price.push_price",
	},
	"Stock Ledger Entry": {
		"on_submit": "iiab_commerce.sync.inventory.push_stock",
	},
	"Item": {
		"on_update": "iiab_commerce.sync.variant.push_variant",
	},
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"daily": [
		"iiab_commerce.sync.reconcile.full_sync",
	],
}

# Log clearing
default_log_clearing_doctypes = {
	"Bagisto Sync Log": 30  # Retain logs for 30 days
}
