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
    Passes HSN/SAC Code to satisfy India Compliance validation.
    """
    item_code = doc.item_code
    if not item_code:
        return

    # HSN/SAC is mandatory due to India Compliance app
    hsn_code = doc.hsn_sac_code or ""
    if not hsn_code:
        frappe.msgprint(
            "⚠ HSN/SAC Code is missing in Pharmacy Item. "
            "Native Item Master was NOT created. "
            "Please add HSN/SAC Code and save again.",
            title="Item Master Sync Warning",
            indicator="orange"
        )
        return

    try:
        if frappe.db.exists("Item", item_code):
            # Update existing native Item
            native_item = frappe.get_doc("Item", item_code)
        else:
            # Create brand new native Item
            native_item = frappe.new_doc("Item")
            native_item.item_code = item_code

        # Sync all fields
        native_item.item_name        = doc.item_name
        native_item.stock_uom        = doc.uom or "Nos"
        native_item.is_stock_item    = 1
        native_item.item_group       = "Products"
        native_item.disabled         = 0 if doc.is_active else 1
        native_item.description      = doc.item_name

        # India Compliance mandatory field
        native_item.gst_hsn_code     = hsn_code

        native_item.flags.ignore_permissions = True
        native_item.flags.ignore_mandatory   = True
        native_item.save()

        frappe.db.commit()

        frappe.msgprint(
            f"✅ Item Master synced successfully for {item_code}.",
            alert=True
        )

    except Exception as e:
        frappe.log_error(
            f"Item Master sync failed for {item_code}: {str(e)}",
            "Pharmacy Item Sync Error"
        )
        frappe.msgprint(
            f"⚠ Item Master sync failed: {str(e)}. "
            f"Please check Error Log for details.",
            title="Sync Error",
            indicator="red"
        )