import io
import base64
import re
from copy import copy
from decimal import Decimal
from pathlib import Path

import qrcode
from django.conf import settings
from django.contrib.staticfiles import finders
from django.utils.translation import get_language
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
from apps.core.export_security import pdf_safe_text
from apps.inventory.models import Client


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
    return money_value(value) + ' DZD'


def money_value(value):
    """Format a monetary value without repeating the currency in table cells."""
    return f'{Decimal(value):,.2f}'.replace(',', ' ')


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
        except (AttributeError, NotImplementedError, ValueError):
            try:
                company.logo.open('rb')
                logo_buffer = io.BytesIO(company.logo.read())
                logo_buffer.seek(0)
                return logo_buffer
            except (OSError, ValueError):
                pass
            finally:
                try:
                    company.logo.close()
                except Exception:
                    pass
    static_logo = finders.find('img/logo.png')
    return static_logo


def client_legal_identifiers(client):
    """Return the non-empty legal identifiers displayed on invoices."""
    identifiers = (
        ('NIF', client.tax_number),
        ('NIS', client.nis),
        ("N° article", client.article_number),
        ('RC', client.trade_register_number),
    )
    return [(label, value) for label, value in identifiers if value]


def legacy_snapshot_warning():
    language = (get_language() or 'fr').split('-', 1)[0]
    return {
        'ar': (
            'فاتورة سابقة لنظام الحفظ التاريخي: معلومات الهوية المعروضة مأخوذة '
            'من السجلات الحالية وليست موثقة كمعلومات تاريخية.'
        ),
        'en': (
            'Invoice created before historical snapshots: the displayed identity data '
            'comes from current records and is not certified as historical.'
        ),
    }.get(language, (
        'Facture antérieure aux snapshots : les informations d’identité affichées '
        'proviennent des fiches actuelles et ne sont pas certifiées historiques.'
    ))


def build_invoice_context(sale):
    uses_historical_snapshot = sale.has_certified_snapshot
    if uses_historical_snapshot:
        company = CompanySettings(
            company_name=sale.company_name_snapshot,
            address=sale.company_address_snapshot,
            phone=sale.company_phone_snapshot,
            email=sale.company_email_snapshot,
            rc_number=sale.company_rc_number_snapshot,
            tax_number=sale.company_tax_number_snapshot,
            nis=sale.company_nis_snapshot,
            article_number=sale.company_article_number_snapshot,
            share_capital=sale.company_share_capital_snapshot,
        )
        if sale.company_logo_snapshot:
            company.logo.name = sale.company_logo_snapshot
        client = Client(
            pk=sale.client_id,
            name=sale.client_name_snapshot,
            phone=sale.client_phone_snapshot,
            address=sale.client_address_snapshot,
            tax_number=sale.client_tax_number_snapshot,
            nis=sale.client_nis_snapshot,
            article_number=sale.client_article_number_snapshot,
            trade_register_number=sale.client_trade_register_number_snapshot,
            customer_type=sale.client_customer_type_snapshot or Client.CustomerType.RETAIL,
        )
        total_ht = sale.subtotal_ht_snapshot
        tax_amount = sale.tax_amount_snapshot
        total_ttc = sale.total
    else:
        company = CompanySettings.objects.first()
        # Never infer legacy capital from current settings. A stored value may
        # have been captured by a deliberate edit; old untouched rows stay null.
        # Keep the existing legacy identity fallback and warning unchanged.
        if company is not None:
            company = copy(company)
            company.share_capital = sale.company_share_capital_snapshot
        elif sale.company_share_capital_snapshot is not None:
            company = CompanySettings(
                company_name='', share_capital=sale.company_share_capital_snapshot,
            )
        client = sale.client
        total_ht = None
        tax_amount = None
    lines = list(sale.lines.select_related('product', 'product__unit', 'packaging').all())
    tax_rate = Decimal(str(sale.tax_rate or 0))
    discount = Decimal(str(sale.discount or 0))
    if total_ht is None:
        total_ht = sum((line.line_total() for line in lines), Decimal('0.00'))
    if tax_amount is None:
        tax_amount = total_ht * (tax_rate / Decimal('100'))
    if not uses_historical_snapshot:
        total_ttc = total_ht + tax_amount - discount
    return {
        'company': company,
        'company_name_fr': company.company_name if company and company.company_name else COMPANY_NAME_FR,
        'company_name_ar': (
            sale.company_name_ar_snapshot
            if uses_historical_snapshot and sale.company_name_ar_snapshot
            else COMPANY_NAME_AR
        ),
        'sale': sale,
        'invoice_client': client,
        'client_legal_identifiers': client_legal_identifiers(client),
        'lines': lines,
        'tax_rate': tax_rate,
        'discount': discount,
        'total_ht': total_ht,
        'tax_amount': tax_amount,
        'total_ttc': total_ttc,
        'amount_words': amount_to_french_words(total_ttc),
        'issued_at': sale.issued_at or sale.created_at,
        'currency': sale.currency_snapshot or 'DZD',
        'snapshot_integrity_status': sale.snapshot_integrity_status,
        'uses_historical_snapshot': uses_historical_snapshot,
        'snapshot_warning': '' if uses_historical_snapshot else legacy_snapshot_warning(),
    }


def delivery_note_number(sale):
    """Return the stable one-to-one delivery-note reference for an invoice."""
    return f'BL-{sale.invoice_number}'


def delivery_note_filename(sale):
    """Build an ASCII filename without trusting the invoice number as a header."""
    safe_number = re.sub(r'[^A-Za-z0-9._-]+', '_', delivery_note_number(sale)).strip('._')
    return f'{safe_number[:120] or "bon-livraison"}.pdf'


def build_delivery_note_context(sale):
    """Expose only non-financial invoice snapshot data to the delivery note."""
    invoice = build_invoice_context(sale)
    return {
        'company': invoice['company'],
        'company_name_fr': invoice['company_name_fr'],
        'company_name_ar': invoice['company_name_ar'],
        'sale': sale,
        'delivery_note_number': delivery_note_number(sale),
        'invoice_client': invoice['invoice_client'],
        'client_legal_identifiers': invoice['client_legal_identifiers'],
        'lines': invoice['lines'],
        'issued_at': invoice['issued_at'],
        'uses_historical_snapshot': invoice['uses_historical_snapshot'],
        'snapshot_warning': invoice['snapshot_warning'],
    }


def _pdf_directional_text(value):
    text = pdf_safe_text(value)
    if any('\u0600' <= character <= '\u06ff' for character in str(value or '')):
        return format_arabic(text)
    return text


def generate_delivery_note_pdf(response, sale):
    context = build_delivery_note_context(sale)
    font_name = register_unicode_font()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name='BLTitle', fontName=font_name, fontSize=18, leading=22,
        alignment=TA_CENTER, textColor=colors.white,
    ))
    styles.add(ParagraphStyle(
        name='BLHeading', fontName=font_name, fontSize=15, leading=19,
        textColor=BRAND_COLOR,
    ))
    styles.add(ParagraphStyle(name='BLBody', fontName=font_name, fontSize=9, leading=12))
    styles.add(ParagraphStyle(
        name='BLArabic', fontName=font_name, fontSize=13, leading=16,
        alignment=TA_RIGHT, textColor=BRAND_COLOR,
    ))
    styles.add(ParagraphStyle(name='BLSmall', fontName=font_name, fontSize=8, leading=10))

    doc = SimpleDocTemplate(
        response,
        pagesize=A4,
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=f"Bon de livraison {context['delivery_note_number']}",
    )
    story = []
    company = context['company']
    logo_path = get_company_logo_path(company)
    logo_cell = ''
    if logo_path:
        try:
            logo_cell = Image(logo_path, width=26 * mm, height=26 * mm, kind='proportional')
        except Exception:
            logo_cell = ''

    company_lines = [
        Paragraph(f"<b>{_pdf_directional_text(context['company_name_fr'])}</b>", styles['BLHeading']),
        Paragraph(_pdf_directional_text(context['company_name_ar']), styles['BLArabic']),
        Paragraph(
            f"Adresse : {_pdf_directional_text(company.address if company else '')}",
            styles['BLBody'],
        ),
        Paragraph(
            f"Téléphone : {_pdf_directional_text(company.phone if company else '')} | "
            f"Email : {_pdf_directional_text(company.email if company else '')}",
            styles['BLBody'],
        ),
    ]
    document_box = Table(
        [
            [Paragraph('<b>BON DE LIVRAISON</b>', styles['BLTitle'])],
            [Paragraph(
                f"<b>N° :</b> {_pdf_directional_text(context['delivery_note_number'])}",
                styles['BLBody'],
            )],
            [Paragraph(f"<b>Date :</b> {context['issued_at']:%d/%m/%Y}", styles['BLBody'])],
            [Paragraph(
                f"<b>Facture :</b> {_pdf_directional_text(sale.invoice_number)}",
                styles['BLBody'],
            )],
        ],
        colWidths=[54 * mm],
    )
    document_box.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, 0), BRAND_COLOR),
        ('BOX', (0, 0), (-1, -1), 0.8, BRAND_COLOR),
        ('INNERGRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#d8dee4')),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    header = Table([[logo_cell, company_lines, document_box]], colWidths=[30 * mm, 92 * mm, 56 * mm])
    header.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    story.extend([header, Spacer(1, 9 * mm)])

    client = context['invoice_client']
    client_lines = [
        '<b>Client</b>',
        _pdf_directional_text(client.name),
        f"Téléphone : {_pdf_directional_text(client.phone)}",
        f"Adresse : {_pdf_directional_text(client.address)}",
    ]
    client_lines.extend(
        f'{_pdf_directional_text(label)} : {_pdf_directional_text(value)}'
        for label, value in context['client_legal_identifiers']
    )
    client_table = Table(
        [[Paragraph('<br/>'.join(client_lines), styles['BLBody'])]],
        colWidths=[178 * mm],
    )
    client_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f5f7f9')),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#ccd6dd')),
        ('PADDING', (0, 0), (-1, -1), 8),
    ]))
    story.extend([client_table, Spacer(1, 7 * mm)])

    table_data = [['N°', 'Référence', 'Désignation', 'Quantité', 'Unité']]
    for index, line in enumerate(context['lines'], start=1):
        table_data.append([
            str(index),
            Paragraph(_pdf_directional_text(line.invoice_product_reference), styles['BLSmall']),
            Paragraph(_pdf_directional_text(line.invoice_product_name), styles['BLSmall']),
            str(line.packaging_quantity),
            Paragraph(
                _pdf_directional_text(line.packaging_name or line.invoice_unit_name),
                styles['BLSmall'],
            ),
        ])
    products_table = Table(
        table_data,
        colWidths=[10 * mm, 34 * mm, 78 * mm, 24 * mm, 32 * mm],
        repeatRows=1,
    )
    products_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), BRAND_COLOR),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#cbd5dc')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fbfcfd')]),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 1), (0, -1), 'CENTER'),
        ('ALIGN', (3, 1), (3, -1), 'RIGHT'),
        ('PADDING', (0, 0), (-1, -1), 5),
    ]))
    story.extend([products_table, Spacer(1, 10 * mm)])

    signatures = Table(
        [[
            Paragraph('<b>Signature du livreur</b><br/><br/><br/>____________________________', styles['BLBody']),
            Paragraph('<b>Signature du client</b><br/><br/><br/>____________________________', styles['BLBody']),
        ]],
        colWidths=[89 * mm, 89 * mm],
    )
    signatures.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#9aa8b2')),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#9aa8b2')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('PADDING', (0, 0), (-1, -1), 10),
    ]))
    story.append(signatures)
    story.extend([
        Spacer(1, 6 * mm),
        Paragraph(
            'La génération de ce document ne confirme pas à elle seule la livraison effective.',
            styles['BLSmall'],
        ),
    ])
    if context['snapshot_warning']:
        story.extend([
            Spacer(1, 3 * mm),
            Paragraph(_pdf_directional_text(context['snapshot_warning']), styles['BLSmall']),
        ])
    doc.build(story)


def qr_code_image_reader(sale, total_ttc, client_name=None):
    payload = (
        f'Facture: {sale.invoice_number}\n'
        f'Client: {client_name or sale.client.name}\n'
        f'Total TTC: {money(total_ttc)}\n'
        f'Date: {sale.created_at:%d/%m/%Y}'
    )
    img = qrcode.make(payload)
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    return buffer


def qr_code_data_uri(sale, total_ttc, client_name=None):
    buffer = qr_code_image_reader(sale, total_ttc, client_name=client_name)
    encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
    return f'data:image/png;base64,{encoded}'


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
        rightMargin=12 * mm,
        leftMargin=12 * mm,
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
        Paragraph(f"<b>{pdf_safe_text(context['company_name_fr'])}</b>", styles['ERPTitle']),
        Paragraph(pdf_safe_text(format_arabic(context['company_name_ar'])), styles['ERPArabic']),
        Paragraph(f"Adresse : {pdf_safe_text(company.address if company else '')}", styles['ERPSubTitle']),
        Paragraph(
            f"Téléphone : {pdf_safe_text(company.phone if company else '')} | "
            f"Email : {pdf_safe_text(company.email if company else '')}",
            styles['ERPSubTitle'],
        ),
        Paragraph(
            f"NIF : {pdf_safe_text(company.tax_number if company else '')} | "
            f"NIS : {pdf_safe_text(company.nis if company else '')}",
            styles['ERPSubTitle'],
        ),
        Paragraph(
            f"RC : {pdf_safe_text(company.rc_number if company else '')} | "
            f"AI : {pdf_safe_text(company.article_number if company else '')}",
            styles['ERPSubTitle'],
        ),
    ]
    if company and company.share_capital is not None:
        company_lines.append(Paragraph(
            f"Capital social : {money(company.share_capital)}",
            styles['ERPSubTitle'],
        ))
    invoice_box = Table(
        [
            [Paragraph('<b>FACTURE</b>', styles['ERPBoxTitle'])],
            [Paragraph(f"<b>N° :</b> {pdf_safe_text(sale.invoice_number)}", styles['ERPSubTitle'])],
            [Paragraph(f"<b>Date :</b> {context['issued_at']:%d/%m/%Y}", styles['ERPSubTitle'])],
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

    client = context['invoice_client']
    client_lines = [
        '<b>Client</b>',
        pdf_safe_text(client.name),
        f'Téléphone : {pdf_safe_text(client.phone)}',
        f'Adresse : {pdf_safe_text(client.address)}',
    ]
    client_lines.extend(
        f'{pdf_safe_text(label)} : {pdf_safe_text(value)}'
        for label, value in context['client_legal_identifiers']
    )
    client_table = Table(
        [[
            Paragraph(
                '<br/>'.join(client_lines),
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

    table_data = [['N°', 'Produit', 'Qté', 'PU (DZD)', 'TVA', 'Montant HT (DZD)', 'TTC (DZD)']]
    for index, line in enumerate(context['lines'], start=1):
        table_data.append([
            str(index),
            Paragraph(pdf_safe_text(f'{line.invoice_product_name} - {line.packaging_name}'), styles['ERPSmall']),
            str(line.packaging_quantity),
            money_value(line.unit_price),
            f"{context['tax_rate']}%",
            money_value(line.line_total()),
            money_value(line.line_total() + (line.line_total() * context['tax_rate'] / Decimal('100'))),
        ])

    products_table = Table(
        table_data,
        colWidths=[8 * mm, 65 * mm, 14 * mm, 24 * mm, 16 * mm, 29.5 * mm, 29.5 * mm],
        repeatRows=1,
    )
    products_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), BRAND_COLOR),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 7.5),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#cbd5dc')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fbfcfd')]),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 1), (0, -1), 'CENTER'),
        ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.extend([products_table, Spacer(1, 7 * mm)])

    totals_table = Table(
        [
            ['Total HT', money(context['total_ht'])],
            [f"TVA ({context['tax_rate']:.2f}%)", money(context['tax_amount'])],
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

    if context['snapshot_warning']:
        story.extend([
            Spacer(1, 4 * mm),
            Paragraph(pdf_safe_text(context['snapshot_warning']), styles['ERPSmall']),
        ])
    qr_buffer = qr_code_image_reader(
        sale, context['total_ttc'], client_name=context['invoice_client'].name,
    )
    footer = Table(
        [[
            Paragraph('Signature et cachet<br/><br/>____________________________', styles['ERPSubTitle']),
            Image(qr_buffer, width=26 * mm, height=26 * mm),
            Paragraph(
                'Merci pour votre confiance.<br/>Téléphone : '
                + pdf_safe_text(company.phone if company else '')
                + '<br/>Email : '
                + pdf_safe_text(company.email if company else ''),
                styles['ERPSubTitle'],
            ),
        ]],
        colWidths=[70 * mm, 30 * mm, 78 * mm],
    )
    footer.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
    ]))
    story.extend([Spacer(1, 8 * mm), footer])

    doc.build(story)
