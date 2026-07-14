# Safe patch for woocommerce_fusion on Frappe Cloud.
# Injects custom WC order statuses into the upstream mapping dict.
# Called via before_request hook — runs in every gunicorn worker
# before any API method executes.

import json

# ── Custom WC order statuses ──────────────────────────────────────────────────
# Add any new custom WC statuses here — single place to maintain
_CUSTOM_WC_STATUSES = {
    "arranged": "Arranged",
    "returned": "Returned",
}

# ── Order category normalisation map ─────────────────────────────────────────
# WooCommerce sends lowercase, ERPNext field uses exact casing
_ORDER_CATEGORY_MAP = {
    "art":  "ART",
    "pep":  "PEP",
    "prep": "PrEP",
}


def apply():
    """
    Entry point — called via before_request hook on every HTTP request.
    All patches are idempotent and never raise.
    """
    _patch_wc_mapping()
    _patch_update_sales_order()


# ─────────────────────────────────────────────────────────────────────────────
# Patch 1 — Fix KeyError on unknown WC statuses (line 241)
# ─────────────────────────────────────────────────────────────────────────────
def _patch_wc_mapping():
    try:
        from woocommerce_fusion.tasks import sync_sales_orders as ss

        for wc_key, erp_label in _CUSTOM_WC_STATUSES.items():
            if wc_key not in ss.WC_ORDER_STATUS_MAPPING_REVERSE:
                ss.WC_ORDER_STATUS_MAPPING_REVERSE[wc_key] = erp_label

            if hasattr(ss, "WC_ORDER_STATUS_MAPPING"):
                if erp_label not in ss.WC_ORDER_STATUS_MAPPING:
                    ss.WC_ORDER_STATUS_MAPPING[erp_label] = wc_key

    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Patch 2 — Inject order_category into Sales Order during WC sync
# ─────────────────────────────────────────────────────────────────────────────
def _patch_update_sales_order():
    """
    Wraps SynchroniseWooCommerceOrder.update_sales_order.

    The original method:
      1. Maps WC status → ERPNext status
      2. Updates other fields on sales_order
      3. Calls sales_order.save()   ← all inside update_sales_order()

    Our wrapper sets custom_order_category on the sales_order object
    BEFORE calling the original — so the value is included in that save().

    _taal_patched sentinel ensures the wrap happens only once per
    gunicorn worker process (before_request fires on every request,
    but the class object persists in memory).
    """
    try:
        from woocommerce_fusion.tasks.sync_sales_orders import (
            SynchroniseWooCommerceOrder,
        )

        if getattr(SynchroniseWooCommerceOrder, "_taal_patched", False):
            return  # Already patched in this worker — nothing to do

        original_update = SynchroniseWooCommerceOrder.update_sales_order

        def patched_update(self, woocommerce_order, sales_order):

            # ── Extract order_category from WC meta_data ──────────────────────
            try:
                meta = woocommerce_order.meta_data
                meta_list = (
                    json.loads(meta)
                    if isinstance(meta, str)
                    else (meta or [])
                )

                for item in meta_list:
                    if item.get("key") == "order_category":
                        raw      = item.get("value", "").strip()
                        category = _ORDER_CATEGORY_MAP.get(raw.lower(), "")
                        if category:
                            # Only set if field exists on the doctype
                            if hasattr(sales_order, "custom_order_category"):
                                sales_order.custom_order_category = category
                        break

            except Exception:
                pass  # Never block the sync for a category extraction failure

            # ── Call original (handles status + save()) ───────────────────────
            original_update(self, woocommerce_order, sales_order)

        SynchroniseWooCommerceOrder.update_sales_order = patched_update
        SynchroniseWooCommerceOrder._taal_patched = True

    except Exception:
        pass