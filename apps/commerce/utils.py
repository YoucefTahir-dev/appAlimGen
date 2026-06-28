import io
from decimal import Decimal
from pathlib import Path

import qrcode
from django.conf import settings
from django.contrib.staticfiles import finders
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from apps.core.models import CompanySettings


COMPANY_NAME_FR = 'El Amine lil Mawad El Ghidhaiya wa Ghayr El Ghidhaiya'
COMPANY_NAME_AR = 'الأمين للمواد الغذائية و غير الغذائية'
BRAND_COLOR = colors.HexColor('#163b2f')
GOLD_COLOR = colors.HexColor('#caa45d')


def register_unicode_font():
    candidates = [
        Path(settings.BASE_DIR) / 'static' / 'fonts' / 'DejaVuSans.ttf',
        Path('C:/Windows/Fonts/arial.ttf'),
        Path('C:/Windows/Fonts/tahoma.ttf'),
        Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
        Path('/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf'),
    ]
    for candidate in candidates:
        if candidate.exists():
            pdfmetrics.registerFont(TTFont('ERPUnicode', str(candidate)))
            return 'ERPUnicode'
    return 'Helvetica'


def format_arabic(text):
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def money(value):
    return f'{Decimal(value):,.2f}'.replace(',', ' ') + ' DZD'


def amount_to_french_words(amount):
    units = [
        'zéro', 'un', 'deux', 'trois', 'quatre', 'cinq', 'six', 'sept', 'huit', 'neuf',
        'dix', 'onze', 'douze', 'treize', 'quatorze', 'quinze', 'seize',
    ]
    tens = {
        20: 'vingt',
        30: 'trente',
        40: 'quarante',
        50: 'cinquante',
        60: 'soixante',
        80: 'quatre-vingt',
    }

    def under_hundred(number):
        if number < 17:
            return units[number]
        if number < 20:
            return 'dix-' + units[number - 10]
        if number < 70:
            ten = number // 10 * 10
            rest = number % 10
            return tens[ten] if rest == 0 else f'{tens[ten]}-{units[rest]}'
        if number < 80:
            return 'soixante-' + under_hundred(number - 60)
        if number < 100:
            return tens[80] if number == 80 else f'{tens[80]}-{under_hundred(number - 80)}'
        return ''

    def words(number):
        number = int(number)
        if number < 100:
            return under_hundred(number)
        if number < 1000:
            hundred = number // 100
            rest = number % 100
            prefix = 'cent' if hundred == 1 else f'{units[hundred]} cent'
            return prefix if rest == 0 else f'{prefix} {under_hundred(rest)}'
        if number < 1_000_000:
            thousand = number // 1000
            rest = number % 1000
            prefix = 'mille' if thousand == 1 else f'{words(thousand)} mille'
            return prefix if rest == 0 else f'{prefix} {words(rest)}'
        return str(number)

    integer_amount = int(Decimal(amount).quantize(Decimal('1')))
    return f'{words(integer_amount).capitalize()} dinars algériens seulement.'


def get_company_logo_path(company):
    if company and company.logo:
        try:
            return company.logo.path
        except Exception:
            pass
    static_logo = finders.find('img/logo.png')
    return static_logo


def build_invoice_context(sale):
    company = CompanySettings.objects.first()
    lines = list(sale.lines.select_related('product').all())
    tax_rate = Decimal(str(sale.tax_rate or 0))
    discount = Decimal(str(sale.discount or 0))
    total_ht = sum((line.line_total() for line in lines), Decimal('0.00'))
    tax_amount = total_ht * (tax_rate / Decimal('100'))
    total_ttc = total_ht + tax_amount - discount
    return {
        'company': company,
        'company_name_fr': company.company_name if company and company.company_name else COMPANY_NAME_FR,
        'company_name_ar': COMPANY_NAME_AR,
        'sale': sale,
        'lines': lines,
        'tax_rate': tax_rate,
        'discount': discount,
        'total_ht': total_ht,
        'tax_amount': tax_amount,
        'total_ttc': total_ttc,
        'amount_words': amount_to_french_words(total_ttc),
    }


def qr_code_image_reader(sale, total_ttc):
    payload = (
        f'Facture: {sale.invoice_number}\n'
        f'Client: {sale.client.name}\n'
        f'Total TTC: {money(total_ttc)}\n'
        f'Date: {sale.created_at:%d/%m/%Y}'
    )
    img = qrcode.make(payload)
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    return buffer


def generate_invoice_pdf(response, sale):
    context = build_invoice_context(sale)
    font_name = register_unicode_font()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='ERPTitle', fontName=font_name, fontSize=18, leading=22, textColor=BRAND_COLOR))
    styles.add(ParagraphStyle(name='ERPSubTitle', fontName=font_name, fontSize=10, leading=13))
    styles.add(ParagraphStyle(name='ERPArabic', fontName=font_name, fontSize=13, leading=16, alignment=TA_RIGHT, textColor=BRAND_COLOR))
    styles.add(ParagraphStyle(name='ERPBoxTitle', fontName=font_name, fontSize=16, leading=20, alignment=TA_CENTER, textColor=colors.white))
    styles.add(ParagraphStyle(name='ERPSmall', fontName=font_name, fontSize=8, leading=10))

    doc = SimpleDocTemplate(
        response,
        pagesize=A4,
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=f"Facture {sale.invoice_number}",
    )
    story = []
    company = context['company']
    logo_path = get_company_logo_path(company)

    logo_cell = ''
    if logo_path:
        try:
            logo_cell = Image(logo_path, width=28 * mm, height=28 * mm, kind='proportional')
        except Exception:
            logo_cell = ''

    company_lines = [
        Paragraph(f"<b>{context['company_name_fr']}</b>", styles['ERPTitle']),
        Paragraph(format_arabic(context['company_name_ar']), styles['ERPArabic']),
        Paragraph(f"Adresse : {company.address if company else ''}", styles['ERPSubTitle']),
        Paragraph(f"Téléphone : {company.phone if company else ''} | Email : {company.email if company else ''}", styles['ERPSubTitle']),
        Paragraph(f"RC : {company.rc_number if company else ''} | NIF : {company.tax_number if company else ''}", styles['ERPSubTitle']),
    ]
    invoice_box = Table(
        [
            [Paragraph('<b>FACTURE</b>', styles['ERPBoxTitle'])],
            [Paragraph(f"<b>N° :</b> {sale.invoice_number}", styles['ERPSubTitle'])],
            [Paragraph(f"<b>Date :</b> {sale.created_at:%d/%m/%Y}", styles['ERPSubTitle'])],
            [Paragraph('<b>Statut :</b> Validée', styles['ERPSubTitle'])],
        ],
        colWidths=[46 * mm],
    )
    invoice_box.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, 0), BRAND_COLOR),
        ('BOX', (0, 0), (-1, -1), 0.8, BRAND_COLOR),
        ('INNERGRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#d8dee4')),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    header = Table([[logo_cell, company_lines, invoice_box]], colWidths=[32 * mm, 96 * mm, 50 * mm])
    header.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    story.extend([header, Spacer(1, 10 * mm)])

    client = sale.client
    client_table = Table(
        [[
            Paragraph(
                f"<b>Client</b><br/>{client.name}<br/>Téléphone : {client.phone}<br/>Adresse : {client.address}<br/>NIF : {client.tax_number}",
                styles['ERPSubTitle'],
            )
        ]],
        colWidths=[178 * mm],
    )
    client_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f5f7f9')),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#ccd6dd')),
        ('PADDING', (0, 0), (-1, -1), 8),
    ]))
    story.extend([client_table, Spacer(1, 7 * mm)])

    table_data = [['N°', 'Produit', 'Qté', 'PU', 'TVA', 'Montant HT', 'TTC']]
    for index, line in enumerate(context['lines'], start=1):
        table_data.append([
            str(index),
            Paragraph(line.product.name, styles['ERPSmall']),
            str(line.quantity),
            money(line.unit_price),
            f"{context['tax_rate']}%",
            money(line.line_total()),
            money(line.line_total() + (line.line_total() * context['tax_rate'] / Decimal('100'))),
        ])

    products_table = Table(table_data, colWidths=[9 * mm, 63 * mm, 15 * mm, 25 * mm, 18 * mm, 34 * mm, 20 * mm], repeatRows=1)
    products_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), BRAND_COLOR),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#cbd5dc')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fbfcfd')]),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 1), (0, -1), 'CENTER'),
        ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),
        ('PADDING', (0, 0), (-1, -1), 5),
    ]))
    story.extend([products_table, Spacer(1, 7 * mm)])

    totals_table = Table(
        [
            ['Total HT', money(context['total_ht'])],
            ['TVA', money(context['tax_amount'])],
            ['Remise', money(sale.discount)],
            ['Net à payer', money(context['total_ttc'])],
        ],
        colWidths=[35 * mm, 38 * mm],
        hAlign='RIGHT',
    )
    totals_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#cbd5dc')),
        ('BACKGROUND', (0, 3), (-1, 3), GOLD_COLOR),
        ('TEXTCOLOR', (0, 3), (-1, 3), colors.white),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(totals_table)
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph(f"Arrêtée la présente facture à la somme de : <b>{context['amount_words']}</b>", styles['ERPSubTitle']))

    qr_buffer = qr_code_image_reader(sale, context['total_ttc'])
    footer = Table(
        [[
            Paragraph('Signature et cachet<br/><br/>____________________________', styles['ERPSubTitle']),
            Image(qr_buffer, width=26 * mm, height=26 * mm),
            Paragraph('Merci pour votre confiance.<br/>Téléphone : ' + (company.phone if company else '') + '<br/>Email : ' + (company.email if company else ''), styles['ERPSubTitle']),
        ]],
        colWidths=[70 * mm, 30 * mm, 78 * mm],
    )
    footer.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
    ]))
    story.extend([Spacer(1, 8 * mm), footer])

    doc.build(story)
