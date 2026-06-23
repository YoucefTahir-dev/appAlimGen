from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.colors import black

from apps.core.models import CompanySettings


def generate_invoice_pdf(response, sale):
    company = CompanySettings.objects.first()
    c = canvas.Canvas(response, pagesize=A4)
    width, height = A4
    c.setFont('Helvetica-Bold', 14)
    c.drawString(20 * mm, height - 30 * mm, company.company_name if company else 'El Amine lil Mawad El Ghidhaiya wa Ghayr El Ghidhaiya')
    c.setFont('Helvetica', 9)
    c.drawString(20 * mm, height - 35 * mm, company.address if company else '')
    c.drawString(20 * mm, height - 40 * mm, f'Téléphone: {company.phone if company else ""}')
    c.drawString(20 * mm, height - 45 * mm, f'NIF: {company.tax_number if company else ""} | RC: {company.rc_number if company else ""}')

    c.setFont('Helvetica-Bold', 12)
    c.drawString(130 * mm, height - 30 * mm, f'Facture: {sale.invoice_number}')
    c.setFont('Helvetica', 9)
    c.drawString(130 * mm, height - 35 * mm, f'Date: {sale.created_at.strftime("%d/%m/%Y")}')

    c.setFont('Helvetica-Bold', 10)
    c.drawString(20 * mm, height - 60 * mm, 'Client:')
    c.setFont('Helvetica', 9)
    c.drawString(20 * mm, height - 65 * mm, sale.client.name)
    c.drawString(20 * mm, height - 70 * mm, sale.client.address)
    c.drawString(20 * mm, height - 75 * mm, f'Téléphone: {sale.client.phone}')

    c.setFont('Helvetica-Bold', 10)
    c.drawString(20 * mm, height - 90 * mm, 'Détails des produits')
    c.setFont('Helvetica-Bold', 9)
    c.drawString(20 * mm, height - 100 * mm, 'Produit')
    c.drawString(90 * mm, height - 100 * mm, 'Quantité')
    c.drawString(110 * mm, height - 100 * mm, 'PU')
    c.drawString(140 * mm, height - 100 * mm, 'Total')
    c.line(20 * mm, height - 102 * mm, 190 * mm, height - 102 * mm)

    y = height - 110 * mm
    c.setFont('Helvetica', 9)
    for line in sale.lines.all():
        c.drawString(20 * mm, y, line.product.name)
        c.drawRightString(120 * mm, y, str(line.quantity))
        c.drawRightString(150 * mm, y, f'{line.unit_price:.2f}')
        c.drawRightString(190 * mm, y, f'{line.line_total():.2f}')
        y -= 6 * mm
        if y < 30 * mm:
            c.showPage()
            y = height - 30 * mm

    total_ht = sum(line.line_total() for line in sale.lines.all())
    tax_amount = total_ht * (sale.tax_rate / 100)
    total_ttc = total_ht + tax_amount - sale.discount

    c.line(20 * mm, y - 4 * mm, 190 * mm, y - 4 * mm)
    c.drawString(20 * mm, y - 12 * mm, f'Total HT: {total_ht:.2f} DZD')
    c.drawString(20 * mm, y - 18 * mm, f'TVA ({sale.tax_rate}%): {tax_amount:.2f} DZD')
    c.drawString(20 * mm, y - 24 * mm, f'Remise: {sale.discount:.2f} DZD')
    c.setFont('Helvetica-Bold', 11)
    c.drawString(20 * mm, y - 34 * mm, f'Total TTC: {total_ttc:.2f} DZD')

    c.setFont('Helvetica', 9)
    c.drawString(20 * mm, y - 50 * mm, 'Signature: ___________________________')
    c.save()
