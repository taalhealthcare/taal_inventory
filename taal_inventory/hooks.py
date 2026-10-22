app_name        = "taal_inventory"
app_title       = "Taal Inventory"
app_publisher   = "TAAL+ Healthcare"
app_description = "Pharmacy Inventory Management for TAAL+ Healthcare"
app_version     = "1.0.0"
app_license     = "MIT"

# Custom fields owned by this app. Installed/updated by `bench migrate`
# (or `bench --site <site> import-fixtures`). Currently:
#   Sales Invoice Item: custom_pharmacy_batch, custom_batch_expiry,
#                       custom_batch_available
fixtures = [
    {
        "dt": "Custom Field",
        "filters": [["name", "in", [
            "Sales Invoice Item-custom_pharmacy_batch",
            "Sales Invoice Item-custom_batch_expiry",
            "Sales Invoice Item-custom_batch_available",
        ]]],
    },
]

before_request = [
    "taal_inventory.utils.wc_status_patch.apply",
    "taal_inventory.utils.erpnext_patches.apply",
]
before_job     = ["taal_inventory.utils.wc_status_patch.apply"]

doctype_js = {
    "Stock Receipt":  "public/js/stock_receipt.js",
    "Sales Invoice":  "public/js/sales_invoice.js",
    "Pharmacy Item":  "public/js/pharmacy_item.js",
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
        # Flips past-expiry Active batches to Expired, then self-heals any
        # Pharmacy Item whose Total Stock has drifted from its Stock by
        # Company rows or from the live Active-batch total.
        "taal_inventory.utils.company_stock.daily_stock_maintenance",
    ],
}

doc_events = {
    # ── Transactions ─────────────────────────────────────────────────────────
    # Each of these already calls recalculate_company_stock() internally,
    # which now routes through the full rebuild in utils/company_stock.py.
    "Sales Invoice": {
        # Batch is chosen by the user per row (custom_pharmacy_batch).
        # validate: fills nearest-expiry batch into any empty row, then
        # checks every batch belongs to the row's item + doc.company, is
        # Active and unexpired, and that total qty per batch across rows
        # fits its remaining_qty. Runs on save and again on submit.
        "validate":  "taal_inventory.events.sales_invoice.validate",
        "on_submit": "taal_inventory.events.sales_invoice.on_submit",
        "on_cancel": "taal_inventory.events.sales_invoice.on_cancel",
    },
    "Purchase Invoice": {
        # Pharmacy-only guard: blocks any item that has no Pharmacy Item
        # record. Runs on every save, draft included.
        "validate":  "taal_inventory.events.purchase_invoice.validate",
        "on_submit": "taal_inventory.events.purchase_invoice.on_submit",
        "on_cancel": "taal_inventory.events.purchase_invoice.on_cancel",
    },
    "Stock Receipt": {
        "on_submit": "taal_inventory.events.stock_receipt.on_submit",
        "on_cancel": "taal_inventory.events.stock_receipt.on_cancel",
    },
    "Stock Adjustment": {
        "on_submit": "taal_inventory.events.stock_receipt.on_adjustment_submit",
        "on_cancel": "taal_inventory.events.stock_receipt.on_adjustment_cancel",
    },
    "Pharmacy Stock Transfer": {
        "on_submit": "taal_inventory.events.pharmacy_stock_transfer.on_submit",
        "on_cancel": "taal_inventory.events.pharmacy_stock_transfer.on_cancel",
    },

    # ── Pharmacy Batch: the source of truth for all stock ─────────────────────
    # Safety net for MANUAL batch edits in the UI. on_update also fires on
    # insert, so a separate after_insert entry would only duplicate work.
    "Pharmacy Batch": {
        "validate":  "taal_inventory.utils.company_stock.validate_batch",
        "on_update": "taal_inventory.utils.company_stock.on_batch_change",
        "on_trash":  "taal_inventory.utils.company_stock.on_batch_trash",
    },

    "Pharmacy Item": {
        "before_insert": "taal_inventory.events.pharmacy_item.before_insert",
        "after_insert":  "taal_inventory.events.pharmacy_item.after_insert",
        # on_update is a list so sync_service_item_to_master always runs
        # AFTER the existing on_update → _sync_to_item_master call.
        # Both entries return early when the save came from a stock rebuild
        # (doc.flags.ignore_taal_item_sync), so refreshing stock never
        # re-runs the Item Master / Item Price sync.
        "on_update": [
            "taal_inventory.events.pharmacy_item.on_update",
            "taal_inventory.events.pharmacy_item.sync_service_item_to_master",
        ],
    },
}