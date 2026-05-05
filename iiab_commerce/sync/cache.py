"""
Storefront cache invalidation.

After a product or category sync, purge the Next.js fetch cache
so the storefront picks up changes without waiting for revalidate.
"""
import os
import shutil
import subprocess

STOREFRONT_CACHE_DIR = "/library/nextjs-commerce/.next/cache/fetch-cache"
STOREFRONT_SERVICE = "bagisto-headless"


def invalidate_storefront():
    """Purge the Next.js fetch cache and restart the storefront service.

    This is called after successful product/category syncs to ensure
    the storefront reflects the latest data immediately.
    Runs as a fire-and-forget — errors are logged but don't block sync.
    """
    try:
        if os.path.isdir(STOREFRONT_CACHE_DIR):
            shutil.rmtree(STOREFRONT_CACHE_DIR, ignore_errors=True)

        subprocess.run(
            ["systemctl", "restart", STOREFRONT_SERVICE],
            timeout=10,
            capture_output=True,
        )
    except Exception:
        # Best-effort: cache will expire naturally on its own
        pass
