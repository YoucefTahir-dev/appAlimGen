from django.db.models import Sum, F
from django.shortcuts import render
from django.utils import timezone
from apps.accounts.permissions import seller_required
from apps.inventory.models import Product, Client, Supplier
from apps.commerce.models import Sale, Purchase

@seller_required
def dashboard(request):
    today = timezone.localtime(timezone.now()).date()
    total_products = Product.objects.count()
    stock_value = Product.objects.aggregate(total_value=Sum(F('purchase_price') * F('quantity')))['total_value'] or 0
    total_clients = Client.objects.count()
    total_suppliers = Supplier.objects.count()

    sales_today = Sale.objects.filter(created_at__date=today).aggregate(total=Sum('total'))['total'] or 0
    purchases_today = Purchase.objects.filter(created_at__date=today).aggregate(total=Sum('total'))['total'] or 0
    monthly_revenue = Sale.objects.filter(created_at__month=today.month, created_at__year=today.year).aggregate(total=Sum('total'))['total'] or 0

    out_of_stock = Product.objects.filter(quantity__lte=F('minimum_stock')).count()

    context = {
        'total_products': total_products,
        'stock_value': stock_value,
        'total_clients': total_clients,
        'total_suppliers': total_suppliers,
        'sales_today': sales_today,
        'purchases_today': purchases_today,
        'monthly_revenue': monthly_revenue,
        'out_of_stock': out_of_stock,
    }
    return render(request, 'core/dashboard.html', context)
