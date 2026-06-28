import openpyxl
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import render, get_object_or_404, redirect
from apps.accounts.permissions import manager_required, seller_required
from .forms import ProductForm, ClientForm, SupplierForm, StockMovementForm, ImportExcelForm
from .models import Product, Client, Supplier, StockMovement
from django.http import FileResponse

@seller_required
def product_list(request):
    products = Product.objects.select_related('category', 'brand', 'unit')
    query = request.GET.get('q')
    if query:
        products = products.filter(name__icontains=query) | products.filter(reference__icontains=query) | products.filter(barcode__icontains=query)
    return render(request, 'inventory/product_list.html', {'products': products})

@manager_required
def product_create(request):
    form = ProductForm(request.POST or None, request.FILES or None)
    if form.is_valid():
        form.save()
        messages.success(request, 'Produit ajouté avec succès.')
        return redirect('product_list')
    return render(request, 'inventory/product_form.html', {'form': form, 'title': 'Ajouter un produit'})

@manager_required
def product_update(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = ProductForm(request.POST or None, request.FILES or None, instance=product)
    if form.is_valid():
        form.save()
        messages.success(request, 'Produit mis à jour.')
        return redirect('product_list')
    return render(request, 'inventory/product_form.html', {'form': form, 'title': 'Modifier le produit'})

@manager_required
def product_delete(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        product.delete()
        messages.success(request, 'Produit supprimé.')
        return redirect('product_list')
    return render(request, 'inventory/product_confirm_delete.html', {'product': product})

@manager_required
def product_import(request):
    form = ImportExcelForm(request.POST or None, request.FILES or None)
    if form.is_valid():
        workbook = openpyxl.load_workbook(form.cleaned_data['file'])
        sheet = workbook.active
        for row in sheet.iter_rows(min_row=2, values_only=True):
            reference, barcode, name, category, brand, unit, purchase_price, sale_price, quantity, minimum_stock = row[:10]
            Product.objects.update_or_create(
                reference=reference,
                defaults={
                    'barcode': barcode or '',
                    'name': name or '',
                    'purchase_price': purchase_price or 0,
                    'sale_price': sale_price or 0,
                    'quantity': int(quantity or 0),
                    'minimum_stock': int(minimum_stock or 0),
                },
            )
        messages.success(request, 'Importation Excel terminée.')
        return redirect('product_list')
    return render(request, 'inventory/product_import.html', {'form': form})

@manager_required
def product_export(request):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = 'Produits'
    headers = ['Référence', 'Code-barres', 'Nom produit', 'Prix d\'achat', 'Prix de vente', 'Quantité', 'Stock minimum']
    sheet.append(headers)
    for product in Product.objects.all():
        sheet.append([
            product.reference,
            product.barcode,
            product.name,
            float(product.purchase_price),
            float(product.sale_price),
            product.quantity,
            product.minimum_stock,
        ])
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename=produits.xlsx'
    workbook.save(response)
    return response

@manager_required
def client_list(request):
    clients = Client.objects.all()
    return render(request, 'inventory/client_list.html', {'clients': clients})

@seller_required
def client_create(request):
    form = ClientForm(request.POST or None)
    if form.is_valid():
        form.save()
        messages.success(request, 'Client ajouté avec succès.')
        return redirect('client_list')
    return render(request, 'inventory/client_form.html', {'form': form, 'title': 'Ajouter un client'})

@manager_required
def client_update(request, pk):
    client_obj = get_object_or_404(Client, pk=pk)
    form = ClientForm(request.POST or None, instance=client_obj)
    if form.is_valid():
        form.save()
        messages.success(request, 'Client mis à jour.')
        return redirect('client_list')
    return render(request, 'inventory/client_form.html', {'form': form, 'title': 'Modifier le client'})

@manager_required
def client_delete(request, pk):
    client_obj = get_object_or_404(Client, pk=pk)
    if request.method == 'POST':
        client_obj.delete()
        messages.success(request, 'Client supprimé.')
        return redirect('client_list')
    return render(request, 'inventory/client_confirm_delete.html', {'client': client_obj})

@manager_required
def supplier_list(request):
    suppliers = Supplier.objects.all()
    return render(request, 'inventory/supplier_list.html', {'suppliers': suppliers})

@manager_required
def supplier_create(request):
    form = SupplierForm(request.POST or None)
    if form.is_valid():
        form.save()
        messages.success(request, 'Fournisseur ajouté avec succès.')
        return redirect('supplier_list')
    return render(request, 'inventory/supplier_form.html', {'form': form, 'title': 'Ajouter un fournisseur'})

@manager_required
def supplier_update(request, pk):
    supplier_obj = get_object_or_404(Supplier, pk=pk)
    form = SupplierForm(request.POST or None, instance=supplier_obj)
    if form.is_valid():
        form.save()
        messages.success(request, 'Fournisseur mis à jour.')
        return redirect('supplier_list')
    return render(request, 'inventory/supplier_form.html', {'form': form, 'title': 'Modifier le fournisseur'})

@manager_required
def supplier_delete(request, pk):
    supplier_obj = get_object_or_404(Supplier, pk=pk)
    if request.method == 'POST':
        supplier_obj.delete()
        messages.success(request, 'Fournisseur supprimé.')
        return redirect('supplier_list')
    return render(request, 'inventory/supplier_confirm_delete.html', {'supplier': supplier_obj})

@seller_required
def stock_movement_list(request):
    movements = StockMovement.objects.select_related('product').order_by('-created_at')
    return render(request, 'inventory/stock_movement_list.html', {'movements': movements})

@manager_required
def stock_movement_create(request):
    form = StockMovementForm(request.POST or None)
    if form.is_valid():
        form.save()
        messages.success(request, 'Mouvement de stock enregistré.')
        return redirect('stock_movement_list')
    return render(request, 'inventory/stock_movement_form.html', {'form': form, 'title': 'Ajouter un mouvement de stock'})

@manager_required
def stock_movement_delete(request, pk):
    movement = get_object_or_404(StockMovement, pk=pk)
    if request.method == 'POST':
        movement.delete()
        messages.success(request, 'Mouvement de stock supprimé.')
        return redirect('stock_movement_list')
    return render(request, 'inventory/stock_movement_confirm_delete.html', {'movement': movement})


@seller_required
def product_detail(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if not product.barcode or not product.barcode_image or not product.qr_code:
        product.save()
        product.refresh_from_db()
    return render(request, 'inventory/product_detail.html', {'product': product})


@seller_required
def product_qr_download(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if not product.qr_code:
        messages.error(request, 'Aucun QR code disponible pour ce produit.')
        return redirect('product_detail', pk=pk)
    return FileResponse(product.qr_code.open('rb'), as_attachment=True, filename=f"{product.reference}_qr.png")


@seller_required
def product_barcode_download(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if not product.barcode_image:
        product.save()
        product.refresh_from_db()
    if not product.barcode_image:
        messages.error(request, 'Aucun code-barres disponible pour ce produit.')
        return redirect('product_detail', pk=pk)
    return FileResponse(product.barcode_image.open('rb'), as_attachment=True, filename=f"{product.reference}_barcode.svg")
