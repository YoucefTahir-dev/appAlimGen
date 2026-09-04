from decimal import Decimal

from django.core.exceptions import ValidationError
from django.contrib.messages import get_messages
from django.db import transaction
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from apps.inventory.models import Product, ProductPackaging, Category, Brand, Unit, Client, StockMovement, Supplier
from apps.commerce.forms import SaleForm
from apps.commerce.models import InvoiceSequence, Payment, Purchase, PurchaseLine, Sale, SaleLine, TicketSequence
from apps.commerce.utils import COMPANY_NAME_AR, format_arabic, register_unicode_font


class CommerceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='tester', password='pass', role=User.MANAGER)
        self.client.login(username='tester', password='pass')
        self.cat = Category.objects.create(name='Cat1')
        self.brand = Brand.objects.create(name='Brand1')
        self.unit = Unit.objects.create(name='Unit1')
        self.product = Product.objects.create(
            barcode='123',
            name='Test Product',
            category=self.cat,
            brand=self.brand,
            unit=self.unit,
            purchase_price='10.00',
            sale_price='15.00',
            quantity=10,
            minimum_stock=1,
        )
        self.client_obj = Client.objects.create(name='Client1')
        self.supplier_obj = Supplier.objects.create(name='Supplier1')
    def test_purchase_create_and_stock_update(self):
        url = reverse('purchase_create')
        response = self.client.post(url, {
            'reference': 'PO123',
            'supplier': self.supplier_obj.pk,
            'tax_rate': '10.00',
            'lines-TOTAL_FORMS': '1',
            'lines-INITIAL_FORMS': '0',
            'lines-MIN_NUM_FORMS': '0',
            'lines-MAX_NUM_FORMS': '1000',
            'lines-0-product': str(self.product.pk),
            'lines-0-quantity': '5',
            'lines-0-purchase_price': '20.00',
        })
        self.assertEqual(
            response.status_code,
            302,
            response.context['form'].errors.as_text() if response.context else None,
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 15)
        movement = StockMovement.objects.get(source_type=StockMovement.SOURCE_PURCHASE)
        self.assertEqual(movement.applied_delta, 5)
        self.assertEqual(movement.created_by, self.user)
        self.assertIn('purchase:', movement.source_reference)

    def test_sale_create_and_stock_decrement(self):
        url = reverse('sale_create')
        response = self.client.post(url, {
            'invoice_number': 'INV123',
            'client': self.client_obj.pk,
            'discount': '0.00',
            'tax_rate': '10.00',
            'lines-TOTAL_FORMS': '1',
            'lines-INITIAL_FORMS': '0',
            'lines-MIN_NUM_FORMS': '0',
            'lines-MAX_NUM_FORMS': '1000',
            'lines-0-product': str(self.product.pk),
            'lines-0-quantity': '3',
            'lines-0-unit_price': '15.00',
        })
        self.assertEqual(
            response.status_code,
            302,
            response.context['form'].errors.as_text() if response.context else None,
        )
        sale = Sale.objects.latest('pk')
        self.assertRegex(sale.invoice_number, r'^FAC-\d{4}-000001$')
        self.assertRegex(sale.ticket_number, r'^TCK-\d{4}-000001$')
        self.assertEqual(sale.payment_type, Sale.CASH)
        self.assertEqual(sale.payments.count(), 1)
        self.assertEqual(sale.payments.get().amount, sale.total)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 7)
        movement = StockMovement.objects.get(source_type=StockMovement.SOURCE_SALE)
        self.assertEqual(movement.applied_delta, -3)
        self.assertEqual(movement.created_by, self.user)
        self.assertIn('sale:', movement.source_reference)

    def test_web_sale_by_packaging_uses_shared_conversion_and_price_rule(self):
        packaging = ProductPackaging.objects.create(
            product=self.product, name='Pack de 2', conversion_factor=2,
            default_sale_price='30.00', barcode='WEB-PACK-2',
        )
        response = self.client.post(reverse('sale_create'), {
            'client': self.client_obj.pk,
            'discount': '0.00',
            'tax_rate': '0.00',
            'payment_type': Sale.CASH,
            'settlement_action': SaleForm.NO_PAYMENT,
            'lines-TOTAL_FORMS': '1',
            'lines-INITIAL_FORMS': '0',
            'lines-MIN_NUM_FORMS': '0',
            'lines-MAX_NUM_FORMS': '1000',
            'lines-0-product': str(self.product.pk),
            'lines-0-packaging': str(packaging.pk),
            'lines-0-quantity': '3',
            'lines-0-unit_price': '30.00',
        })
        self.assertEqual(response.status_code, 302, response.context['formset'].errors if response.context else None)
        line = Sale.objects.latest('pk').lines.get()
        self.assertEqual(line.packaging_quantity, 3)
        self.assertEqual(line.packaging_factor, 2)
        self.assertEqual(line.quantity, 6)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 4)

    def test_sale_form_exposes_dynamic_product_line_controls(self):
        response = self.client.get(reverse('sale_create'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="add-sale-line"')
        self.assertContains(response, 'id="sale-line-template"')
        self.assertContains(response, 'id="id_lines-TOTAL_FORMS"')
        self.assertContains(response, 'sale-formset.')
        self.assertContains(response, 'id="sale-client-type"')
        self.assertContains(response, 'sale-product-meta')
        self.assertContains(response, 'sale-price-feedback')
        self.assertContains(response, 'min="1"')

    def test_sale_create_accepts_multiple_products_on_one_invoice(self):
        second_product = Product.objects.create(
            name='Second Product',
            category=self.cat,
            brand=self.brand,
            unit=self.unit,
            purchase_price='4.00',
            sale_price='7.00',
            quantity=20,
            minimum_stock=1,
        )

        response = self.client.post(
            reverse('sale_create'),
            {
                'client': self.client_obj.pk,
                'discount': '0.00',
                'tax_rate': '0.00',
                'lines-TOTAL_FORMS': '2',
                'lines-INITIAL_FORMS': '0',
                'lines-MIN_NUM_FORMS': '0',
                'lines-MAX_NUM_FORMS': '1000',
                'lines-0-product': str(self.product.pk),
                'lines-0-quantity': '2',
                'lines-0-unit_price': '15.00',
                'lines-1-product': str(second_product.pk),
                'lines-1-quantity': '3',
                'lines-1-unit_price': '7.00',
            },
        )

        self.assertEqual(response.status_code, 302)
        sale = Sale.objects.latest('pk')
        self.assertEqual(sale.lines.count(), 2)
        self.assertEqual(sale.total, Decimal('51.00'))
        self.product.refresh_from_db()
        second_product.refresh_from_db()
        self.assertEqual(self.product.quantity, 8)
        self.assertEqual(second_product.quantity, 17)

    def test_sale_lines_roll_back_together_when_stock_becomes_insufficient(self):
        sale = Sale.objects.create(
            invoice_number='INV-ATOMIC',
            client=self.client_obj,
            total='0',
            discount='0',
            tax_rate='0',
        )

        with self.assertRaises(ValidationError):
            with transaction.atomic():
                SaleLine.objects.create(sale=sale, product=self.product, quantity=6, unit_price='15.00')
                SaleLine.objects.create(sale=sale, product=self.product, quantity=5, unit_price='15.00')

        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 10)
        self.assertFalse(sale.lines.exists())
        self.assertFalse(StockMovement.objects.filter(source_type=StockMovement.SOURCE_SALE).exists())

    def test_queryset_delete_restores_stock_and_bulk_stock_update_is_blocked(self):
        sale = Sale.objects.create(
            invoice_number='INV-QUERYSET',
            client=self.client_obj,
            total='0',
            discount='0',
            tax_rate='0',
        )
        line = SaleLine.objects.create(
            sale=sale,
            product=self.product,
            quantity=4,
            unit_price='15.00',
        )

        with self.assertRaises(ValidationError):
            SaleLine.objects.filter(pk=line.pk).update(quantity=2)
        SaleLine.objects.filter(pk=line.pk).delete()

        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 10)
        self.assertEqual(
            list(
                StockMovement.objects.filter(source_type=StockMovement.SOURCE_SALE)
                .order_by('pk')
                .values_list('applied_delta', flat=True)
            ),
            [-4, 4],
        )

    def test_sale_product_change_rolls_back_when_new_product_stock_is_insufficient(self):
        second_product = Product.objects.create(
            name='Second Product',
            category=self.cat,
            brand=self.brand,
            unit=self.unit,
            purchase_price='10.00',
            sale_price='15.00',
            quantity=2,
            minimum_stock=1,
        )
        sale = Sale.objects.create(
            invoice_number='INV-PRODUCT-CHANGE',
            client=self.client_obj,
            total='0',
            discount='0',
            tax_rate='0',
        )
        line = SaleLine.objects.create(
            sale=sale,
            product=self.product,
            quantity=3,
            unit_price='15.00',
        )
        line.product = second_product

        with self.assertRaises(ValidationError):
            line.save()

        line.refresh_from_db()
        self.product.refresh_from_db()
        second_product.refresh_from_db()
        self.assertEqual(line.product, self.product)
        self.assertEqual(self.product.quantity, 7)
        self.assertEqual(second_product.quantity, 2)
        self.assertEqual(
            StockMovement.objects.filter(source_type=StockMovement.SOURCE_SALE).count(),
            1,
        )

    def test_purchase_reduction_is_rejected_if_received_units_were_already_sold(self):
        purchase = Purchase.objects.create(
            reference='PO-CONSUMED',
            supplier=self.supplier_obj,
            total='0',
            tax_rate='0',
        )
        purchase_line = PurchaseLine.objects.create(
            purchase=purchase,
            product=self.product,
            quantity=5,
            purchase_price='10.00',
        )
        sale = Sale.objects.create(
            invoice_number='INV-CONSUMED',
            client=self.client_obj,
            total='0',
            discount='0',
            tax_rate='0',
        )
        SaleLine.objects.create(
            sale=sale,
            product=self.product,
            quantity=12,
            unit_price='15.00',
        )
        purchase_line.quantity = 1

        with self.assertRaises(ValidationError):
            purchase_line.save()

        purchase_line.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(purchase_line.quantity, 5)
        self.assertEqual(self.product.quantity, 3)

    def test_purchase_delete_failure_keeps_document_and_exposes_a_clear_message(self):
        purchase = Purchase.objects.create(
            reference='PO-DELETE-BLOCKED',
            supplier=self.supplier_obj,
            total='0',
            tax_rate='0',
        )
        PurchaseLine.objects.create(
            purchase=purchase,
            product=self.product,
            quantity=5,
            purchase_price='10.00',
        )
        sale = Sale.objects.create(
            invoice_number='INV-DELETE-BLOCKED',
            client=self.client_obj,
            total='0',
            discount='0',
            tax_rate='0',
        )
        SaleLine.objects.create(
            sale=sale,
            product=self.product,
            quantity=12,
            unit_price='15.00',
        )

        response = self.client.post(reverse('purchase_delete', args=[purchase.pk]))

        self.assertEqual(
            response.status_code,
            302,
            response.context['form'].errors.as_text() if response.context else None,
        )
        self.assertTrue(Purchase.objects.filter(pk=purchase.pk).exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 3)
        self.assertTrue(
            any('Stock insuffisant' in str(message) for message in get_messages(response.wsgi_request))
        )

    def test_invoice_and_ticket_numbers_are_independent_and_not_reused_after_delete(self):
        url = reverse('sale_create')
        payload = {
            'client': self.client_obj.pk,
            'discount': '0.00',
            'tax_rate': '0.00',
            'payment_type': Sale.CASH,
            'settlement_action': 'no_payment',
            'lines-TOTAL_FORMS': '1',
            'lines-INITIAL_FORMS': '0',
            'lines-MIN_NUM_FORMS': '0',
            'lines-MAX_NUM_FORMS': '1000',
            'lines-0-product': str(self.product.pk),
            'lines-0-quantity': '1',
            'lines-0-unit_price': '15.00',
        }

        first_response = self.client.post(url, payload)
        self.assertEqual(first_response.status_code, 302)
        first_sale = Sale.objects.latest('pk')
        first_number = first_sale.invoice_number
        first_ticket = first_sale.ticket_number

        self.client.post(reverse('sale_delete', args=[first_sale.pk]))

        second_response = self.client.post(url, payload)
        self.assertEqual(second_response.status_code, 302)
        second_sale = Sale.objects.latest('pk')

        self.assertRegex(first_number, r'^FAC-\d{4}-000001$')
        self.assertRegex(second_sale.invoice_number, r'^FAC-\d{4}-000002$')
        self.assertRegex(first_ticket, r'^TCK-\d{4}-000001$')
        self.assertRegex(second_sale.ticket_number, r'^TCK-\d{4}-000002$')
        self.assertNotEqual(first_number, second_sale.invoice_number)
        self.assertNotEqual(first_ticket, second_sale.ticket_number)
        self.assertEqual(InvoiceSequence.objects.get(year=second_sale.created_at.year).last_number, 2)
        self.assertEqual(TicketSequence.objects.get(year=second_sale.created_at.year).last_number, 2)

    def test_sale_rejects_price_below_purchase_price(self):
        initial_quantity = self.product.quantity
        url = reverse('sale_create')
        response = self.client.post(url, {
            'invoice_number': 'INV-LOSS-001',
            'client': self.client_obj.pk,
            'discount': '0.00',
            'tax_rate': '0.00',
            'lines-TOTAL_FORMS': '1',
            'lines-INITIAL_FORMS': '0',
            'lines-MIN_NUM_FORMS': '0',
            'lines-MAX_NUM_FORMS': '1000',
            'lines-0-product': str(self.product.pk),
            'lines-0-quantity': '1',
            'lines-0-unit_price': '5.00',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Le prix de vente est inférieur au coût")
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, initial_quantity)
        self.assertFalse(Sale.objects.filter(invoice_number='INV-LOSS-001').exists())

    def test_sale_rejects_invalid_tax_and_excessive_discount(self):
        url = reverse('sale_create')
        base_payload = {
            'client': self.client_obj.pk,
            'discount': '0.00',
            'tax_rate': '-1.00',
            'payment_type': Sale.CASH,
            'settlement_action': 'no_payment',
            'lines-TOTAL_FORMS': '1',
            'lines-INITIAL_FORMS': '0',
            'lines-MIN_NUM_FORMS': '0',
            'lines-MAX_NUM_FORMS': '1000',
            'lines-0-product': str(self.product.pk),
            'lines-0-quantity': '1',
            'lines-0-unit_price': '15.00',
        }

        invalid_tax = self.client.post(url, base_payload)
        self.assertEqual(invalid_tax.status_code, 200)
        self.assertContains(invalid_tax, 'Le taux de TVA doit être compris entre 0 et 100.')
        self.assertFalse(Sale.objects.exists())

        excessive_discount_payload = {**base_payload, 'tax_rate': '0.00', 'discount': '100.00'}
        excessive_discount = self.client.post(url, excessive_discount_payload)
        self.assertEqual(excessive_discount.status_code, 200)
        self.assertContains(excessive_discount, 'La remise ne peut pas dépasser le total de la vente.')
        self.assertFalse(Sale.objects.exists())

    def test_sale_rejects_discount_that_erases_the_margin(self):
        response = self.client.post(reverse('sale_create'), {
            'client': self.client_obj.pk,
            'discount': '6.00',
            'tax_rate': '0.00',
            'payment_type': Sale.CASH,
            'lines-TOTAL_FORMS': '1',
            'lines-INITIAL_FORMS': '0',
            'lines-MIN_NUM_FORMS': '0',
            'lines-MAX_NUM_FORMS': '1000',
            'lines-0-product': str(self.product.pk),
            'lines-0-quantity': '1',
            'lines-0-unit_price': '15.00',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Impossible de vendre un produit à un prix inférieur')
        self.assertFalse(Sale.objects.exists())

    def test_sale_create_rejects_insufficient_stock(self):
        initial_quantity = self.product.quantity
        url = reverse('sale_create')
        response = self.client.post(url, {
            'invoice_number': 'INV-STOCK-FAIL',
            'client': self.client_obj.pk,
            'discount': '0.00',
            'tax_rate': '0.00',
            'lines-TOTAL_FORMS': '1',
            'lines-INITIAL_FORMS': '0',
            'lines-MIN_NUM_FORMS': '0',
            'lines-MAX_NUM_FORMS': '1000',
            'lines-0-product': str(self.product.pk),
            'lines-0-quantity': str(initial_quantity + 1),
            'lines-0-unit_price': '15.00',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Stock insuffisant')
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, initial_quantity)
        self.assertFalse(Sale.objects.filter(invoice_number='INV-STOCK-FAIL').exists())

    def test_purchase_update_and_stock_adjust(self):
        purchase = Purchase.objects.create(reference='PO123', supplier=self.supplier_obj, total='0', tax_rate='10')
        line = PurchaseLine.objects.create(purchase=purchase, product=self.product, quantity=5, purchase_price='20.00')
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 15)

        url = reverse('purchase_update', args=[purchase.pk])
        response = self.client.post(url, {
            'reference': purchase.reference,
            'supplier': self.supplier_obj.pk,
            'tax_rate': '10.00',
            'lines-TOTAL_FORMS': '1',
            'lines-INITIAL_FORMS': '1',
            'lines-MIN_NUM_FORMS': '0',
            'lines-MAX_NUM_FORMS': '1000',
            'lines-0-id': line.pk,
            'lines-0-product': str(self.product.pk),
            'lines-0-quantity': '8',
            'lines-0-purchase_price': '20.00',
        })
        self.assertEqual(
            response.status_code,
            302,
            response.context['form'].errors.as_text() if response.context else None,
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 18)
        movements = StockMovement.objects.filter(source_type=StockMovement.SOURCE_PURCHASE).order_by('pk')
        self.assertEqual([movement.applied_delta for movement in movements], [5, 3])
        self.assertEqual(movements.last().created_by, self.user)

    def test_sale_update_and_stock_adjust(self):
        sale = Sale.objects.create(invoice_number='INV123', client=self.client_obj, total='0', discount='0', tax_rate='10')
        line = SaleLine.objects.create(sale=sale, product=self.product, quantity=3, unit_price='15.00')
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 7)

        url = reverse('sale_update', args=[sale.pk])
        response = self.client.post(url, {
            'invoice_number': sale.invoice_number,
            'client': self.client_obj.pk,
            'discount': '0.00',
            'tax_rate': '10.00',
            'lines-TOTAL_FORMS': '1',
            'lines-INITIAL_FORMS': '1',
            'lines-MIN_NUM_FORMS': '0',
            'lines-MAX_NUM_FORMS': '1000',
            'lines-0-id': line.pk,
            'lines-0-product': str(self.product.pk),
            'lines-0-quantity': '1',
            'lines-0-unit_price': '15.00',
        })
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 9)
        movements = StockMovement.objects.filter(source_type=StockMovement.SOURCE_SALE).order_by('pk')
        self.assertEqual([movement.applied_delta for movement in movements], [-3, 2])
        self.assertEqual(movements.last().created_by, self.user)
        self.assertFalse(sale.payments.exists())

    def test_sale_update_rejects_insufficient_stock(self):
        sale = Sale.objects.create(invoice_number='INV-STOCK-UPD', client=self.client_obj, total='0', discount='0', tax_rate='0')
        line = SaleLine.objects.create(sale=sale, product=self.product, quantity=3, unit_price='15.00')
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 7)

        url = reverse('sale_update', args=[sale.pk])
        response = self.client.post(url, {
            'invoice_number': sale.invoice_number,
            'client': self.client_obj.pk,
            'discount': '0.00',
            'tax_rate': '0.00',
            'lines-TOTAL_FORMS': '1',
            'lines-INITIAL_FORMS': '1',
            'lines-MIN_NUM_FORMS': '0',
            'lines-MAX_NUM_FORMS': '1000',
            'lines-0-id': line.pk,
            'lines-0-product': str(self.product.pk),
            'lines-0-quantity': '11',
            'lines-0-unit_price': '15.00',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Stock insuffisant')
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 7)
        line.refresh_from_db()
        self.assertEqual(line.quantity, 3)

    def test_sale_invoice_pdf(self):
        sale = Sale.objects.create(invoice_number='INV456', client=self.client_obj, total='0', discount='0', tax_rate='10')
        SaleLine.objects.create(sale=sale, product=self.product, quantity=1, unit_price='15.00')
        url = reverse('sale_invoice_pdf', args=[sale.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_invoice_pdf_has_bundled_unicode_font_and_arabic_shaping(self):
        self.assertEqual(register_unicode_font(), 'ERPUnicode')
        self.assertNotEqual(format_arabic(COMPANY_NAME_AR), COMPANY_NAME_AR)

    def test_sale_invoice_preview(self):
        sale = Sale.objects.create(invoice_number='FAC-2026-000100', client=self.client_obj, total='0', discount='0', tax_rate='10')
        SaleLine.objects.create(sale=sale, product=self.product, quantity=1, unit_price='15.00')
        response = self.client.get(reverse('sale_invoice_preview', args=[sale.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'FACTURE')
        self.assertContains(response, sale.invoice_number)
        self.assertContains(response, 'الأمين للمواد الغذائية و غير الغذائية')
        self.assertContains(response, 'Télécharger PDF')

    def test_sale_ticket_preview_generates_missing_ticket_number_and_supports_thermal_widths(self):
        sale = Sale.objects.create(invoice_number='FAC-2026-000101', client=self.client_obj, total='0', discount='0', tax_rate='10')
        SaleLine.objects.create(sale=sale, product=self.product, quantity=1, unit_price='15.00')

        response_80 = self.client.get(reverse('sale_ticket_preview', args=[sale.pk, 80]))
        self.assertEqual(response_80.status_code, 200)
        sale.refresh_from_db()
        self.assertRegex(sale.ticket_number, r'^TCK-\d{4}-000001$')
        self.assertContains(response_80, sale.ticket_number)
        self.assertContains(response_80, 'size: 80mm auto')
        self.assertContains(response_80, 'Conditionnement : Unit1')
        self.assertContains(response_80, 'data:image/png;base64')

        response_58 = self.client.get(reverse('sale_ticket_preview', args=[sale.pk, 58]))
        self.assertEqual(response_58.status_code, 200)
        self.assertContains(response_58, 'size: 58mm auto')

    def test_sale_delete_reverts_stock(self):
        initial_quantity = self.product.quantity
        sale = Sale.objects.create(invoice_number='INV999', client=self.client_obj, total='0', discount='0', tax_rate='10')
        SaleLine.objects.create(sale=sale, product=self.product, quantity=4, unit_price='15.00')
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, initial_quantity - 4)

        url = reverse('sale_delete', args=[sale.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('sale_list'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, initial_quantity)
        self.assertFalse(Sale.objects.filter(pk=sale.pk).exists())

    def test_purchase_delete_reverts_stock(self):
        initial_quantity = self.product.quantity
        purchase = Purchase.objects.create(reference='PO999', supplier=self.supplier_obj, total='0', tax_rate='10')
        PurchaseLine.objects.create(purchase=purchase, product=self.product, quantity=6, purchase_price='20.00')
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, initial_quantity + 6)

        url = reverse('purchase_delete', args=[purchase.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('purchase_list'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, initial_quantity)
        self.assertFalse(Purchase.objects.filter(pk=purchase.pk).exists())


class PaymentAndHistoricalCostTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username='payment-manager',
            password='pass',
            role=User.MANAGER,
        )
        cls.seller = User.objects.create(username='payment-seller', role=User.SELLER)
        cls.category = Category.objects.create(name='Paiements')
        cls.brand = Brand.objects.create(name='Marque paiements')
        cls.unit = Unit.objects.create(name='PiÃ¨ce paiements')
        cls.product = Product.objects.create(
            name='Produit paiement',
            category=cls.category,
            brand=cls.brand,
            unit=cls.unit,
            purchase_price='10.00',
            sale_price='20.00',
            quantity=100,
            minimum_stock=1,
        )
        cls.client_record = Client.objects.create(name='Client paiement')
        cls.supplier = Supplier.objects.create(name='Fournisseur paiement')
        cls.sale = Sale.objects.create(
            invoice_number='FAC-PAY-BASE',
            client=cls.client_record,
            total='100.00',
            discount='0.00',
            tax_rate='0.00',
        )
        cls.purchase = Purchase.objects.create(
            reference='ACH-PAY-BASE',
            supplier=cls.supplier,
            total='80.00',
            tax_rate='0.00',
        )

    def setUp(self):
        self.client.login(username='payment-manager', password='pass')

    def test_payment_requires_exactly_one_document_and_positive_amount(self):
        with self.assertRaises(ValidationError):
            Payment(amount='10.00', payment_type=Payment.CASH).save()
        with self.assertRaises(ValidationError):
            Payment(
                sale=self.sale,
                purchase=self.purchase,
                amount='10.00',
                payment_type=Payment.CASH,
            ).save()
        with self.assertRaises(ValidationError):
            Payment(sale=self.sale, amount='0.00', payment_type=Payment.CASH).save()

    def test_partial_full_and_overpayment_are_consistent(self):
        first = Payment.objects.create(
            sale=self.sale,
            amount='40.00',
            payment_type=Payment.CASH,
            created_by=self.user,
        )
        self.assertRegex(first.reference, r'^PAY-\d{4}-[A-F0-9]{16}$')
        self.assertEqual(self.sale.amount_paid, Decimal('40.00'))
        self.assertEqual(self.sale.balance_due, Decimal('60.00'))
        self.assertEqual(self.sale.payment_status, 'partial')

        Payment.objects.create(
            sale=self.sale,
            amount='60.00',
            payment_type=Payment.TRANSFER,
            created_by=self.user,
        )
        self.assertEqual(self.sale.balance_due, Decimal('0.00'))
        self.assertEqual(self.sale.payment_status, 'paid')
        with self.assertRaises(ValidationError):
            Payment.objects.create(
                sale=self.sale,
                amount='0.01',
                payment_type=Payment.CASH,
            )
        self.assertEqual(self.sale.payments.count(), 2)

    def test_supplier_payment_and_document_total_guard(self):
        Payment.objects.create(
            purchase=self.purchase,
            amount='25.00',
            payment_type=Payment.CHEQUE,
            created_by=self.user,
        )
        self.assertEqual(self.purchase.amount_paid, Decimal('25.00'))
        self.assertEqual(self.purchase.balance_due, Decimal('55.00'))
        self.assertEqual(self.purchase.payment_status, 'partial')

        self.purchase.total = Decimal('24.99')
        with self.assertRaises(ValidationError):
            self.purchase.save(update_fields=['total'])
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.total, Decimal('80.00'))

    def test_payment_reference_is_immutable(self):
        payment = Payment.objects.create(
            sale=self.sale,
            amount='10.00',
            payment_type=Payment.CASH,
        )
        payment.reference = 'MANUAL-REFERENCE'
        with self.assertRaises(ValidationError):
            payment.save(update_fields=['reference'])
        payment.refresh_from_db()
        self.assertRegex(payment.reference, r'^PAY-\d{4}-[A-F0-9]{16}$')

    def test_payment_routes_create_list_and_delete_with_audit_user(self):
        create_response = self.client.post(
            reverse('sale_payment_create', args=[self.sale.pk]),
            {'amount': '35.00', 'payment_type': Payment.TRANSFER},
        )
        self.assertEqual(create_response.status_code, 302)
        payment = self.sale.payments.get()
        self.assertEqual(payment.created_by, self.user)

        list_response = self.client.get(reverse('sale_payment_list', args=[self.sale.pk]))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, payment.reference)
        self.assertContains(list_response, '35,00')

        delete_response = self.client.post(
            reverse('sale_payment_delete', args=[self.sale.pk, payment.pk])
        )
        self.assertEqual(delete_response.status_code, 302)
        self.assertFalse(Payment.objects.filter(pk=payment.pk).exists())
        self.assertEqual(self.sale.balance_due, Decimal('100.00'))

        purchase_create = self.client.post(
            reverse('purchase_payment_create', args=[self.purchase.pk]),
            {'amount': '20.00', 'payment_type': Payment.CHEQUE},
        )
        self.assertEqual(purchase_create.status_code, 302)
        supplier_payment = self.purchase.payments.get()
        self.assertEqual(supplier_payment.created_by, self.user)
        purchase_list = self.client.get(
            reverse('purchase_payment_list', args=[self.purchase.pk])
        )
        self.assertContains(purchase_list, supplier_payment.reference)
        purchase_delete = self.client.post(
            reverse(
                'purchase_payment_delete',
                args=[self.purchase.pk, supplier_payment.pk],
            )
        )
        self.assertEqual(purchase_delete.status_code, 302)
        self.assertFalse(Payment.objects.filter(pk=supplier_payment.pk).exists())

    def test_payment_mutations_require_the_standard_document_change_permission(self):
        self.client.force_login(self.seller)

        view_response = self.client.get(reverse('sale_payment_list', args=[self.sale.pk]))
        create_response = self.client.post(
            reverse('sale_payment_create', args=[self.sale.pk]),
            {'amount': '10.00', 'payment_type': Payment.CASH},
        )

        self.assertEqual(view_response.status_code, 200)
        self.assertEqual(create_response.status_code, 403)
        self.assertFalse(self.sale.payments.exists())

    def test_sale_form_can_explicitly_leave_the_invoice_unpaid(self):
        response = self.client.post(
            reverse('sale_create'),
            {
                'client': self.client_record.pk,
                'discount': '0.00',
                'tax_rate': '0.00',
                'payment_type': Sale.CASH,
                'settlement_action': SaleForm.NO_PAYMENT,
                'lines-TOTAL_FORMS': '1',
                'lines-INITIAL_FORMS': '0',
                'lines-MIN_NUM_FORMS': '0',
                'lines-MAX_NUM_FORMS': '1000',
                'lines-0-product': str(self.product.pk),
                'lines-0-quantity': '1',
                'lines-0-unit_price': '20.00',
            },
        )
        self.assertEqual(response.status_code, 302)
        sale = Sale.objects.exclude(pk=self.sale.pk).latest('pk')
        self.assertTrue(sale.payment_tracking_initialized)
        self.assertFalse(sale.payments.exists())
        self.assertEqual(sale.payment_status, 'unpaid')

    def test_sale_line_cost_is_snapshotted_and_never_recomputed(self):
        line = SaleLine.objects.create(
            sale=self.sale,
            product=self.product,
            quantity=2,
            unit_price='20.00',
        )
        self.assertEqual(line.unit_cost, Decimal('10.00'))

        self.product.purchase_price = Decimal('17.00')
        self.product.save(update_fields=['purchase_price'])
        line.unit_cost = Decimal('99.00')
        line.save(update_fields=['unit_cost'])
        line.refresh_from_db()
        self.assertEqual(line.unit_cost, Decimal('10.00'))

    def test_changing_the_product_refreshes_the_cost_snapshot(self):
        second = Product.objects.create(
            name='DeuxiÃ¨me produit paiement',
            category=self.category,
            brand=self.brand,
            unit=self.unit,
            purchase_price='22.00',
            sale_price='30.00',
            quantity=20,
            minimum_stock=1,
        )
        line = SaleLine.objects.create(
            sale=self.sale,
            product=self.product,
            quantity=1,
            unit_price='30.00',
        )
        line.product = second
        line.save()
        line.refresh_from_db()
        self.assertEqual(line.product, second)
        self.assertEqual(line.unit_cost, Decimal('22.00'))

    def test_sale_and_purchase_lists_are_paginated_by_25(self):
        for number in range(26):
            Sale.objects.create(
                invoice_number=f'FAC-PAGE-{number:03d}',
                client=self.client_record,
                total='0.00',
                discount='0.00',
                tax_rate='0.00',
            )
            Purchase.objects.create(
                reference=f'ACH-PAGE-{number:03d}',
                supplier=self.supplier,
                total='0.00',
                tax_rate='0.00',
            )

        sales_page_one = self.client.get(reverse('sale_list'))
        sales_page_two = self.client.get(reverse('sale_list'), {'page': 2})
        purchases_page_one = self.client.get(reverse('purchase_list'))
        purchases_page_two = self.client.get(reverse('purchase_list'), {'page': 2})
        self.assertEqual(len(sales_page_one.context['sales']), 25)
        self.assertEqual(len(sales_page_two.context['sales']), 2)
        self.assertEqual(len(purchases_page_one.context['purchases']), 25)
        self.assertEqual(len(purchases_page_two.context['purchases']), 2)
