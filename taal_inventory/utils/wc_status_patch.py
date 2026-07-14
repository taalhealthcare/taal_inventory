# Safe patch for woocommerce_fusion on Frappe Cloud.
# Injects custom WC order statuses into the upstream mapping dict.
# Called via before_request hook — runs in every gunicorn worker
# before any API method executes.

_CUSTOM_WC_STATUSES = {
    "arranged": "Arranged",
}

def apply():
    try:
        from woocommerce_fusion.tasks import sync_sales_orders as ss

        for wc_key, erp_label in _CUSTOM_WC_STATUSES.items():

            # Patch the reverse map (wc_status → erp_label)
            # This is what line 241 uses — the one that was crashing
            if wc_key not in ss.WC_ORDER_STATUS_MAPPING_REVERSE:
                ss.WC_ORDER_STATUS_MAPPING_REVERSE[wc_key] = erp_label

            # Patch the forward map (erp_label → wc_status) if it exists
            if hasattr(ss, "WC_ORDER_STATUS_MAPPING"):
                if erp_label not in ss.WC_ORDER_STATUS_MAPPING:
                    ss.WC_ORDER_STATUS_MAPPING[erp_label] = wc_key

    except Exception:
        # Never raise from a patch — must not crash user requests
        pass