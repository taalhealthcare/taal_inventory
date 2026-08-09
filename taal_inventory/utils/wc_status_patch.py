import frappe

CUSTOM_WC_STATUSES = {
    "arranged": "Arranged",
    "returned": "Returned",
    "cod-return": "COD Return",
}


def apply():
    try:
        from woocommerce_fusion.tasks import sync_sales_orders as ss

        for wc_key, erp_label in CUSTOM_WC_STATUSES.items():
            if wc_key not in ss.WC_ORDER_STATUS_MAPPING_REVERSE:
                ss.WC_ORDER_STATUS_MAPPING_REVERSE[wc_key] = erp_label
            if hasattr(ss, "WC_ORDER_STATUS_MAPPING"):
                if erp_label not in ss.WC_ORDER_STATUS_MAPPING:
                    ss.WC_ORDER_STATUS_MAPPING[erp_label] = wc_key

    except Exception:
        frappe.log_error(
            title="WooCommerce Status Mapping Patch Failed",
            message=frappe.get_traceback(),
        )