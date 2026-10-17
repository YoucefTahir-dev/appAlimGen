import base64
from dataclasses import dataclass
from datetime import datetime

from django.core.exceptions import ValidationError
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from apps.commerce.utils import build_invoice_context, qr_code_data_uri
from apps.core.models import CompanySettings

from .models import PrinterProfile


class PrinterTransport:
    """Client-side transport contract. The Django server never opens local printer connections."""

    def send(self, payload: bytes) -> None:
        raise NotImplementedError


class MockPrinterTransport(PrinterTransport):
    def __init__(self):
        self.payloads = []

    def send(self, payload: bytes) -> None:
        self.payloads.append(payload)


@dataclass(frozen=True)
class PrinterTestResult:
    payload: bytes
    protocol: str
    raster_arabic_recommended: bool


class PrinterDriver:
    protocol = 'abstract'

    def build_test_payload(self, printer: PrinterProfile, now: datetime | None = None) -> PrinterTestResult:
        raise NotImplementedError


class GenericEscPosDriver(PrinterDriver):
    protocol = PrinterProfile.GENERIC_ESCPOS
    INITIALIZE = b'\x1b\x40'
    CUT = b'\x1d\x56\x00'

    def build_test_payload(self, printer, now=None):
        now = now or timezone.localtime()
        width = printer.characters_per_line
        lines = [
            'EL AMINE ERP'.center(width),
            str(_('Test imprimante')).center(width),
            now.strftime('%Y-%m-%d %H:%M:%S').center(width),
            'ABCDEFGHIJKLMNOPQRSTUVWXYZ',
            '0123456789',
            'Francais : Test impression',
            'ARABIC_RASTER_REQUIRED',
            '-' * width,
        ]
        body = ('\n'.join(line[:width] for line in lines) + '\n\n').encode(printer.encoding, errors='replace')
        return PrinterTestResult(self.INITIALIZE + body + self.CUT, self.protocol, True)


class Rpp02nDiagnosticDriver(GenericEscPosDriver):
    """Produces conservative ESC/POS diagnostics; compatibility is confirmed on the Android device."""

    protocol = 'rpp02n_diagnostic'


def driver_for(printer):
    # RPP02N profiles are often configured as an unknown/generic model by the
    # companion applications. Detect the device from every locally visible
    # identifier before falling back to the manually selected protocol.
    identifiers = (
        printer.model_name,
        printer.bluetooth_name,
        printer.name,
    )
    normalized_identifiers = {
        ''.join(character for character in (value or '').upper() if character.isalnum())
        for value in identifiers
    }
    if any(
        alias in identifier
        for identifier in normalized_identifiers
        for alias in ('RPP02N', 'RPPO2N')
    ):
        return Rpp02nDiagnosticDriver()

    if printer.protocol in {
        PrinterProfile.GENERIC_ESCPOS,
        PrinterProfile.EPSON_ESCPOS,
        PrinterProfile.XPRINTER,
        PrinterProfile.POSIFLEX,
        PrinterProfile.GP,
    }:
        return GenericEscPosDriver()
    raise ValidationError(_('Ce protocole nécessite un adaptateur local dédié.'))


def printer_test_payload(printer):
    return driver_for(printer).build_test_payload(printer)


def select_printer_for_user(user):
    preference = getattr(user, 'printer_preference', None)
    if preference and preference.printer.is_active:
        return preference.printer
    return PrinterProfile.objects.filter(is_default=True, is_active=True).first()


def invoice_print_data(sale, *, paper_width=80, language='bilingual'):
    if paper_width not in (58, 80):
        raise ValidationError(_('La largeur papier doit être 58 ou 80 mm.'))
    with translation.override('fr' if language == 'bilingual' else language):
        context = build_invoice_context(sale)
        company = context['company'] or CompanySettings()
        complete_80mm = paper_width == 80
        return {
            'invoice_number': sale.invoice_number,
            'ticket_number': sale.ticket_number,
            'issued_at': context['issued_at'].isoformat(),
            'snapshot_integrity_status': context['snapshot_integrity_status'],
            'snapshot_warning': context['snapshot_warning'],
            'cashier': (
                sale.created_by.get_full_name() or sale.created_by.get_username()
                if sale.created_by_id else ''
            ),
            'language': language,
            'paper_width': paper_width,
            'characters_per_line': 32 if paper_width == 58 else 48,
            # The 80 mm layout is the complete commercial ticket requested by
            # the business. The 58 mm layout keeps honoring the compact option.
            'show_legal_information': complete_80mm or sale.show_legal_information,
            'show_complete_customer_details': complete_80mm,
            'company': {
                'name_fr': company.company_name,
                'name_ar': context['company_name_ar'],
                'address': company.address,
                'phone': company.phone,
                'rc_number': company.rc_number,
                'tax_number': company.tax_number,
                'nis': company.nis,
                'article_number': company.article_number,
                'logo_url': company.logo.url if company.logo else None,
            },
            'customer': ({
                'id': sale.client_id,
                'name': context['invoice_client'].name,
                'phone': context['invoice_client'].phone,
                'address': context['invoice_client'].address,
                'tax_number': context['invoice_client'].tax_number,
                'nis': context['invoice_client'].nis,
                'article_number': context['invoice_client'].article_number,
                'trade_register_number': context['invoice_client'].trade_register_number,
            } if context['invoice_client'] else None),
            'items': [
                {
                    'product_id': line.product_id,
                    'name': line.invoice_product_name,
                    'reference': line.invoice_product_reference,
                    'unit': line.invoice_unit_name,
                    'packaging': line.packaging_name,
                    'packaging_factor': line.packaging_factor,
                    'quantity': line.packaging_quantity,
                    'stock_quantity': line.quantity,
                    'unit_price': str(line.unit_price),
                    'total': str(line.line_total()),
                }
                for line in context['lines']
            ],
            'totals': {
                'total_ht': str(context['total_ht']),
                'tax_rate': str(context['tax_rate']),
                'tax_amount': str(context['tax_amount']),
                'discount': str(context['discount']),
                'total_ttc': str(context['total_ttc']),
                'payment_method': sale.get_payment_type_display(),
                'amount_paid': str(sale.amount_paid),
                'balance_due': str(sale.balance_due),
                'payment_status': sale.payment_status,
            },
            'qr_code': qr_code_data_uri(
                sale, context['total_ttc'], client_name=context['invoice_client'].name,
            ),
            'messages': {'thank_you_fr': 'Merci pour votre visite.', 'thank_you_ar': 'شكراً لزيارتكم'},
        }


def encode_payload(payload):
    return base64.b64encode(payload).decode('ascii')
