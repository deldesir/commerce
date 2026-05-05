"""
Storefront cache invalidation.

After a product or category sync, purge Next.js caches so the
storefront picks up changes without waiting for the revalidation timer.

Two strategies are used:
1. HTTP call to the Next.js revalidation API (tag-based invalidation)
2. Purge the on-disk fetch-cache as a fallback

This module is designed to be called once after a batch of syncs
(e.g. at the end of full_sync), NOT after every individual product.
Individual sync modules should NOT call invalidate_storefront() directly.
"""
import os
import shutil
import urllib.request
import json

STOREFRONT_CACHE_DIR = "/library/nextjs-commerce/.next/cache/fetch-cache"
STOREFRONT_BASE_URL = "http://127.0.0.1:3000"
REVALIDATION_SECRET = "iiab-commerce-sync"


def invalidate_storefront():
    """Invalidate the Next.js storefront caches.

    Uses the built-in revalidation API endpoint to trigger ISR
    revalidation, then clears the fetch cache on disk as a fallback.
    Does NOT restart the service — that causes downtime.
    """
    _call_revalidation_api("products/update")
    _purge_fetch_cache()


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
