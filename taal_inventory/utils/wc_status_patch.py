# Safe patch for woocommerce_fusion on Frappe Cloud.
# Injects custom WC order statuses into the upstream mapping dict.
# Called via before_request hook — runs in every gunicorn worker
# before any API method executes.

import json

_CUSTOM_WC_STATUSES = {
    "arranged": "Arranged",
    "returned": "Returned",
}

_ORDER_CATEGORY_MAP = {
    "art":  "ART",
    "pep":  "PEP",
    "prep": "PrEP",
    "STI": "STI",
}


def apply():
    _patch_wc_mapping()
    _patch_update_sales_order()


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


def _patch_update_sales_order():
    try:
        from woocommerce_fusion.tasks.sync_sales_orders import (
            SynchroniseWooCommerceOrder,
        )

        if getattr(SynchroniseWooCommerceOrder, "_taal_patched", False):
            return

        original_update = SynchroniseWooCommerceOrder.update_sales_order

        def patched_update(self, woocommerce_order, sales_order):
            try:
                meta = woocommerce_order.meta_data
                meta_list = (
                    json.loads(meta)
                    if isinstance(meta, str)
                    else (meta or [])
                )

                for item in meta_list:
                    if item.get("key") == "order_category":
                        raw      = (item.get("value") or "").strip()
                        category = _ORDER_CATEGORY_MAP.get(raw.lower(), "")

                        # ── Only set if a valid mapped category found ──────
                        # Empty WC value → leave SO field empty (no default)
                        if category and hasattr(sales_order, "custom_order_category"):
                            sales_order.custom_order_category = category
                        break

            except Exception:
                pass

            original_update(self, woocommerce_order, sales_order)

        SynchroniseWooCommerceOrder.update_sales_order = patched_update
        SynchroniseWooCommerceOrder._taal_patched = True

    except Exception:
        pass