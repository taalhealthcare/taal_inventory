import datetime
import frappe


def apply():
    try:
        import erpnext.stock.utils as stock_utils

        def safe_get_combine_datetime(posting_date, posting_time):
            if posting_time is None:
                posting_time = datetime.datetime.now().time()

            if isinstance(posting_time, str):
                for fmt in ["%H:%M:%S", "%H:%M"]:
                    try:
                        posting_time = datetime.datetime.strptime(
                            posting_time, fmt
                        ).time()
                        break
                    except ValueError:
                        continue
                else:
                    posting_time = datetime.datetime.now().time()

            if isinstance(posting_date, str):
                posting_date = datetime.datetime.strptime(
                    posting_date, "%Y-%m-%d"
                ).date()

            return datetime.datetime.combine(posting_date, posting_time)

        stock_utils.get_combine_datetime = safe_get_combine_datetime

    except Exception as e:
        frappe.logger().warning(
            "taal_inventory patch failed: " + str(e)
        )