app_name        = "taal_inventory"
app_title       = "Taal Inventory"
app_publisher   = "TAAL+ Healthcare"
app_description = "Pharmacy Inventory Management for TAAL+ Healthcare"
app_version     = "1.0.0"
app_license     = "MIT"

before_request = [
    "taal_inventory.utils.wc_status_patch.apply",
    "taal_inventory.utils.erpnext_patches.apply",
]
before_job     = ["taal_inventory.utils.wc_status_patch.apply"]

doctype_js = {
    "Stock Receipt": "public/js/stock_receipt.js",
    "Sales Invoice": "public/js/sales_invoice.js",
}

scheduler_events = {
    "cron": {
        "*/5 * * * *": [
            "taal_inventory.api.woocommerce_category.reconcile_recent_order_categories"
        ],
        "0 7 1 * *": [
            "taal_inventory.utils.gst_sales_dashboard.send_monthly_gst_email"
        ],
    },
    "daily": [
        "taal_inventory.tasks.check_expiry_alerts",
        "taal_inventory.tasks.check_low_stock_daily",
        "taal_inventory.utils.refill_reminder.run_refill_reminder_check",
    ],
}

doc_events = {
    "Sales Invoice": {
        "on_submit": "taal_inventory.events.sales_invoice.on_submit",
        "on_cancel": "taal_inventory.events.sales_invoice.on_cancel",
    },
    "Stock Receipt": {
        "on_submit": "taal_inventory.events.stock_receipt.on_submit",
        "on_cancel": "taal_inventory.events.stock_receipt.on_cancel",
    },
    "Stock Adjustment": {
        "on_submit": "taal_inventory.events.stock_receipt.on_adjustment_submit",
        "on_cancel": "taal_inventory.events.stock_receipt.on_adjustment_cancel",
    },
    "Pharmacy Item": {
        "before_insert": "taal_inventory.events.pharmacy_item.before_insert",
        "after_insert":  "taal_inventory.events.pharmacy_item.after_insert",
        # on_update is a list so sync_service_item_to_master always runs
        # AFTER the existing on_update → _sync_to_item_master call.
        # For service items, sync_service_item_to_master then overrides
        # is_stock_item=0, has_batch_no=0, item_group=Services, and sets
        # the Item Price from service_selling_price. For all other items
        # it is a silent no-op — existing FEFO logic is completely unchanged.
        "on_update": [
            "taal_inventory.events.pharmacy_item.on_update",
            "taal_inventory.events.pharmacy_item.sync_service_item_to_master",
        ],
    },
    "Purchase Invoice": {
        "on_submit": "taal_inventory.events.purchase_invoice.on_submit",
        "on_cancel": "taal_inventory.events.purchase_invoice.on_cancel",
    },
    "Pharmacy Stock Transfer": {
        "on_submit": "taal_inventory.events.pharmacy_stock_transfer.on_submit",
        "on_cancel": "taal_inventory.events.pharmacy_stock_transfer.on_cancel",
    },
}