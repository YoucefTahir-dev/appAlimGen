import io

import openpyxl
from django.http import HttpResponse
from django.shortcuts import render
from django.utils.translation import gettext as _
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.accounts.permissions import seller_required
from .dashboard import dashboard_context


def money(value):
    return f"{value:,.2f} DZD".replace(",", " ")


@seller_required
def dashboard(request):
    return render(request, 'core/dashboard.html', dashboard_context(request))


@seller_required
def dashboard_export_excel(request):
    context = dashboard_context(request)
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Dashboard"
    sheet.append([_("Période"), context["start_date"].isoformat(), context["end_date"].isoformat()])
    sheet.append([])
    sheet.append([_("Indicateur"), _("Valeur")])
    for label, value in [
        (_("Chiffre d'affaires du jour"), context["sales_today"]),
        (_("Chiffre d'affaires de la période"), context["period_revenue"]),
        (_("Nombre de ventes"), context["sales_count"]),
        (_("Panier moyen"), context["average_basket"]),
        (_("Gain brut (avant charges)"), context["gross_profit"]),
        (_("Total des charges"), context["expenses_total"]),
        (_("Gain net (après charges)"), context["net_profit"]),
        (_("Valeur du stock"), context["stock_value"]),
        (_("Achats de la période"), context["purchases_total"]),
        (_("Produits vendus"), context["products_sold"]),
        (_("Produits achetés"), context["products_purchased"]),
        (_("Notifications"), context["notification_count"]),
    ]:
        sheet.append([label, value])

    sheet.append([])
    sheet.append([_("Évolution"), _("CA"), _("Achats"), _("Charges"), _("Gain brut"), _("Gain net")])
    trend = context["chart_data"]["trend"]
    for index, label in enumerate(trend["labels"]):
        sheet.append([
            label,
            trend["revenue"][index],
            trend["purchases"][index],
            trend["expenses"][index],
            trend["gross_profit"][index],
            trend["net_profit"][index],
        ])

    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    response = HttpResponse(output.read(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = "attachment; filename=dashboard.xlsx"
    return response


@seller_required
def dashboard_export_pdf(request):
    context = dashboard_context(request)
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = "attachment; filename=dashboard.pdf"
    doc = SimpleDocTemplate(response, pagesize=A4, title="Dashboard")
    styles = getSampleStyleSheet()
    story = [
        Paragraph(_("Tableau de bord décisionnel"), styles["Title"]),
        Paragraph(f"{context['start_date']:%d/%m/%Y} - {context['end_date']:%d/%m/%Y}", styles["Normal"]),
        Spacer(1, 12),
    ]
    indicators = [
        [_("Chiffre d'affaires de la période"), money(context["period_revenue"])],
        [_("Nombre de ventes"), context["sales_count"]],
        [_("Panier moyen"), money(context["average_basket"])],
        [_("Gain brut (avant charges)"), money(context["gross_profit"])],
        [_("Total des charges"), money(context["expenses_total"])],
        [_("Gain net (après charges)"), money(context["net_profit"])],
        [_("Valeur du stock"), money(context["stock_value"])],
        [_("Notifications"), context["notification_count"]],
    ]
    table = Table([[_("Indicateur"), _("Valeur")], *indicators], colWidths=[260, 180])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a2a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([table, Spacer(1, 12), Paragraph(_("Séries des graphiques"), styles["Heading2"])])
    trend = context["chart_data"]["trend"]
    chart_rows = [[_("Date"), _("CA"), _("Achats"), _("Charges"), _("Gain brut"), _("Gain net")]]
    for index, label in enumerate(trend["labels"][:25]):
        chart_rows.append([
            label,
            trend["revenue"][index],
            trend["purchases"][index],
            trend["expenses"][index],
            trend["gross_profit"][index],
            trend["net_profit"][index],
        ])
    chart_table = Table(chart_rows, repeatRows=1)
    chart_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9ecef")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
    ]))
    story.append(chart_table)
    doc.build(story)
    return response
