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
    Ensures the item appears in Sales Invoice dropdown.
    """
    item_code = doc.item_code
    if not item_code:
        return

    try:
        if frappe.db.exists("Item", item_code):
            # Update existing native Item
            native_item = frappe.get_doc("Item", item_code)
        else:
            # Create brand new native Item
            native_item = frappe.new_doc("Item")
            native_item.item_code = item_code

        # Sync all relevant fields
        native_item.item_name     = doc.item_name
        native_item.stock_uom     = doc.uom or "Nos"
        native_item.is_stock_item = 1
        native_item.item_group    = "Products"
        native_item.disabled      = 0 if doc.is_active else 1
        native_item.description   = doc.item_name

        native_item.flags.ignore_permissions = True
        native_item.flags.ignore_mandatory   = True
        native_item.save()

        frappe.db.commit()

    except Exception as e:
        frappe.log_error(
            f"Item Master sync failed for {item_code}: {str(e)}",
            "Pharmacy Item Sync Error"
        )