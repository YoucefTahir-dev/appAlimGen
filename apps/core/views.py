from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.shortcuts import render
from django.utils import timezone
from apps.accounts.permissions import seller_required
from apps.inventory.models import Product, Client, Supplier
from apps.commerce.models import Sale, Purchase
from apps.expenses.models import Expense

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
    yearly_revenue = Sale.objects.filter(created_at__year=today.year).aggregate(total=Sum('total'))['total'] or 0

    expenses_today = Expense.objects.filter(date=today).aggregate(total=Sum('amount'))['total'] or 0
    monthly_expenses = Expense.objects.filter(date__month=today.month, date__year=today.year).aggregate(total=Sum('amount'))['total'] or 0
    yearly_expenses = Expense.objects.filter(date__year=today.year).aggregate(total=Sum('amount'))['total'] or 0
    cost_expression = ExpressionWrapper(
        F('lines__quantity') * F('lines__product__purchase_price'),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    monthly_cogs = Sale.objects.filter(
        created_at__month=today.month,
        created_at__year=today.year,
    ).aggregate(total=Sum(cost_expression))['total'] or 0
    gross_profit = monthly_revenue - monthly_cogs
    net_profit = gross_profit - monthly_expenses
    expense_categories = Expense.objects.filter(
        date__month=today.month,
        date__year=today.year,
    ).values('category__name').annotate(total=Sum('amount')).order_by('-total')[:8]

    out_of_stock = Product.objects.filter(quantity__lte=F('minimum_stock')).count()

    context = {
        'total_products': total_products,
        'stock_value': stock_value,
        'total_clients': total_clients,
        'total_suppliers': total_suppliers,
        'sales_today': sales_today,
        'purchases_today': purchases_today,
        'monthly_revenue': monthly_revenue,
        'yearly_revenue': yearly_revenue,
        'expenses_today': expenses_today,
        'monthly_expenses': monthly_expenses,
        'yearly_expenses': yearly_expenses,
        'gross_profit': gross_profit,
        'net_profit': net_profit,
        'expense_categories': expense_categories,
        'out_of_stock': out_of_stock,
    }
    return render(request, 'core/dashboard.html', context)
