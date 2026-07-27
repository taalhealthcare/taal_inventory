import frappe


def after_insert(doc, method):
    """Triggered after new Pharmacy Item is created."""
    _sync_to_item_master(doc)


def on_update(doc, method):
    """Triggered after Pharmacy Item is saved or updated."""
    _sync_to_item_master(doc)


def _sync_to_item_master(doc):
    """
    Auto-create or update ERPNext native Item Master
    whenever a Pharmacy Item is saved.

    Syncs:
    - item_name, uom, item_group, description
    - gst_hsn_code (mandatory for India Compliance)
    - is_sales_item, is_stock_item, is_purchase_item, disabled
    - Item Price (Standard Selling) from FEFO batch if available

    Barcode sync: skipped — no barcode workflow implemented yet
    WC link: read-only check — shows warning if not linked, no writes
    """
    item_code = doc.item_code
    if not item_code:
        return

    # ── HSN/SAC mandatory for India Compliance ────────────────────────────────
    hsn_code = doc.hsn_sac_code or ""
    if not hsn_code:
        frappe.msgprint(
            "HSN/SAC Code is missing in Pharmacy Item. "
            "Item Master was NOT synced. "
            "Please add HSN/SAC Code and save again.",
            title="Item Master Sync Warning",
            indicator="orange"
        )
        return

    # ── Map product_type to item_group ────────────────────────────────────────
    product_type_map = {
        "Medicine":      "Medicines",
        "Surgical":      "Surgical Products",
        "Consumable":    "Consumables",
        "FMCG":          "FMCG",
        "Nutraceutical": "Nutraceuticals",
        "Ayurvedic":     "Ayurvedic Products",
        "Diagnostic":    "Diagnostic Items",
        "Equipment":     "Medical Equipment",
    }

    item_group = product_type_map.get(doc.product_type or "", "Products")

    # Verify item_group exists — fall back to Products if not
    if not frappe.db.exists("Item Group", item_group):
        item_group = "Products"

    try:
        # ── Create or update Item Master ──────────────────────────────────────
        if frappe.db.exists("Item", item_code):
            native_item = frappe.get_doc("Item", item_code)
            is_new      = False
        else:
            native_item           = frappe.new_doc("Item")
            native_item.item_code = item_code
            is_new                = True

        native_item.item_name        = doc.item_name or item_code
        native_item.stock_uom        = doc.uom or "Nos"
        native_item.is_stock_item    = 1
        native_item.is_sales_item    = 1
        native_item.is_purchase_item = 1
        native_item.item_group       = item_group
        native_item.disabled         = 0 if doc.is_active else 1
        native_item.description      = doc.item_name or item_code
        native_item.gst_hsn_code     = hsn_code

        # Barcode sync intentionally skipped
        # Will be added when barcode workflow is implemented

        native_item.flags.ignore_permissions = True
        native_item.flags.ignore_mandatory   = True
        native_item.save()

        frappe.db.commit()

        # ── Sync Item Price (Standard Selling) from FEFO batch ────────────────
        today = frappe.utils.today()

        fefo_batches = frappe.get_all(
            "Pharmacy Batch",
            filters={
                "item":          doc.name,
                "status":        "Active",
                "remaining_qty": [">", 0],
                "expiry_date":   [">", today]
            },
            fields=["selling_price"],
            order_by="expiry_date ASC",
            limit_page_length=1
        )

        if fefo_batches:
            selling_price = float(fefo_batches[0].selling_price or 0)

            if selling_price > 0:
                existing_ip = frappe.db.get_value(
                    "Item Price",
                    {
                        "item_code":  item_code,
                        "price_list": "Standard Selling",
                        "selling":    1
                    },
                    ["name", "price_list_rate"],
                    as_dict=True
                )

                if existing_ip:
                    current_rate = float(existing_ip.price_list_rate or 0)
                    if abs(current_rate - selling_price) > 0.01:
                        frappe.db.set_value(
                            "Item Price",
                            existing_ip.name,
                            "price_list_rate",
                            selling_price
                        )
                        frappe.db.commit()
                else:
                    new_ip = frappe.get_doc({
                        "doctype":         "Item Price",
                        "item_code":       item_code,
                        "price_list":      "Standard Selling",
                        "price_list_rate": selling_price,
                        "selling":         1,
                        "currency":        "INR"
                    })
                    new_ip.flags.ignore_permissions = True
                    new_ip.insert()
                    frappe.db.commit()

        # ── WC Link check — READ ONLY, no writes ──────────────────────────────
        wc_linked = frappe.db.get_value(
            "Item WooCommerce Server",
            {"parent": item_code},
            "name"
        )

        # ── Summary message ───────────────────────────────────────────────────
        action = "Created" if is_new else "Updated"

        if is_new and not wc_linked:
            frappe.msgprint(
                "Item Master created for " + item_code + ". "
                "WooCommerce product is not yet linked — "
                "please create a product on taalhealthcare.com "
                "and add the WC Server row in Item Master.",
                title="Item Master Synced",
                indicator="orange"
            )
        else:
            frappe.msgprint(
                action + " Item Master: " + item_code,
                alert=True
            )

    except Exception as e:
        frappe.log_error(
            "Item Master sync failed for " + str(item_code) + ": " + str(e),
            "Pharmacy Item Sync Error"
        )
        frappe.msgprint(
            "Item Master sync failed: " + str(e) + ". "
            "Please check Error Log for details.",
            title="Sync Error",
            indicator="red"
        )