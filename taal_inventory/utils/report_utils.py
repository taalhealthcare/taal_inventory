import frappe
import json


@frappe.whitelist()
def email_report(report_name, filters, to_email):
    """
    Called from JS Email Report button.
    Runs the report and emails HTML table to given address.
    """
    try:
        filters_dict = (
            json.loads(filters)
            if isinstance(filters, str)
            else (filters or {})
        )

        # Remove UI-only filters before running
        filters_dict.pop("show_analytics", None)

        # Run the report
        from frappe.desk.query_report import run
        result = run(report_name, filters=filters_dict)

        columns    = result.get("columns", [])
        result_data = result.get("result",  [])

        # Build HTML
        html = _build_html_table(report_name, columns, result_data)

        frappe.sendmail(
            recipients=[to_email],
            subject=f"TAAL+ Healthcare — {report_name}",
            message=html,
            delayed=False
        )

        return {"success": True}

    except Exception as e:
        frappe.log_error(str(e), "Email Report Error")
        return {"success": False, "error": str(e)}


def _build_html_table(report_name, columns, data):
    """Build a clean HTML table for email."""

    header_cells = ""
    for col in columns:
        label = col.get("label") if isinstance(col, dict) else str(col)
        header_cells += (
            "<th style='background:#1F3864;color:#fff;"
            "padding:8px 12px;text-align:left;"
            "font-size:12px;'>"
            + label + "</th>"
        )

    body_rows = ""
    for i, row in enumerate(data):
        if not row:
            continue
        bg = "#ffffff" if i % 2 == 0 else "#EBF3FB"
        cells = ""
        if isinstance(row, dict):
            for col in columns:
                key = (
                    col.get("fieldname")
                    if isinstance(col, dict)
                    else ""
                )
                val = row.get(key, "")
                # Strip HTML tags from badge fields
                if isinstance(val, str) and "<span" in val:
                    import re
                    val = re.sub(r'<[^>]+>', '', val)
                cells += (
                    "<td style='padding:7px 12px;"
                    "font-size:12px;color:#2c3e50;"
                    "border-bottom:1px solid #eee;'>"
                    + str(val or "") + "</td>"
                )
        else:
            for val in row:
                cells += (
                    "<td style='padding:7px 12px;"
                    "font-size:12px;color:#2c3e50;"
                    "border-bottom:1px solid #eee;'>"
                    + str(val or "") + "</td>"
                )
        body_rows += (
            "<tr style='background:" + bg + ";'>"
            + cells + "</tr>"
        )

    return f"""
    <div style="font-family:Arial,sans-serif;max-width:900px;margin:0 auto;">
        <div style="background:#1F3864;padding:20px 24px;
                    border-radius:8px 8px 0 0;">
            <h2 style="color:#fff;margin:0;font-size:18px;">
                TAAL+ Healthcare
            </h2>
            <p style="color:#9EC8F5;margin:4px 0 0 0;font-size:13px;">
                {report_name}
            </p>
        </div>
        <table style="width:100%;border-collapse:collapse;
                      border:1px solid #ddd;">
            <thead>
                <tr>{header_cells}</tr>
            </thead>
            <tbody>{body_rows}</tbody>
        </table>
        <div style="background:#f8f9fa;padding:10px 24px;
                    border-top:1px solid #ddd;
                    border-radius:0 0 8px 8px;">
            <p style="color:#888;font-size:11px;margin:0;">
                Sent automatically by TAAL+ Healthcare Inventory System
            </p>
        </div>
    </div>
    """