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
                "INSERT INTO products (sku, type, created_at, updated_at) "
                "VALUES (%s, %s, NOW(), NOW())",
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
            # Remove non-flat fields
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

            # Build the SQL with mixed placeholders
            col_str = ", ".join(f"`{c}`" for c in cols)
            ph_str = ", ".join(placeholders)
            cursor.execute(
                f"INSERT INTO product_flat ({col_str}) VALUES ({ph_str})",
                values,
            )

    return product_id


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

            # Create translation
            cursor.execute(
                "INSERT INTO category_translations "
                "(category_id, locale, name, slug, description) "
                "VALUES (%s, 'en', %s, %s, %s)",
                (category_id, name, slug, description),
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
    """Update a product's price in product_flat."""
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            "UPDATE product_flat SET price = %s, updated_at = NOW() "
            "WHERE product_id = %s",
            (price, product_id),
        )


def deactivate_product(product_id):
    """Deactivate a product in Bagisto."""
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            "UPDATE product_flat SET status = 0, updated_at = NOW() "
            "WHERE product_id = %s",
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
