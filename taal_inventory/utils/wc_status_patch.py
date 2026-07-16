# Safe patch for woocommerce_fusion on Frappe Cloud.
# Injects custom WC order statuses into the upstream mapping dict.
# Called via before_request hook — runs in every gunicorn worker
# before any API method executes.

import json
import frappe

_CUSTOM_WC_STATUSES = {
    "arranged": "Arranged",
    "returned": "Returned",
}

_ORDER_CATEGORY_MAP = {
    "art":  "ART",
    "pep":  "PEP",
    "prep": "PrEP",
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

            # ── Step 1: Extract category from WC meta_data ───────
            extracted_category = ""
            try:
                meta = woocommerce_order.meta_data
                meta_list = (
                    json.loads(meta)
                    if isinstance(meta, str)
                    else (meta or [])
                )
                for item in meta_list:
                    if item.get("key") == "order_category":
                        raw = (item.get("value") or "").strip()
                        extracted_category = _ORDER_CATEGORY_MAP.get(
                            raw.lower(), ""
                        )
                        break
            except Exception:
                pass

            # ── Step 2: Call original (handles status + save()) ──
            original_update(self, woocommerce_order, sales_order)

            # ── Step 3: Write category AFTER save via db.set_value
            # db.set_value bypasses allow_on_submit validation entirely
            # This works whether the SO is draft OR submitted
            try:
                if extracted_category and sales_order.name:
                    current = frappe.db.get_value(
                        "Sales Order",
                        sales_order.name,
                        "custom_order_category"
                    )
                    if current != extracted_category:
                        frappe.db.set_value(
                            "Sales Order",
                            sales_order.name,
                            "custom_order_category",
                            extracted_category,
                            update_modified=False
                        )
            except Exception:
                pass

        SynchroniseWooCommerceOrder.update_sales_order = patched_update
        SynchroniseWooCommerceOrder._taal_patched = True

    except Exception:
        pass