"""
Bagisto sync client for iiab_commerce.

Since both ERPNext and Bagisto run on the same IIAB box and share the same
MySQL server, we use direct database access for write operations (product
create/update, inventory, categories) and the shop GraphQL API for reads.

This eliminates the need for admin API authentication and is more reliable
for a single-box deployment.
"""
import frappe
import pymysql
import json

# Module-level connection cache
_bagisto_conn = None

BAGISTO_DB_NAME = "bagisto"

# Bagisto core attribute IDs (from `attributes` table)
# These must be populated in `product_attribute_values` for the API Platform
# GraphQL layer to see products.
ATTR_MAP = {
    "sku":                  (1,  "text_value"),
    "name":                 (2,  "text_value"),
    "url_key":              (3,  "text_value"),
    "new":                  (5,  "boolean_value"),
    "featured":             (6,  "boolean_value"),
    "visible_individually": (7,  "boolean_value"),
    "status":               (8,  "boolean_value"),
    "short_description":    (9,  "text_value"),
    "description":          (10, "text_value"),
    "price":                (11, "float_value"),
    "weight":               (22, "text_value"),
}

# Default Shop category ID (set after first category sync or channel setup)
DEFAULT_SHOP_CATEGORY_ID = None


def _get_config(key, default=None):
    """Read a value from site_config.json."""
    return frappe.conf.get(key, default)


def get_bagisto_url():
    """Return the base Bagisto URL from config."""
    return (_get_config("bagisto_api_url") or "http://127.0.0.1/live").rstrip("/")


def get_db():
    """Get a direct MySQL connection to the Bagisto database."""
    global _bagisto_conn

    if _bagisto_conn and _bagisto_conn.open:
        try:
            _bagisto_conn.ping(reconnect=True)
            return _bagisto_conn
        except Exception:
            _bagisto_conn = None

    _bagisto_conn = pymysql.connect(
        unix_socket="/run/mysqld/mysqld.sock",
        user=_get_config("bagisto_db_user", "bagisto"),
        password=_get_config("bagisto_db_password", "bagisto_secret"),
        database=BAGISTO_DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )
    return _bagisto_conn


def find_product_by_sku(sku):
    """Look up a Bagisto product by SKU. Returns dict or None."""
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT p.id, p.sku, p.type, pf.name, pf.price, pf.status "
            "FROM products p "
            "LEFT JOIN product_flat pf ON pf.product_id = p.id "
            "WHERE p.sku = %s LIMIT 1",
            (sku,),
        )
        return cursor.fetchone()


def find_category_by_slug(slug):
    """Look up a Bagisto category by slug. Returns dict or None."""
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT c.id, ct.name, ct.slug "
            "FROM categories c "
            "JOIN category_translations ct ON ct.category_id = c.id "
            "WHERE ct.slug = %s AND ct.locale = 'en' LIMIT 1",
            (slug,),
        )
        return cursor.fetchone()


def find_category_by_name(name):
    """Look up a Bagisto category by name. Returns dict or None."""
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT c.id, ct.name, ct.slug "
            "FROM categories c "
            "JOIN category_translations ct ON ct.category_id = c.id "
            "WHERE ct.name = %s AND ct.locale = 'en' LIMIT 1",
            (name,),
        )
        return cursor.fetchone()


def upsert_product(sku, product_type, data):
    """Create or update a Bagisto product.

    Populates both `product_flat` (for Bagisto's Eloquent layer) and
    `product_attribute_values` (for the API Platform GraphQL layer).

    Args:
        sku: Product SKU (join key)
        product_type: 'simple' or 'configurable'
        data: dict with keys matching product_flat columns
              (name, description, short_description, price, weight, status, url_key, etc.)

    Returns:
        product_id (int)
    """
    db = get_db()
    existing = find_product_by_sku(sku)

    with db.cursor() as cursor:
        if existing:
            product_id = existing["id"]
            # Update product_flat
            set_clauses = []
            values = []
            for key, val in data.items():
                set_clauses.append(f"`{key}` = %s")
                values.append(val)
            if set_clauses:
                values.append(product_id)
                cursor.execute(
                    f"UPDATE product_flat SET {', '.join(set_clauses)}, "
                    f"updated_at = NOW() WHERE product_id = %s",
                    values,
                )
        else:
            # Create product
            cursor.execute(
                "INSERT INTO products (sku, type, attribute_family_id, "
                "created_at, updated_at) VALUES (%s, %s, 1, NOW(), NOW())",
                (sku, product_type),
            )
            product_id = cursor.lastrowid

            # Create product_flat entry
            flat_data = {
                "product_id": product_id,
                "sku": sku,
                "type": product_type,
                "locale": "en",
                "channel": "default",
                "attribute_family_id": data.get("attribute_family_id", 1),
                "created_at": "NOW()",
                "updated_at": "NOW()",
            }
            flat_data.update(data)
            flat_data.pop("attribute_family_id", None)

            cols = list(flat_data.keys())
            placeholders = []
            values = []
            for c in cols:
                v = flat_data[c]
                if v == "NOW()":
                    placeholders.append("NOW()")
                else:
                    placeholders.append("%s")
                    values.append(v)

            col_str = ", ".join(f"`{c}`" for c in cols)
            ph_str = ", ".join(placeholders)
            cursor.execute(
                f"INSERT INTO product_flat ({col_str}) VALUES ({ph_str})",
                values,
            )

    # Populate product_attribute_values (required for API Platform GraphQL)
    _populate_attribute_values(product_id, sku, data)

    # Assign to default Shop category
    shop_cat_id = _get_shop_category_id()
    if shop_cat_id:
        assign_category(product_id, shop_cat_id)

    return product_id


def _populate_attribute_values(product_id, sku, data):
    """Populate product_attribute_values for API Platform visibility."""
    db = get_db()
    vals = {
        "sku": sku,
        "name": data.get("name", sku),
        "url_key": data.get("url_key", sku.lower()),
        "new": data.get("new", 1),
        "featured": data.get("featured", 0),
        "visible_individually": data.get("visible_individually", 1),
        "status": data.get("status", 1),
        "short_description": data.get("short_description", ""),
        "description": data.get("description", ""),
        "price": float(data.get("price", 0)),
        "weight": str(data.get("weight", 1)),
    }

    with db.cursor() as cursor:
        for attr_code, (attr_id, col) in ATTR_MAP.items():
            val = vals.get(attr_code)
            cursor.execute(
                "DELETE FROM product_attribute_values "
                "WHERE product_id = %s AND attribute_id = %s",
                (product_id, attr_id),
            )
            if val is not None:
                cursor.execute(
                    f"INSERT INTO product_attribute_values "
                    f"(product_id, attribute_id, {col}, channel, locale) "
                    f"VALUES (%s, %s, %s, %s, %s)",
                    (product_id, attr_id, val, "default", "en"),
                )


def _get_shop_category_id():
    """Get or cache the default Shop category ID."""
    global DEFAULT_SHOP_CATEGORY_ID
    if DEFAULT_SHOP_CATEGORY_ID:
        return DEFAULT_SHOP_CATEGORY_ID

    cat = find_category_by_slug("shop")
    if cat:
        DEFAULT_SHOP_CATEGORY_ID = cat["id"]
        return DEFAULT_SHOP_CATEGORY_ID

    # Create the Shop category if it doesn't exist
    DEFAULT_SHOP_CATEGORY_ID = upsert_category(
        name="Shop", slug="shop", parent_id=1, description="All products"
    )
    return DEFAULT_SHOP_CATEGORY_ID


def upsert_category(name, slug, parent_id=1, description="", status=1):
    """Create or update a Bagisto category.

    Returns:
        category_id (int)
    """
    db = get_db()
    existing = find_category_by_slug(slug)

    with db.cursor() as cursor:
        if existing:
            category_id = existing["id"]
            cursor.execute(
                "UPDATE category_translations SET name = %s, description = %s "
                "WHERE category_id = %s AND locale = 'en'",
                (name, description, category_id),
            )
        else:
            cursor.execute(
                "INSERT INTO categories (parent_id, position, status, created_at, updated_at) "
                "VALUES (%s, 0, %s, NOW(), NOW())",
                (parent_id, status),
            )
            category_id = cursor.lastrowid

            # Create translation (url_path is required)
            cursor.execute(
                "INSERT INTO category_translations "
                "(category_id, locale, name, slug, url_path, description) "
                "VALUES (%s, 'en', %s, %s, %s, %s)",
                (category_id, name, slug, slug, description),
            )

    return category_id


def update_inventory(product_id, qty, inventory_source_id=1):
    """Set the inventory quantity for a product."""
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT id FROM product_inventories "
            "WHERE product_id = %s AND inventory_source_id = %s",
            (product_id, inventory_source_id),
        )
        existing = cursor.fetchone()

        if existing:
            cursor.execute(
                "UPDATE product_inventories SET qty = %s "
                "WHERE product_id = %s AND inventory_source_id = %s",
                (qty, product_id, inventory_source_id),
            )
        else:
            cursor.execute(
                "INSERT INTO product_inventories "
                "(product_id, inventory_source_id, qty, vendor_id) "
                "VALUES (%s, %s, %s, 0)",
                (product_id, inventory_source_id, qty),
            )


def update_product_price(product_id, price):
    """Update a product's price in product_flat and attribute_values."""
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            "UPDATE product_flat SET price = %s, updated_at = NOW() "
            "WHERE product_id = %s",
            (price, product_id),
        )
        # Also update in product_attribute_values (attr 11 = price)
        cursor.execute(
            "UPDATE product_attribute_values SET float_value = %s "
            "WHERE product_id = %s AND attribute_id = 11",
            (price, product_id),
        )


def deactivate_product(product_id):
    """Deactivate a product in Bagisto (both product_flat and attribute_values)."""
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            "UPDATE product_flat SET status = 0, updated_at = NOW() "
            "WHERE product_id = %s",
            (product_id,),
        )
        # Also update in product_attribute_values (attr 8 = status)
        cursor.execute(
            "UPDATE product_attribute_values SET boolean_value = 0 "
            "WHERE product_id = %s AND attribute_id = 8",
            (product_id,),
        )


def get_all_products():
    """Get all products from Bagisto."""
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT p.id, p.sku, p.type, pf.name, pf.price, pf.status "
            "FROM products p "
            "LEFT JOIN product_flat pf ON pf.product_id = p.id"
        )
        return cursor.fetchall()


def assign_category(product_id, category_id):
    """Assign a product to a category."""
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT id FROM product_categories "
            "WHERE product_id = %s AND category_id = %s",
            (product_id, category_id),
        )
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO product_categories (product_id, category_id) "
                "VALUES (%s, %s)",
                (product_id, category_id),
            )
