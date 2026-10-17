from decimal import Decimal, InvalidOperation
import hashlib

import openpyxl
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.shortcuts import render, get_object_or_404, redirect
from apps.accounts.permissions import manager_required, seller_required, permission_required, has_permission
from apps.core.security import log_security_event
from apps.core.export_security import excel_safe_text
from apps.core.pagination import paginate_queryset
from .forms import (
    CategoryDeleteForm, CategoryForm,
    ClientForm,
    ImportExcelForm,
    ProductForm,
    QuickProductForm,
    CartonConfigurationForm,
    ConditioningCorrectionForm,
    HistoricalStockConversionForm,
    OtherProductPackagingFormSet,
    LoadingOrderForm,
    LoadingOrderLineFormSet,
    StockMovementForm,
    SupplierForm,
)
from .models import (
    Brand, Category, Client, HistoricalStockConversion, LoadingOrder, OperatorStock,
    Product, ProductPackaging, StockMovement, Supplier, Unit,
)
from .services import (
    cancel_loading_order, close_loading_order, create_product_from_form, generate_loading_number,
    record_stock_movement, reverse_stock_movement, validate_loading_order,
)
from .historical_stock import (
    apply_historical_stock_conversion,
    build_conversion_plans,
    correct_conditioning,
    correction_preview,
)
from .location import audit_location, location_snapshot
from .geocoding import GeocodingService, GeocodingUnavailable
from django import forms
from django.core.cache import cache
from django.views.decorators.cache import never_cache
import time
from django.http import FileResponse
from django.utils.translation import gettext as _


@login_required
def loading_order_list(request):
    orders = LoadingOrder.objects.select_related('operator', 'created_by').prefetch_related(
        'lines__product__unit', 'lines__product__packagings', 'lines__packaging',
    )
    if not has_permission(request.user, 'inventory.view_all_loadingorders'):
        orders = orders.filter(operator=request.user)
    stocks = OperatorStock.objects.select_related('product__unit').prefetch_related(
        'product__packagings'
    ).filter(operator=request.user, quantity__gt=0)
    return render(request, 'inventory/loading_order_list.html', {'orders': orders, 'operator_stocks': stocks})


@permission_required('inventory.add_loadingorder')
@transaction.atomic
def loading_order_create(request):
    form = LoadingOrderForm(request.POST or None)
    order = LoadingOrder(created_by=request.user)
    formset = LoadingOrderLineFormSet(request.POST or None, instance=order, prefix='lines')
    if form.is_valid() and formset.is_valid():
        order = form.save(commit=False)
        order.number = generate_loading_number()
        order.created_by = request.user
        order.save()
        formset.instance = order
        formset.save()
        log_security_event(request, 'web.loading_order.create', status_code=201)
        messages.success(request, _('Bon de chargement créé.'))
        return redirect('loading_order_list')
    return render(request, 'inventory/loading_order_form.html', {'form': form, 'formset': formset, 'title': _('Nouveau chargement')})


@permission_required('inventory.change_loadingorder')
@transaction.atomic
def loading_order_update(request, pk):
    order = get_object_or_404(LoadingOrder.objects.select_for_update(), pk=pk)
    if order.status != LoadingOrder.DRAFT:
        messages.error(request, _('Seul un chargement brouillon peut être modifié.'))
        return redirect('loading_order_list')
    form = LoadingOrderForm(request.POST or None, instance=order)
    formset = LoadingOrderLineFormSet(request.POST or None, instance=order, prefix='lines')
    if form.is_valid() and formset.is_valid():
        form.save()
        formset.save()
        log_security_event(request, 'web.loading_order.update')
        messages.success(request, _('Bon de chargement modifié.'))
        return redirect('loading_order_list')
    return render(request, 'inventory/loading_order_form.html', {'form': form, 'formset': formset, 'title': _('Modifier le chargement')})


@permission_required('inventory.validate_loadingorder')
@require_POST
def loading_order_validate(request, pk):
    try:
        validate_loading_order(get_object_or_404(LoadingOrder, pk=pk), user=request.user)
        log_security_event(request, 'web.loading_order.validate')
        messages.success(request, _('Chargement validé et stock transféré à l’opérateur.'))
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
    return redirect('loading_order_list')


@permission_required('inventory.close_loadingorder')
@require_POST
def loading_order_close(request, pk):
    try:
        close_loading_order(get_object_or_404(LoadingOrder, pk=pk), user=request.user)
        log_security_event(request, 'web.loading_order.close')
        messages.success(request, _('Chargement clôturé et reliquat retourné au dépôt.'))
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
    return redirect('loading_order_list')


@permission_required('inventory.delete_loadingorder')
@require_POST
def loading_order_cancel(request, pk):
    try:
        cancel_loading_order(get_object_or_404(LoadingOrder, pk=pk))
        log_security_event(request, 'web.loading_order.cancel')
        messages.success(request, _('Chargement annulé.'))
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
    return redirect('loading_order_list')

@seller_required
def product_list(request):
    products = Product.objects.select_related('category', 'brand', 'unit')
    query = request.GET.get('q', '').strip()
    if query:
        products = products.filter(
            Q(name__icontains=query)
            | Q(reference__icontains=query)
            | Q(barcode__icontains=query)
        )
    page_obj, pagination_query = paginate_queryset(request, products.order_by('name', 'pk'))
    return render(
        request,
        'inventory/product_list.html',
        {'products': page_obj, 'page_obj': page_obj, 'pagination_query': pagination_query},
    )


@permission_required('inventory.view_product')
def category_list(request):
    categories = Category.objects.annotate(product_count=Count('products')).order_by('name', 'pk')
    return render(request, 'inventory/category_list.html', {'categories': categories})


@permission_required('inventory.add_product')
def category_create(request):
    form = CategoryForm(request.POST or None)
    if form.is_valid():
        try:
            with transaction.atomic():
                form.save()
        except IntegrityError:
            form.add_error('name', _('Une catégorie portant ce nom existe déjà.'))
        else:
            messages.success(request, _('Catégorie ajoutée.'))
            return redirect('category_list')
    return render(request, 'inventory/category_form.html', {
        'form': form, 'title': _('Ajouter une catégorie'),
    })


@permission_required('inventory.change_product')
def category_update(request, pk):
    category = get_object_or_404(Category, pk=pk)
    form = CategoryForm(request.POST or None, instance=category)
    if form.is_valid():
        try:
            with transaction.atomic():
                form.save()
        except IntegrityError:
            form.add_error('name', _('Une catégorie portant ce nom existe déjà.'))
        else:
            messages.success(request, _('Catégorie renommée.'))
            return redirect('category_list')
    return render(request, 'inventory/category_form.html', {
        'form': form, 'title': _('Modifier la catégorie'), 'category': category,
    })


@permission_required('inventory.delete_product')
def category_delete(request, pk):
    category = get_object_or_404(Category, pk=pk)
    form = CategoryDeleteForm(request.POST or None, category=category)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            locked = Category.objects.select_for_update().get(pk=category.pk)
            products = Product.objects.select_for_update().filter(category=locked).order_by('pk')
            replacement = form.cleaned_data['replacement']
            if products.exists() and replacement is None:
                form.add_error('replacement', _('Choisissez une catégorie de remplacement.'))
            else:
                products.update(category=replacement)
                locked.delete()
                messages.success(request, _('Catégorie supprimée.'))
                return redirect('category_list')
    return render(request, 'inventory/category_confirm_delete.html', {
        'category': category, 'form': form, 'product_count': category.products.count(),
    })


@require_POST
@permission_required('inventory.add_product')
def category_quick_create(request):
    form = CategoryForm(request.POST)
    if form.is_valid():
        try:
            with transaction.atomic():
                category = form.save()
        except IntegrityError:
            existing = Category.objects.filter(name__iexact=form.cleaned_data['name']).first()
            if existing:
                return JsonResponse({'id': existing.pk, 'name': existing.name})
            return JsonResponse({'errors': {'name': [_('Cette catégorie existe déjà.')]}}, status=409)
        return JsonResponse({'id': category.pk, 'name': category.name}, status=201)
    return JsonResponse({'errors': form.errors.get_json_data()}, status=400)


def _add_product_validation_errors(form, exc, fallback='quantity'):
    if hasattr(exc, 'error_dict'):
        for field_name, errors in exc.error_dict.items():
            target = field_name if field_name in form.fields else fallback
            for error in errors:
                form.add_error(target, error)
        return
    for error in exc.error_list:
        form.add_error(fallback, error)

@manager_required
def product_create(request):
    form = ProductForm(request.POST or None, request.FILES or None)
    packaging_data = request.POST if 'other_packagings-TOTAL_FORMS' in request.POST else None
    packaging_formset = OtherProductPackagingFormSet(packaging_data, prefix='other_packagings')
    posted_mode = request.POST.get('unit_mode') if request.method == 'POST' else Product.UnitMode.PIECE_ONLY
    carton_required = posted_mode in (Product.UnitMode.CARTON_ONLY, Product.UnitMode.CARTON_AND_PIECE)
    carton_form = CartonConfigurationForm(
        request.POST if carton_required else None,
        prefix='carton',
        required=carton_required,
    )
    form_valid = form.is_valid()
    if form_valid and carton_required:
        # ProductPackaging.clean() needs the (possibly unsaved) product to
        # validate carton prices against the piece purchase price.
        carton_form.instance.product = form.instance
    if form_valid and packaging_formset.is_bound:
        packaging_formset.instance = form.instance
    carton_valid = not carton_required or carton_form.is_valid()
    packaging_valid = not packaging_formset.is_bound or packaging_formset.is_valid()
    if form_valid and carton_valid and packaging_valid:
        try:
            product = create_product_from_form(
                form, user=request.user, packaging_formset=packaging_formset,
                carton_form=carton_form,
            )
        except ValidationError as exc:
            _add_product_validation_errors(form, exc)
        except IntegrityError:
            form.add_error(
                'barcode',
                _('Ce code-barres vient d’être utilisé. Générez-en un autre.'),
            )
        else:
            messages.success(request, _('Produit ajouté avec succès.'))
            if getattr(product, '_generated_media_errors', None):
                messages.warning(
                    request,
                    _(
                        'Le produit a été créé, mais ses images QR/code-barres '
                        'n’ont pas pu être stockées. Vérifiez le stockage média.'
                    ),
                )
            return redirect('product_list')
    return render(request, 'inventory/product_form.html', {
        'form': form, 'carton_form': carton_form,
        'packaging_formset': packaging_formset, 'title': _('Ajouter un produit'),
    })


@require_GET
@permission_required('inventory.add_product')
def product_generate_barcode(request):
    """Return a non-reserved internal EAN-13 suggestion for the Web editor."""
    return JsonResponse({'barcode': Product.generate_available_barcode()})

@require_POST
@permission_required('inventory.add_product', 'commerce.add_purchase')
def product_quick_create(request):
    form = QuickProductForm(request.POST, prefix='quick')
    if form.is_valid():
        try:
            product = create_product_from_form(form, user=request.user)
        except ValidationError as exc:
            for message in exc.messages:
                form.add_error(None, message)
        except IntegrityError:
            form.add_error(None, _('Ce produit existe déjà.'))
        else:
            log_security_event(request, _('Création produit depuis achat'), status_code=201)
            return JsonResponse({
                'success': True,
                'product': {
                    'id': product.pk, 'reference': product.reference,
                    'name': product.name, 'purchase_price': f'{product.purchase_price:.2f}',
                },
            }, status=201)
    return JsonResponse({'success': False, 'errors': form.errors.get_json_data()}, status=400)


@manager_required
def product_update(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = ProductForm(request.POST or None, request.FILES or None, instance=product)
    carton = ProductPackaging.objects.filter(product=product, kind=ProductPackaging.CARTON).first()
    packaging_data = request.POST if 'other_packagings-TOTAL_FORMS' in request.POST else None
    packaging_formset = OtherProductPackagingFormSet(
        packaging_data, instance=product, prefix='other_packagings',
    )
    posted_mode = request.POST.get('unit_mode') if request.method == 'POST' else product.unit_mode
    carton_required = posted_mode in (Product.UnitMode.CARTON_ONLY, Product.UnitMode.CARTON_AND_PIECE)
    carton_form = CartonConfigurationForm(
        request.POST if request.method == 'POST' and carton_required else None,
        instance=carton,
        prefix='carton',
        required=carton_required,
    )
    form_valid = form.is_valid()
    if form_valid and carton_required:
        carton_form.instance.product = form.instance
    carton_valid = not carton_required or carton_form.is_valid()
    packaging_valid = not packaging_formset.is_bound or packaging_formset.is_valid()
    if form_valid and carton_valid and packaging_valid:
        try:
            with transaction.atomic():
                locked_product = Product.objects.select_for_update().get(pk=product.pk)
                form.instance.quantity = locked_product.quantity
                saved_product = form.save()
                if packaging_formset.is_bound:
                    packaging_formset.instance = saved_product
                    packaging_formset.save()
                if saved_product.unit_mode in (
                    Product.UnitMode.CARTON_ONLY, Product.UnitMode.CARTON_AND_PIECE,
                ):
                    # Serialize packaging edits with sales/purchases that
                    # snapshot the factor.  This lock protects only metadata;
                    # it never recalculates canonical piece stock.
                    ProductPackaging.objects.select_for_update().filter(
                        product=saved_product, kind=ProductPackaging.CARTON,
                    ).first()
                    carton_form.save_for(saved_product)
                elif saved_product.unit_mode == Product.UnitMode.PIECE_ONLY:
                    CartonConfigurationForm.disable_for(saved_product)
        except ValidationError as exc:
            _add_product_validation_errors(form, exc)
        except IntegrityError:
            form.add_error(
                'barcode',
                _('Ce code-barres vient d’être utilisé. Générez-en un autre.'),
            )
        else:
            messages.success(request, _('Produit mis à jour.'))
            return redirect('product_list')
    return render(request, 'inventory/product_form.html', {
        'form': form, 'carton_form': carton_form,
        'packaging_formset': packaging_formset, 'title': _('Modifier le produit'),
    })

@manager_required
def product_delete(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        try:
            product.delete()
        except ProtectedError:
            messages.error(
                request,
                _('Ce produit ne peut pas être supprimé car il possède un historique de stock ou commercial.'),
            )
        else:
            messages.success(request, _('Produit supprimé.'))
        return redirect('product_list')
    return render(request, 'inventory/product_confirm_delete.html', {'product': product})

@manager_required
def product_import(request):
    form = ImportExcelForm(request.POST or None, request.FILES or None)
    if form.is_valid():
        try:
            with transaction.atomic():
                workbook = openpyxl.load_workbook(form.cleaned_data['file'], read_only=True, data_only=True)
                sheet = workbook.active
                for row in sheet.iter_rows(min_row=2, values_only=True):
                    reference, barcode, name, category, brand, unit, purchase_price, sale_price, quantity, minimum_stock = row[:10]
                    tier_values = list(row[10:13])
                    tier_values.extend([None] * (3 - len(tier_values)))
                    reference = str(reference or '').strip()
                    name = str(name or '').strip()
                    if not name:
                        raise ValueError('missing product name')

                    try:
                        purchase_price = Decimal(str(purchase_price or 0))
                        sale_price = Decimal(str(sale_price or 0))
                        super_wholesale_price = Decimal(str(tier_values[0] if tier_values[0] is not None else sale_price))
                        wholesale_price = Decimal(str(tier_values[1] if tier_values[1] is not None else sale_price))
                        retail_price = Decimal(str(tier_values[2] if tier_values[2] is not None else sale_price))
                        quantity_decimal = Decimal(str(quantity or 0))
                        minimum_stock_decimal = Decimal(str(minimum_stock or 0))
                    except InvalidOperation as exc:
                        raise ValueError('invalid numeric value') from exc
                    if (
                        purchase_price < 0
                        or sale_price < 0
                        or super_wholesale_price < purchase_price
                        or wholesale_price < purchase_price
                        or retail_price < purchase_price
                        or not super_wholesale_price <= wholesale_price <= retail_price
                        or quantity_decimal < 0
                        or minimum_stock_decimal < 0
                        or quantity_decimal != quantity_decimal.to_integral_value()
                        or minimum_stock_decimal != minimum_stock_decimal.to_integral_value()
                    ):
                        raise ValueError('invalid stock or price value')

                    category_obj = Category.objects.resolve(category) if category else None
                    brand_obj = Brand.objects.resolve(brand) if brand else None
                    unit_obj = Unit.objects.resolve(unit) if unit else None
                    product = None
                    if reference:
                        product = Product.objects.select_for_update().filter(reference=reference).first()

                    target_quantity = int(quantity_decimal)
                    if product is None:
                        product = Product(
                            reference=reference,
                            barcode=str(barcode or '').strip(),
                            name=name,
                            category=category_obj,
                            brand=brand_obj,
                            unit=unit_obj,
                            purchase_price=purchase_price,
                            sale_price=sale_price,
                            super_wholesale_price=super_wholesale_price,
                            wholesale_price=wholesale_price,
                            retail_price=retail_price,
                            quantity=0,
                            minimum_stock=int(minimum_stock_decimal),
                        )
                        product.save()
                    else:
                        current_quantity = product.quantity
                        product.barcode = str(barcode or '').strip()
                        product.name = name
                        product.category = category_obj
                        product.brand = brand_obj
                        product.unit = unit_obj
                        product.purchase_price = purchase_price
                        product.sale_price = sale_price
                        product.super_wholesale_price = super_wholesale_price
                        product.wholesale_price = wholesale_price
                        product.retail_price = retail_price
                        product.quantity = current_quantity
                        product.minimum_stock = int(minimum_stock_decimal)
                        product.save()

                    if product.quantity != target_quantity:
                        record_stock_movement(
                            product=product,
                            movement_type=StockMovement.ADJUSTMENT,
                            quantity=target_quantity,
                            reason=_('Synchronisation du stock par import Excel'),
                            user=request.user,
                            source_type=StockMovement.SOURCE_IMPORT,
                            source_reference=product.reference,
                        )
                workbook.close()
        except (
            DatabaseError,
            OSError,
            TypeError,
            ValueError,
            ValidationError,
            openpyxl.utils.exceptions.InvalidFileException,
        ):
            form.add_error('file', _('Le fichier Excel contient des données invalides. Aucune donnée n’a été importée.'))
        else:
            messages.success(request, _('Importation Excel terminée.'))
            return redirect('product_list')
    return render(request, 'inventory/product_import.html', {'form': form})

@manager_required
def product_export(request):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = str(_('Produits'))[:31]
    headers = [
        _('Référence'),
        _('Code-barres'),
        _('Nom produit'),
        _('Catégorie'),
        _('Marque'),
        _('Unité'),
        _("Prix d'achat"),
        _('Prix de vente'),
        _('Quantité'),
        _('Stock minimum'),
        _('Prix Super Gros'),
        _('Prix Gros'),
        _('Prix Détail'),
    ]
    sheet.append(headers)
    for product in Product.objects.all():
        sheet.append([
            excel_safe_text(product.reference),
            excel_safe_text(product.barcode),
            excel_safe_text(product.name),
            excel_safe_text(product.category),
            excel_safe_text(product.brand),
            excel_safe_text(product.unit),
            float(product.purchase_price),
            float(product.sale_price),
            product.quantity,
            product.minimum_stock,
            float(product.super_wholesale_price),
            float(product.wholesale_price),
            float(product.retail_price),
        ])
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename=produits.xlsx'
    workbook.save(response)
    return response

@manager_required
def client_list(request):
    clients = Client.objects.order_by('name', 'pk')
    query = request.GET.get('q', '').strip()
    customer_type = request.GET.get('customer_type', '').strip()
    if query:
        clients = clients.filter(
            Q(name__icontains=query)
            | Q(phone__icontains=query)
            | Q(email__icontains=query)
            | Q(tax_number__icontains=query)
            | Q(nis__icontains=query)
            | Q(article_number__icontains=query)
            | Q(trade_register_number__icontains=query)
        )
    if customer_type in Client.CustomerType.values:
        clients = clients.filter(customer_type=customer_type)
    page_obj, pagination_query = paginate_queryset(request, clients)
    return render(
        request,
        'inventory/client_list.html',
        {
            'clients': page_obj,
            'page_obj': page_obj,
            'pagination_query': pagination_query,
            'customer_types': Client.CustomerType.choices,
            'selected_customer_type': customer_type,
        },
    )


@manager_required
def client_export(request):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = str(_('Clients'))[:31]
    sheet.append([
        _('Nom'),
        _('Téléphone'),
        _('Adresse'),
        _('Wilaya'),
        _('Type de client'),
        _('NIF'),
        _('Solde'),
        _('Notes'),
    ])
    for client in Client.objects.order_by('name', 'pk'):
        sheet.append([
            excel_safe_text(client.name),
            excel_safe_text(client.phone),
            excel_safe_text(client.address),
            excel_safe_text(client.wilaya),
            excel_safe_text(client.get_customer_type_display()),
            excel_safe_text(client.tax_number),
            float(client.balance),
            excel_safe_text(client.notes),
        ])
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename=clients.xlsx'
    workbook.save(response)
    return response


@seller_required
def client_create(request):
    form = ClientForm(request.POST or None)
    if form.is_valid():
        client_obj = form.save()
        audit_location(request, client_obj, None)
        messages.success(request, _('Client ajouté avec succès.'))
        return redirect('client_list')
    return render(request, 'inventory/client_form.html', {'form': form, 'title': _('Ajouter un client')})


@manager_required
def client_detail(request, pk):
    client_obj = get_object_or_404(Client, pk=pk)
    sales = client_obj.sales.order_by('-created_at', '-pk')[:50] if has_permission(request.user, 'commerce.view_sale') else client_obj.sales.none()
    return render(
        request,
        'inventory/client_detail.html',
        {'client_obj': client_obj, 'sales': sales},
    )


@manager_required
def client_update(request, pk):
    client_obj = get_object_or_404(Client, pk=pk)
    before = location_snapshot(client_obj)
    form = ClientForm(request.POST or None, instance=client_obj)
    if form.is_valid():
        form.save()
        audit_location(request, client_obj, before)
        messages.success(request, _('Client mis à jour.'))
        return redirect('client_list')
    return render(request, 'inventory/client_form.html', {'form': form, 'title': _('Modifier le client')})

@manager_required
def client_delete(request, pk):
    client_obj = get_object_or_404(Client, pk=pk)
    if request.method == 'POST':
        client_obj.delete()
        messages.success(request, _('Client supprimé.'))
        return redirect('client_list')
    return render(request, 'inventory/client_confirm_delete.html', {'client': client_obj})


@never_cache
@require_POST
@permission_required('inventory.add_client', 'inventory.change_client', any_permission=True)
def client_reverse_geocode(request):
    # This endpoint proposes an address only; it never writes a client.
    from .location import validate_finite
    coordinates = {}
    try:
        for name, bound in (('latitude', 90), ('longitude', 180)):
            coordinates[name] = forms.FloatField(min_value=-bound, max_value=bound, validators=[validate_finite]).clean(request.POST.get(name))
    except ValidationError:
        return JsonResponse({'error': _('Coordonnée ou précision GPS invalide.')}, status=400)
    key = f'client-geocode:{request.user.pk}:{int(time.time()) // 60}'
    if not cache.add(key, 1, timeout=65):
        try:
            count = cache.incr(key)
        except ValueError:
            count = 1
            cache.set(key, count, timeout=65)
        if count > 30:
            return JsonResponse({'error': _('Trop de demandes. Veuillez patienter.')}, status=429)
    try:
        result = GeocodingService().reverse_geocode(**coordinates)
        return JsonResponse({'success': True, 'address': result.get('formatted_address', ''), **result})
    except GeocodingUnavailable as exc:
        return JsonResponse({
            'success': False, 'code': exc.code,
            'error': _('Votre position a été détectée, mais l’adresse n’a pas pu être récupérée.'),
        }, status=503)

@manager_required
def supplier_list(request):
    suppliers = Supplier.objects.order_by('name', 'pk')
    query = request.GET.get('q', '').strip()
    if query:
        suppliers = suppliers.filter(
            Q(name__icontains=query)
            | Q(phone__icontains=query)
            | Q(email__icontains=query)
            | Q(rc_number__icontains=query)
            | Q(tax_number__icontains=query)
        )
    page_obj, pagination_query = paginate_queryset(request, suppliers)
    return render(
        request,
        'inventory/supplier_list.html',
        {'suppliers': page_obj, 'page_obj': page_obj, 'pagination_query': pagination_query},
    )

@manager_required
def supplier_create(request):
    form = SupplierForm(request.POST or None)
    if form.is_valid():
        form.save()
        messages.success(request, _('Fournisseur ajouté avec succès.'))
        return redirect('supplier_list')
    return render(request, 'inventory/supplier_form.html', {'form': form, 'title': _('Ajouter un fournisseur')})

@manager_required
def supplier_update(request, pk):
    supplier_obj = get_object_or_404(Supplier, pk=pk)
    form = SupplierForm(request.POST or None, instance=supplier_obj)
    if form.is_valid():
        form.save()
        messages.success(request, _('Fournisseur mis à jour.'))
        return redirect('supplier_list')
    return render(request, 'inventory/supplier_form.html', {'form': form, 'title': _('Modifier le fournisseur')})

@manager_required
def supplier_delete(request, pk):
    supplier_obj = get_object_or_404(Supplier, pk=pk)
    if request.method == 'POST':
        supplier_obj.delete()
        messages.success(request, _('Fournisseur supprimé.'))
        return redirect('supplier_list')
    return render(request, 'inventory/supplier_confirm_delete.html', {'supplier': supplier_obj})

@seller_required
def stock_movement_list(request):
    movements = (
        StockMovement.objects.select_related('product', 'created_by')
        .annotate(has_reversal=Exists(StockMovement.objects.filter(reversal_of=OuterRef('pk'))))
        .order_by('-created_at', '-pk')
    )
    page_obj, pagination_query = paginate_queryset(request, movements)
    return render(
        request,
        'inventory/stock_movement_list.html',
        {'movements': page_obj, 'page_obj': page_obj, 'pagination_query': pagination_query},
    )

@manager_required
def stock_movement_create(request):
    form = StockMovementForm(request.POST or None)
    if form.is_valid():
        movement = form.save(commit=False)
        try:
            record_stock_movement(
                product=movement.product,
                movement_type=movement.movement_type,
                quantity=movement.quantity,
                reason=movement.reason,
                user=request.user,
            )
        except ValidationError as exc:
            if hasattr(exc, 'message_dict'):
                for field, errors in exc.message_dict.items():
                    target = field if field in form.fields else None
                    for error in errors:
                        form.add_error(target, error)
            else:
                for error in exc.messages:
                    form.add_error(None, error)
        else:
            messages.success(request, _('Mouvement de stock enregistré.'))
            return redirect('stock_movement_list')
    return render(request, 'inventory/stock_movement_form.html', {'form': form, 'title': _('Ajouter un mouvement de stock')})

@manager_required
def stock_movement_delete(request, pk):
    movement = get_object_or_404(StockMovement, pk=pk)
    if request.method == 'POST':
        try:
            reverse_stock_movement(movement, user=request.user)
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        else:
            messages.success(request, _('Mouvement annulé par une écriture de contrepassation.'))
        return redirect('stock_movement_list')
    return render(request, 'inventory/stock_movement_confirm_delete.html', {'movement': movement})


@seller_required
def product_detail(request, pk):
    product = get_object_or_404(
        Product.objects.prefetch_related(
            'packagings', 'conditioning_corrections__created_by',
        ),
        pk=pk,
    )
    if not product.barcode or not product.barcode_image or not product.qr_code:
        product.save()
        product.refresh_from_db()
    return render(request, 'inventory/product_detail.html', {'product': product})


@permission_required('inventory.regularize_product_conditioning')
def product_conditioning_correct(request, pk):
    product = get_object_or_404(
        Product.objects.select_related('historical_stock_conversion').prefetch_related(
            'historical_stock_conversion__operator_locations__operator',
            'conditioning_corrections__created_by',
        ),
        pk=pk,
    )
    try:
        conversion = product.historical_stock_conversion
    except HistoricalStockConversion.DoesNotExist:
        messages.error(request, _('Ce produit ne possède pas de conversion historique à corriger.'))
        return redirect('product_detail', pk=product.pk)

    form = ConditioningCorrectionForm(request.POST or None, product=product)
    factor_value = request.POST.get('new_factor') if request.method == 'POST' else conversion.factor
    try:
        factor_for_preview = max(2, int(factor_value))
    except (TypeError, ValueError):
        factor_for_preview = conversion.factor
    preview = correction_preview(conversion=conversion, new_factor=factor_for_preview)

    if request.method == 'POST' and form.is_valid():
        try:
            correction = correct_conditioning(
                product=product,
                new_factor=form.cleaned_data['new_factor'],
                reason=form.cleaned_data['reason'],
                user=request.user,
                physical_depot_quantity=form.cleaned_data.get('physical_depot_quantity'),
                physical_operator_quantities=form.physical_operator_quantities(),
            )
        except ValidationError as exc:
            _add_product_validation_errors(form, exc, fallback=None)
        else:
            log_security_event(
                request,
                f'web.product.conditioning_correct product={product.pk} correction={correction.pk}',
                status_code=200,
            )
            messages.success(request, _('Le conditionnement et le stock ont été régularisés.'))
            return redirect('product_detail', pk=product.pk)

    return render(request, 'inventory/product_conditioning_correct.html', {
        'product': product,
        'conversion': conversion,
        'form': form,
        'preview': preview,
        'physical_operator_fields': [
            form[field_name]
            for field_name in form.fields
            if field_name.startswith('operator_')
        ],
    })


@permission_required('inventory.regularize_product_conditioning')
def product_conditioning_convert(request, pk):
    product = get_object_or_404(
        Product.objects.prefetch_related('packagings', 'operator_stocks__operator'),
        pk=pk,
    )
    if product.unit_mode != Product.UnitMode.LEGACY:
        messages.info(request, _('Le conditionnement de ce produit est déjà confirmé.'))
        return redirect('product_detail', pk=product.pk)
    if HistoricalStockConversion.objects.filter(product=product).exists():
        messages.info(request, _('La conversion historique de ce produit a déjà été appliquée.'))
        return redirect('product_detail', pk=product.pk)

    form = HistoricalStockConversionForm(request.POST or None, product=product)
    raw_factor = request.POST.get('factor') if request.method == 'POST' else form.fields['factor'].initial
    preview_factor = None
    try:
        if raw_factor not in (None, ''):
            preview_factor = max(2, int(raw_factor))
    except (TypeError, ValueError):
        preview_factor = None
    # The historical service still needs a deterministic fallback to build
    # blockers, but it must never be shown as a confirmed physical factor.
    factor = preview_factor or HistoricalStockConversion.DEFAULT_FACTOR
    plan = build_conversion_plans(
        default_factor=factor,
        queryset=Product.objects.filter(pk=product.pk),
    )[0]

    if request.method == 'POST' and form.is_valid():
        factor = form.cleaned_data['factor']
        checksum_source = '|'.join((
            str(product.pk), str(product.quantity), str(product.minimum_stock), str(factor),
            ','.join(
                f'{stock.operator_id}:{stock.quantity}'
                for stock in product.operator_stocks.all().order_by('operator_id')
            ),
        ))
        checksum = hashlib.sha256(checksum_source.encode('utf-8')).hexdigest()
        try:
            conversion, created = apply_historical_stock_conversion(
                product=product,
                user=request.user,
                confirmed_factor=factor,
                backup_reference=form.cleaned_data['backup_reference'],
                simulation_checksum=checksum,
            )
        except ValidationError as exc:
            _add_product_validation_errors(form, exc, fallback=None)
        else:
            log_security_event(
                request,
                f'web.product.historical_stock_convert product={product.pk} conversion={conversion.pk}',
                status_code=200,
            )
            if created:
                messages.success(
                    request,
                    _('Le stock historique a été converti en pièces avec succès.'),
                )
            return redirect('product_update', pk=product.pk)

    operator_preview = [
        {
            'name': stock.operator.get_full_name() or stock.operator.get_username(),
            'before': stock.quantity,
            'after': stock.quantity * preview_factor if preview_factor else '—',
        }
        for stock in product.operator_stocks.all().order_by('operator_id')
    ]
    return render(request, 'inventory/product_conditioning_convert.html', {
        'product': product,
        'form': form,
        'factor': preview_factor,
        'plan': plan,
        'depot_after': product.quantity * preview_factor if preview_factor else '—',
        'minimum_after': product.minimum_stock * preview_factor if preview_factor else '—',
        'operator_preview': operator_preview,
    })


@seller_required
def product_qr_download(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if not product.qr_code:
        messages.error(request, _('Aucun QR code disponible pour ce produit.'))
        return redirect('product_detail', pk=pk)
    return FileResponse(product.qr_code.open('rb'), as_attachment=True, filename=f"{product.reference}_qr.png")


@seller_required
def product_barcode_download(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if not product.barcode_image:
        product.save()
        product.refresh_from_db()
    if not product.barcode_image:
        messages.error(request, _('Aucun code-barres disponible pour ce produit.'))
        return redirect('product_detail', pk=pk)
    return FileResponse(product.barcode_image.open('rb'), as_attachment=True, filename=f"{product.reference}_barcode.svg")
