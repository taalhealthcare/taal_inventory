import frappe


WC_SERVER_NAME = "taalhealthcare.com"
SKIP_STATUSES  = {"checkout-draft", "trash"}


def get_wc_api_client():
    """Get authenticated WooCommerce API client from woocommerce_fusion."""
    from woocommerce_fusion.woocommerce.woocommerce_api import (
        WooCommerceAPI,
    )
    wc_server = frappe.get_doc("WooCommerce Server", WC_SERVER_NAME)
    return WooCommerceAPI(wc_server)


def fetch_all_wc_order_ids():
    """
    Fetches ALL WC order IDs directly from WooCommerce REST API.
    Paginates automatically — handles all 1163 orders.
    Returns list of dicts: [{id, status}, ...]
    """
    from woocommerce import API

    wc_server = frappe.get_doc("WooCommerce Server", WC_SERVER_NAME)

    wcapi = API(
        url=wc_server.woocommerce_server_url,
        consumer_key=wc_server.api_consumer_key,
        consumer_secret=wc_server.api_consumer_secret,
        version="wc/v3",
        timeout=30,
    )

    all_orders = []
    page       = 1
    per_page   = 100

    print("Fetching all WC orders from API...")

    while True:
        response = wcapi.get(
            "orders",
            params={
                "per_page": per_page,
                "page":     page,
                "fields":   "id,status",
                "orderby":  "id",
                "order":    "asc",
            }
        )

        if response.status_code != 200:
            print("API error on page " + str(page) + ": " + str(response.status_code))
            break

        batch = response.json()

        if not batch:
            break

        all_orders.extend(batch)
        print("  Page " + str(page) + " → " + str(len(batch)) + " orders fetched (total: " + str(len(all_orders)) + ")")

        if len(batch) < per_page:
            break

        page += 1

    print("Total fetched from WC API: " + str(len(all_orders)))
    return all_orders


def get_synced_wc_ids():
    """Get all WC IDs that already have a Sales Order in ERPNext."""
    rows = frappe.db.sql("""
        SELECT woocommerce_id
        FROM   `tabSales Order`
        WHERE  woocommerce_id     IS NOT NULL
        AND    woocommerce_id     != ''
        AND    woocommerce_server IS NOT NULL
        AND    woocommerce_server != ''
    """, as_dict=True)
    return set(str(r.woocommerce_id) for r in rows)


def print_gap_report():
    """
    Full gap report — fetches real data from WC API.
    Usage from System Console:
        frappe.enqueue(
            "taal_inventory.utils.bulk_sync_wc_orders.print_gap_report",
            queue="long", timeout=600, now=False
        )
    """
    all_wc_orders = fetch_all_wc_order_ids()
    synced_ids    = get_synced_wc_ids()

    missing = [
        o for o in all_wc_orders
        if str(o["id"]) not in synced_ids
    ]

    status_count = {}
    for o in missing:
        s = o.get("status") or "(blank)"
        status_count[s] = status_count.get(s, 0) + 1

    print("=" * 60)
    print("TAAL WC → SO Gap Report")
    print("=" * 60)
    print("Total WC orders (from API) : " + str(len(all_wc_orders)))
    print("Already synced SOs         : " + str(len(synced_ids)))
    print("Missing                    : " + str(len(missing)))
    print("")
    print("Missing by WC status:")
    for status, count in sorted(status_count.items(), key=lambda x: -x[1]):
        skip_note = " ← WILL SKIP" if status in SKIP_STATUSES else ""
        print("  " + str(status) + " : " + str(count) + skip_note)
    print("=" * 60)

    # Log to Error Log so result persists
    frappe.log_error(
        title="TAAL Bulk Sync — Gap Report",
        message=(
            "Total WC orders : " + str(len(all_wc_orders)) + "\n"
            "Synced SOs      : " + str(len(synced_ids)) + "\n"
            "Missing         : " + str(len(missing)) + "\n\n"
            "Missing by status:\n" +
            "\n".join(
                "  " + s + " : " + str(c)
                for s, c in sorted(status_count.items(), key=lambda x: -x[1])
            )
        )
    )


def enqueue_all_missing():
    """
    Fetches all WC orders from API, compares with ERPNext,
    and enqueues sync for every missing one.

    Trigger from System Console:
        frappe.enqueue(
            "taal_inventory.utils.bulk_sync_wc_orders.enqueue_all_missing",
            queue="long", timeout=1800, now=False
        )
    """
    all_wc_orders = fetch_all_wc_order_ids()
    synced_ids    = get_synced_wc_ids()

    missing = [
        o for o in all_wc_orders
        if str(o["id"]) not in synced_ids
        and o.get("status") not in SKIP_STATUSES
    ]

    skippable = [
        o for o in all_wc_orders
        if str(o["id"]) not in synced_ids
        and o.get("status") in SKIP_STATUSES
    ]

    print("=" * 60)
    print("TAAL Bulk WC Sync")
    print("=" * 60)
    print("Total WC orders : " + str(len(all_wc_orders)))
    print("Already synced  : " + str(len(synced_ids)))
    print("To enqueue      : " + str(len(missing)))
    print("Skipped         : " + str(len(skippable)) + " (draft/trash)")
    print("")

    enqueued = 0
    for order in missing:
        wc_name = WC_SERVER_NAME + "~" + str(order["id"])
        frappe.enqueue(
            "woocommerce_fusion.tasks.sync_sales_orders.run_sales_order_sync",
            queue="long",
            timeout=300,
            woocommerce_order_name=wc_name,
            now=False,
        )
        enqueued += 1

        if enqueued % 100 == 0:
            frappe.db.commit()
            print("  Enqueued " + str(enqueued) + " / " + str(len(missing)))

    frappe.db.commit()
    print("")
    print("Done — " + str(enqueued) + " jobs enqueued")
    print("Monitor progress with check_progress()")
    print("=" * 60)

    frappe.log_error(
        title="TAAL Bulk Sync — Jobs Enqueued",
        message=(
            "Enqueued : " + str(enqueued) + "\n"
            "Skipped  : " + str(len(skippable)) + "\n"
            "Total WC : " + str(len(all_wc_orders))
        )
    )


def check_progress():
    """Check current sync progress."""
    synced = frappe.db.sql("""
        SELECT COUNT(*) as cnt
        FROM `tabSales Order`
        WHERE woocommerce_id IS NOT NULL
        AND   woocommerce_id != ''
        AND   woocommerce_server IS NOT NULL
        AND   woocommerce_server != ''
    """, as_dict=True)

    print("=" * 60)
    print("Sales Orders synced: " + str(synced[0].cnt))
    print("=" * 60)