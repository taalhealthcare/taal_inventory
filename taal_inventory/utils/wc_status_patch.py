import frappe

CUSTOM_WC_STATUSES = {
    "arranged":       "Arranged",
    "returned":       "Returned",
    "cod-return":     "COD Return",
    "cancel-request": "Cancel Request",
}

CUSTOM_SO_WC_STATUS_LABELS = [
    "Arranged",
    "Returned",
    "COD Return",
    "Cancel Request",
]


def apply():
    _patch_wc_mapping()
    _patch_sales_order_meta()


def _patch_wc_mapping():
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
            title="TAAL WC Patch — Mapping Failed",
            message=frappe.get_traceback(),
        )


def _patch_sales_order_meta():
    try:
        meta  = frappe.get_meta("Sales Order")
        field = meta.get_field("woocommerce_status")

        if not field:
            return

        current = field.options or ""
        for label in CUSTOM_SO_WC_STATUS_LABELS:
            if label not in current:
                field.options = current.rstrip("\n") + "\n" + label
                current = field.options

    except Exception:
        frappe.log_error(
            title="TAAL WC Patch — Meta Failed",
            message=frappe.get_traceback(),
        )