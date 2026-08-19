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

        patched_keys = []
        for wc_key, erp_label in CUSTOM_WC_STATUSES.items():
            if wc_key not in ss.WC_ORDER_STATUS_MAPPING_REVERSE:
                ss.WC_ORDER_STATUS_MAPPING_REVERSE[wc_key] = erp_label
                patched_keys.append(wc_key)
            if hasattr(ss, "WC_ORDER_STATUS_MAPPING"):
                if erp_label not in ss.WC_ORDER_STATUS_MAPPING:
                    ss.WC_ORDER_STATUS_MAPPING[erp_label] = wc_key

        if patched_keys:
            frappe.log_error(
                title="TAAL WC Patch — Applied",
                message=(
                    "Newly patched keys: " + str(patched_keys) + "\n"
                    "All keys now in reverse map: " +
                    str(list(CUSTOM_WC_STATUSES.keys()))
                )
            )

    except Exception:
        frappe.log_error(
            title="TAAL WC Patch — FAILED",
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
            title="TAAL WC Patch — Meta Patch FAILED",
            message=frappe.get_traceback(),
        )