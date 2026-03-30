import frappe


def create_dashboard_page():
    """
    Creates the Inventory Dashboard page in the database
    automatically on every migrate.
    Does not require Developer Mode.
    """
    if frappe.db.exists("Page", "inventory-dashboard"):
        # Already exists — just ensure title is correct
        frappe.db.set_value(
            "Page",
            "inventory-dashboard",
            "title",
            "Inventory Dashboard"
        )
        frappe.db.commit()
        return

    try:
        doc = frappe.get_doc({
            "doctype":     "Page",
            "name":        "inventory-dashboard",
            "page_name":   "inventory-dashboard",
            "module":      "Taal Inventory",
            "title":       "Inventory Dashboard",
            "system_page": 0,
            "roles":       []
        })
        doc.flags.ignore_permissions    = True
        doc.flags.ignore_mandatory      = True
        doc.flags.ignore_validate       = True
        doc.flags.ignore_links          = True
        doc.flags.ignore_if_duplicate   = True
        doc.insert()
        frappe.db.commit()
        frappe.logger().info(
            "Inventory Dashboard page created successfully."
        )

    except Exception as e:
        frappe.logger().error(
            "Failed to create Inventory Dashboard page: " + str(e)
        )