import io
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _safe_str(val: Any) -> str:
    if val is None:
        return "N/A"
    if isinstance(val, Decimal):
        return f"{float(val):,.2f}"
    if isinstance(val, float):
        return f"{val:,.2f}"
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d %H:%M")
    return str(val)


def render_report_to_pdf(report_type: str, data: Dict[str, Any], filters: Dict[str, Any]) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )
    styles = getSampleStyleSheet()

    # Custom typography styles
    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#1E3A8A"),
        alignment=0,
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "ReportSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#6B7280"),
        spaceAfter=8,
    )
    filter_box_style = ParagraphStyle(
        "FilterSummary",
        parent=styles["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#374151"),
    )
    h2_style = ParagraphStyle(
        "SectionHeader",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#1E3A8A"),
        spaceBefore=12,
        spaceAfter=6,
    )
    th_style = ParagraphStyle(
        "TableHeader",
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.white,
        alignment=0,
    )
    td_style = ParagraphStyle(
        "TableCell",
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#111827"),
    )

    story = []

    # Title & Subtitle
    title_map = {
        "summary": "DealFlow360 — Executive Summary Report",
        "deals": "DealFlow360 — Deals & Quotations Report",
        "quotations": "DealFlow360 — Deals & Quotations Report",
        "approvals": "DealFlow360 — Approvals & Governance Turnaround",
        "products": "DealFlow360 — Product Sales & Discount Performance",
        "discounts": "DealFlow360 — Sales Rep Discount Exposure & Anomalies",
        "pipeline": "DealFlow360 — Pipeline Funnel & Risk Distribution",
        "risk": "DealFlow360 — Pipeline Funnel & Risk Distribution",
        "fulfillment": "DealFlow360 — Warehouse Fulfillment & Efficiency",
        "billing": "DealFlow360 — Billing, Invoicing & Subscription Metrics",
    }
    report_title = title_map.get(report_type, f"DealFlow360 — {report_type.title()} Report")
    story.append(Paragraph(report_title, title_style))
    story.append(Paragraph("DealFlow360 B2B Sales Operations Platform • Governance Overlay on Odoo", subtitle_style))

    # Filter Box
    filter_desc = f"<b>Period:</b> {filters.get('period', 'N/A')}"
    if filters.get("team_id"):
        filter_desc += f" &nbsp;|&nbsp; <b>Team:</b> {filters['team_id']}"
    if filters.get("rep_id"):
        filter_desc += f" &nbsp;|&nbsp; <b>Rep:</b> {filters['rep_id']}"
    if filters.get("approval_status"):
        filter_desc += f" &nbsp;|&nbsp; <b>Approval Status:</b> {filters['approval_status']}"
    gen_time = _safe_str(data.get("generated_at", datetime.utcnow()))
    filter_desc += f" &nbsp;|&nbsp; <b>Generated:</b> {gen_time}"

    filter_table = Table([[Paragraph(filter_desc, filter_box_style)]], colWidths=[540])
    filter_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F3F4F6")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ])
    )
    story.append(filter_table)
    story.append(Spacer(1, 10))

    def _build_styled_table(headers: List[str], rows: List[List[Any]], col_widths: Optional[List[float]] = None) -> Table:
        table_data = []
        # Header row
        table_data.append([Paragraph(h, th_style) for h in headers])
        # Data rows
        for r in rows:
            table_data.append([Paragraph(_safe_str(c), td_style) for c in r])

        t = Table(table_data, colWidths=col_widths)
        t_style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E3A8A")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
            ("TOPPADDING", (0, 0), (-1, 0), 5),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D1D5DB")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 1), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
        ]
        # Alternating row colors
        for i in range(1, len(table_data)):
            if i % 2 == 0:
                t_style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F9FAFB")))
        t.setStyle(TableStyle(t_style))
        return t

    # Build report sections based on report_type
    if report_type == "summary":
        headers = ["Key Governance & Sales Metric", "Value"]
        rows = [
            ["Quotations Created", data.get("quotations_created", 0)],
            ["Quotations Sent", data.get("quotations_sent", 0)],
            ["Quotations Confirmed", data.get("quotations_confirmed", 0)],
            ["Win Rate", f"{float(data.get('win_rate', 0.0)):.2f}%"],
            ["Confirmed Revenue (One-Time + First Cycle)", f"{float(data.get('revenue_confirmed', 0.0)):,.2f}"],
            ["Average Discount", f"{float(data.get('avg_discount', 0.0)):.2f}%"],
            ["Average Margin", f"{float(data.get('avg_margin', 0.0)):.2f}%"],
            ["Avg Approval Turnaround (Hours)", f"{float(data.get('avg_approval_turnaround_hours', 0.0)):.2f}"],
            ["Pending Approvals Count", data.get("pending_approvals_count", 0)],
        ]
        story.append(Paragraph("Core Performance Indicators", h2_style))
        story.append(_build_styled_table(headers, rows, col_widths=[340, 200]))

    elif report_type in ("deals", "quotations"):
        headers = ["Ref", "Order", "Customer", "Owner", "Status", "Approval", "Amount", "Disc %", "Margin %"]
        items = data.get("items", [])
        rows = []
        for it in items[:40]:  # Limit to 40 in single PDF preview for readability
            rows.append([
                it.get("reference", ""),
                it.get("odoo_order_name", ""),
                it.get("partner_name", "")[:18],
                it.get("owner_odoo_user_id", ""),
                it.get("status", ""),
                it.get("approval_state", ""),
                f"{float(it.get('amount_total', 0.0)):,.0f}",
                f"{float(it.get('order_discount_pct', 0.0)):.1f}%",
                f"{float(it.get('margin_pct', 0.0)):.1f}%",
            ])
        story.append(Paragraph(f"Deals & Quotations List ({len(items)} total)", h2_style))
        story.append(_build_styled_table(headers, rows, col_widths=[60, 60, 110, 45, 65, 75, 55, 35, 35]))

    elif report_type == "approvals":
        # Turnaround by Stage
        story.append(Paragraph("Turnaround by Approval Stage", h2_style))
        h1 = ["Stage", "Total", "Avg Hours", "Approved", "Rejected", "Returned"]
        r1 = []
        for st in data.get("turnaround_by_stage", []):
            r1.append([
                st.get("required_level"), st.get("total_requests"),
                f"{float(st.get('avg_turnaround_hours', 0.0)):.2f}",
                st.get("approved_count"), st.get("rejected_count"), st.get("returned_count")
            ])
        story.append(_build_styled_table(h1, r1, col_widths=[140, 80, 80, 80, 80, 80]))

        # Top Rejection Reasons
        story.append(Paragraph("Top Rejection Reasons", h2_style))
        h2 = ["Reason", "Occurrences"]
        r2 = [[x.get("reason", ""), x.get("count", 0)] for x in data.get("top_rejection_reasons", [])]
        if not r2:
            r2 = [["None recorded", 0]]
        story.append(_build_styled_table(h2, r2, col_widths=[420, 120]))

    elif report_type == "products":
        story.append(Paragraph("Best Selling Products (By Revenue)", h2_style))
        h1 = ["ID", "Product Name", "Category", "Qty Sold", "Revenue", "Avg Disc %"]
        r1 = []
        for p in data.get("best_selling_by_revenue", [])[:10]:
            r1.append([
                p.get("product_id"), p.get("product_name"), p.get("category_id") or "N/A",
                p.get("qty_sold"), f"{float(p.get('revenue', 0.0)):,.2f}", f"{float(p.get('avg_discount_pct', 0.0)):.1f}%"
            ])
        if not r1:
            r1 = [["-", "No products recorded", "-", 0, "0.00", "0.0%"]]
        story.append(_build_styled_table(h1, r1, col_widths=[35, 195, 70, 70, 100, 70]))

        story.append(Paragraph("Most Discounted Products", h2_style))
        r2 = []
        for p in data.get("most_discounted", [])[:10]:
            r2.append([
                p.get("product_id"), p.get("product_name"), p.get("category_id") or "N/A",
                p.get("qty_sold"), f"{float(p.get('revenue', 0.0)):,.2f}", f"{float(p.get('avg_discount_pct', 0.0)):.1f}%"
            ])
        if not r2:
            r2 = [["-", "No products recorded", "-", 0, "0.00", "0.0%"]]
        story.append(_build_styled_table(h1, r2, col_widths=[35, 195, 70, 70, 100, 70]))

    elif report_type == "discounts":
        story.append(Paragraph("Sales Rep Discount Exposure & Compliance", h2_style))
        h1 = ["Rep ID", "Rep Name", "Deals", "Revenue", "Avg Disc %", "Overages", "Anomalies", "Max Disc %"]
        r1 = []
        for s in data.get("rep_stats", []):
            r1.append([
                s.get("rep_id"), s.get("rep_name"), s.get("deals_count"),
                f"{float(s.get('total_revenue', 0.0)):,.0f}",
                f"{float(s.get('avg_weighted_discount_pct', 0.0)):.1f}%",
                s.get("overage_count"), s.get("anomaly_count"),
                f"{float(s.get('max_discount_pct', 0.0)):.1f}%"
            ])
        if not r1:
            r1 = [["-", "No reps recorded", 0, "0", "0.0%", 0, 0, "0.0%"]]
        story.append(_build_styled_table(h1, r1, col_widths=[40, 130, 45, 85, 60, 60, 60, 60]))

        story.append(Paragraph("Company Baseline", h2_style))
        h2 = ["Company Metric", "Value"]
        r2 = [
            ["Company Avg Discount", f"{float(data.get('company_avg_discount_pct', 0.0)):.2f}%"],
            ["Total Discount Anomalies Raised", data.get("total_anomalies", 0)],
            ["Total Policy Overages", data.get("total_overages", 0)],
        ]
        story.append(_build_styled_table(h2, r2, col_widths=[340, 200]))

    elif report_type in ("pipeline", "risk"):
        story.append(Paragraph("Pipeline Funnel by Stage", h2_style))
        h1 = ["Stage", "Deals Count", "Total Value", "Avg Discount %", "Avg Margin %"]
        r1 = []
        for p in data.get("pipeline_funnel", []):
            r1.append([
                p.get("status"), p.get("count"), f"{float(p.get('total_value', 0.0)):,.2f}",
                f"{float(p.get('avg_discount_pct', 0.0)):.1f}%", f"{float(p.get('avg_margin_pct', 0.0)):.1f}%"
            ])
        story.append(_build_styled_table(h1, r1, col_widths=[140, 80, 140, 90, 90]))

        story.append(Paragraph("Risk Severity Distribution", h2_style))
        h2 = ["Severity", "Deals Count", "Total Value", "Avg Risk Score"]
        r2 = []
        for r in data.get("risk_distribution", []):
            r2.append([
                r.get("severity"), r.get("count"), f"{float(r.get('total_value', 0.0)):,.2f}",
                f"{float(r.get('avg_risk_score', 0.0)):.1f}"
            ])
        story.append(_build_styled_table(h2, r2, col_widths=[140, 80, 200, 120]))

    elif report_type == "fulfillment":
        story.append(Paragraph("Warehouse Fulfillment Breakdown", h2_style))
        h1 = ["Warehouse ID", "Warehouse Name", "Shipments", "Allocated Qty", "Shipping Cost"]
        r1 = []
        for w in data.get("warehouse_breakdown", []):
            r1.append([
                w.get("warehouse_id"), w.get("warehouse_name"), w.get("shipment_count"),
                w.get("total_allocated_qty"), f"{float(w.get('total_shipping_cost', 0.0)):,.2f}"
            ])
        if not r1:
            r1 = [["-", "No warehouse plans", 0, 0, "0.00"]]
        story.append(_build_styled_table(h1, r1, col_widths=[70, 170, 100, 100, 100]))

        story.append(Paragraph("Fulfillment Efficiency KPIs", h2_style))
        h2 = ["Fulfillment KPI", "Value"]
        r2 = [
            ["Multi-Warehouse Split Rate", f"{float(data.get('split_rate', 0.0)):.2f}%"],
            ["Backorder Rate", f"{float(data.get('backorder_rate', 0.0)):.2f}%"],
            ["On-Time Fulfillment", f"{float(data.get('on_time_pct', 0.0)):.2f}%"],
            ["Total Fulfillment Plans", data.get("total_plans", 0)],
            ["Total Shipments Executed", data.get("total_shipments", 0)],
        ]
        story.append(_build_styled_table(h2, r2, col_widths=[340, 200]))

    elif report_type == "billing":
        story.append(Paragraph("Billing & Revenue Collection", h2_style))
        h1 = ["Billing Metric", "Amount / Count"]
        r1 = [
            ["Total Invoiced Amount", f"{float(data.get('total_invoiced', 0.0)):,.2f}"],
            ["Total Paid Amount", f"{float(data.get('total_paid', 0.0)):,.2f}"],
            ["Outstanding Balance", f"{float(data.get('total_outstanding', 0.0)):,.2f}"],
            ["Overdue Amount", f"{float(data.get('overdue_amount', 0.0)):,.2f}"],
            ["Overdue Invoices Count", data.get("overdue_count", 0)],
            ["Monthly Recurring Revenue (MRR)", f"{float(data.get('mrr', 0.0)):,.2f}"],
            ["Active Subscriptions Count", data.get("active_subscriptions_count", 0)],
            ["Credit Notes Amount", f"{float(data.get('total_credit_notes_amount', 0.0)):,.2f}"],
            ["Credit Notes Count", data.get("credit_notes_count", 0)],
        ]
        story.append(_build_styled_table(h1, r1, col_widths=[340, 200]))

    else:
        headers = ["Parameter", "Value"]
        rows = [[k, str(v)] for k, v in data.items() if not isinstance(v, (list, dict))]
        story.append(_build_styled_table(headers, rows, col_widths=[240, 300]))

    doc.build(story)
    return buf.getvalue()
