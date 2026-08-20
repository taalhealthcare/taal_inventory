import frappe


# ── Statuses that never produce a Sales Order ──────────────────────────────
SKIP_STATUSES = {
    "checkout-draft",
    "trash",
}


def get_gap_ids():
    """
    Returns list of WooCommerce order IDs that exist in the ID range
    but have no linked Sales Order in ERPNext.
    """
    synced = frappe.db.sql("""
        SELECT
            CAST(woocommerce_id AS UNSIGNED) AS wc_id
        FROM
            `tabSales Order`
        WHERE
            woocommerce_id     IS NOT NULL
            AND woocommerce_id     != ''
            AND woocommerce_server IS NOT NULL
            AND woocommerce_server != ''
            AND CAST(woocommerce_id AS UNSIGNED) > 0
        ORDER BY
            CAST(woocommerce_id AS UNSIGNED) ASC
    """, as_dict=True)

    if not synced:
        return [], 0, 0

    synced_set = set(int(r.wc_id) for r in synced)
    min_id     = min(synced_set)
    max_id     = max(synced_set)
    gap_ids    = sorted(set(range(min_id, max_id + 1)) - synced_set)

    return gap_ids, min_id, max_id


def print_gap_report():
    """
    Call this first to understand the gap before running sync.
    bench execute taal_inventory.utils.bulk_sync_wc_orders.print_gap_report
    """
    synced = frappe.db.sql("""
        SELECT COUNT(*) as cnt
        FROM `tabSales Order`
        WHERE woocommerce_id IS NOT NULL
        AND   woocommerce_id != ''
        AND   woocommerce_server IS NOT NULL
        AND   woocommerce_server != ''
    """, as_dict=True)

    gap_ids, min_id, max_id = get_gap_ids()

    print("=" * 60)
    print("TAAL WooCommerce → Sales Order Sync Gap Report")
    print("=" * 60)
    print("Sales Orders with WC link : " + str(synced[0].cnt))
    print("WC ID range in ERPNext    : " + str(min_id) + " → " + str(max_id))
    print("Missing IDs in range      : " + str(len(gap_ids)))
    print("")

    if gap_ids:
        print("First 30 missing WC IDs:")
        for gid in gap_ids[:30]:
            print("  taalhealthcare.com~" + str(gid))
        if len(gap_ids) > 30:
            print("  ... and " + str(len(gap_ids) - 30) + " more")

    print("=" * 60)


def enqueue_all_missing(wc_server="taalhealthcare.com"):
    """
    Enqueues background sync jobs for ALL missing WC orders.
    bench execute taal_inventory.utils.bulk_sync_wc_orders.enqueue_all_missing
    """
    gap_ids, min_id, max_id = get_gap_ids()

    print("=" * 60)
    print("TAAL Bulk WC Sync — Enqueueing Missing Orders")
    print("=" * 60)
    print("WC ID range : " + str(min_id) + " → " + str(max_id))
    print("Gap count   : " + str(len(gap_ids)))
    print("")

    enqueued = 0
    skipped  = 0

    for wc_id in gap_ids:
        wc_name = wc_server + "~" + str(wc_id)
        frappe.enqueue(
            "woocommerce_fusion.tasks.sync_sales_orders.run_sales_order_sync",
            queue="long",
            timeout=300,
            woocommerce_order_name=wc_name,
            now=False,
            enqueue_after_commit=False,
        )
        enqueued += 1

        if enqueued % 100 == 0:
            frappe.db.commit()
            print("  Enqueued " + str(enqueued) + " / " + str(len(gap_ids)))

    frappe.db.commit()
    print("")
    print("Done")
    print("  Enqueued : " + str(enqueued))
    print("  Skipped  : " + str(skipped))
    print("=" * 60)
    print("Jobs running in background — check progress every 5 min")


def check_progress(wc_server="taalhealthcare.com"):
    """
    Shows current sync progress.
    bench execute taal_inventory.utils.bulk_sync_wc_orders.check_progress
    """
    synced = frappe.db.sql("""
        SELECT COUNT(*) as cnt
        FROM `tabSales Order`
        WHERE woocommerce_id IS NOT NULL
        AND   woocommerce_id != ''
        AND   woocommerce_server IS NOT NULL
        AND   woocommerce_server != ''
    """, as_dict=True)

    gap_ids, min_id, max_id = get_gap_ids()

    print("=" * 60)
    print("TAAL WC Sync Progress")
    print("=" * 60)
    print("Sales Orders synced : " + str(synced[0].cnt))
    print("Still missing       : " + str(len(gap_ids)))
    print("WC ID range         : " + str(min_id) + " → " + str(max_id))
    print("=" * 60)