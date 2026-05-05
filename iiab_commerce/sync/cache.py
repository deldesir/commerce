"""
Storefront cache invalidation.

After a product or category sync, purge Next.js caches so the
storefront picks up changes without waiting for the revalidation timer.

Two strategies are used:
1. Purge the Bagisto PHP application cache (so GraphQL returns fresh data)
2. Purge the Next.js on-disk fetch cache
3. HTTP call to the Next.js revalidation API (tag-based ISR invalidation)

This module is designed to be called once after a batch of syncs
(e.g. at the end of full_sync), NOT after every individual product.
Individual sync modules should NOT call invalidate_storefront() directly.
"""
import os
import shutil
import subprocess
import urllib.request
import json

STOREFRONT_CACHE_DIR = "/library/nextjs-commerce/.next/cache/fetch-cache"
STOREFRONT_BASE_URL = "http://127.0.0.1:3001/live"
REVALIDATION_SECRET = "iiab-commerce-sync"

# Bagisto cache directories that must exist after clearing
BAGISTO_CACHE_DIRS = [
    "/library/bagisto/storage/framework/cache/data",
    "/library/bagisto/storage/framework/sessions",
    "/library/bagisto/storage/framework/views",
    "/library/bagisto/bootstrap/cache",
]


def invalidate_storefront():
    """Invalidate all caches so the storefront reflects the latest data.

    1. Clears Bagisto PHP cache (GraphQL responses cached by Laravel)
    2. Purges Next.js fetch-cache on disk
    3. Calls the Next.js revalidation API endpoint
    """
    _clear_bagisto_cache()
    _purge_fetch_cache()
    _call_revalidation_api("products/update")


def _clear_bagisto_cache():
    """Clear Bagisto's Laravel application cache.

    Uses `php artisan cache:clear` but ensures the directory
    structure is recreated afterward (artisan sometimes deletes
    subdirectories which breaks subsequent requests).
    """
    try:
        subprocess.run(
            ["php", "artisan", "cache:clear"],
            cwd="/library/bagisto",
            timeout=10,
            capture_output=True,
        )
    except Exception:
        pass

    # Ensure cache directories exist (artisan can delete them)
    for d in BAGISTO_CACHE_DIRS:
        try:
            os.makedirs(d, exist_ok=True)
            # www-data needs write access
            os.chmod(d, 0o775)
        except Exception:
            pass


def _call_revalidation_api(topic="products/update"):
    """Call the Next.js /api/revalidate endpoint to trigger ISR revalidation."""
    try:
        url = f"{STOREFRONT_BASE_URL}/api/revalidate?secret={REVALIDATION_SECRET}"
        req = urllib.request.Request(
            url,
            data=json.dumps({}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-bagisto-topic": topic,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except Exception:
        # Best-effort — the fetch cache purge below is the fallback
        pass


def _purge_fetch_cache():
    """Remove the on-disk fetch cache so Next.js re-fetches from Bagisto."""
    try:
        if os.path.isdir(STOREFRONT_CACHE_DIR):
            shutil.rmtree(STOREFRONT_CACHE_DIR, ignore_errors=True)
    except Exception:
        pass
