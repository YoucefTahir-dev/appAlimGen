import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncDay, TruncMonth
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _

from apps.commerce.models import Payment, Purchase, PurchaseLine, Sale, SaleLine
from apps.expenses.models import Expense
from apps.inventory.models import Client, Product, Supplier


ZERO = Decimal("0.00")
IMPORTANT_EXPENSE_THRESHOLD = Decimal("100000.00")


def money_value(value):
    return value or ZERO


def local_day_bounds(day):
    current_tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time.min), current_tz)
    end = timezone.make_aware(datetime.combine(day, time.max), current_tz)
    return start, end


def get_period_bounds(request):
    today = timezone.localdate()
    period = request.GET.get("period", "today")
    start_date = None
    end_date = None

    if period == "yesterday":
        start_date = end_date = today - timedelta(days=1)
    elif period == "week":
        start_date = today - timedelta(days=today.weekday())
        end_date = today
    elif period == "month":
        start_date = today.replace(day=1)
        end_date = today
    elif period == "year":
        start_date = today.replace(month=1, day=1)
        end_date = today
    elif period == "custom":
        start_date = parse_date(request.GET.get("start_date") or "") or today
        end_date = parse_date(request.GET.get("end_date") or "") or start_date
        if end_date < start_date:
            start_date, end_date = end_date, start_date
    else:
        period = "today"
        start_date = end_date = today

    start_dt, _ = local_day_bounds(start_date)
    _, end_dt = local_day_bounds(end_date)
    previous_days = (end_date - start_date).days + 1
    previous_end_date = start_date - timedelta(days=1)
    previous_start_date = previous_end_date - timedelta(days=previous_days - 1)
    previous_start_dt, _ = local_day_bounds(previous_start_date)
    _, previous_end_dt = local_day_bounds(previous_end_date)

    return {
        "period": period,
        "start_date": start_date,
        "end_date": end_date,
        "start_dt": start_dt,
        "end_dt": end_dt,
        "previous_start_date": previous_start_date,
        "previous_end_date": previous_end_date,
        "previous_start_dt": previous_start_dt,
        "previous_end_dt": previous_end_dt,
    }


def percent_change(current, previous):
    current = Decimal(current or 0)
    previous = Decimal(previous or 0)
    if previous == 0:
        return Decimal("100.00") if current > 0 else Decimal("0.00")
    return ((current - previous) / previous * Decimal("100")).quantize(Decimal("0.01"))


def comparison_payload(current, previous):
    change = percent_change(current, previous)
    return {
        "current": current,
        "previous": previous,
        "percent": change,
        "direction": "up" if change > 0 else "down" if change < 0 else "flat",
    }


def sales_total_between(start_dt, end_dt):
    return money_value(Sale.objects.filter(created_at__range=(start_dt, end_dt)).aggregate(total=Sum("total"))["total"])


def purchases_total_between(start_dt, end_dt):
    return money_value(Purchase.objects.filter(created_at__range=(start_dt, end_dt)).aggregate(total=Sum("total"))["total"])


def expenses_total_between(start_date, end_date):
    return money_value(Expense.objects.filter(date__range=(start_date, end_date)).aggregate(total=Sum("amount"))["total"])


def cogs_between(start_dt, end_dt):
    cost_expression = ExpressionWrapper(
        F("quantity") * F("product__purchase_price"),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    return money_value(
        SaleLine.objects.filter(sale__created_at__range=(start_dt, end_dt)).aggregate(total=Sum(cost_expression))["total"]
    )


def quantity_sum(queryset):
    return queryset.aggregate(total=Sum("quantity"))["total"] or 0


def outstanding_count(model, start_dt, end_dt):
    return (
        model.objects.filter(created_at__range=(start_dt, end_dt))
        .annotate(paid=Coalesce(Sum("payments__amount"), Value(ZERO), output_field=DecimalField(max_digits=14, decimal_places=2)))
        .filter(total__gt=F("paid"))
        .count()
    )


def trend_bucket(start_date, end_date):
    return TruncMonth if (end_date - start_date).days > 90 else TruncDay


def bucket_key(value):
    if isinstance(value, datetime):
        return timezone.localtime(value).date().isoformat()
    return value.isoformat()


def series_from_queryset(queryset, trunc, total_field="total", date_field="created_at"):
    rows = (
        queryset.annotate(bucket=trunc(date_field))
        .values("bucket")
        .annotate(total=Sum(total_field))
        .order_by("bucket")
    )
    return {bucket_key(row["bucket"]): float(row["total"] or 0) for row in rows}


def expense_series(start_date, end_date, trunc):
    rows = (
        Expense.objects.filter(date__range=(start_date, end_date))
        .annotate(bucket=trunc("date"))
        .values("bucket")
        .annotate(total=Sum("amount"))
        .order_by("bucket")
    )
    return {row["bucket"].isoformat(): float(row["total"] or 0) for row in rows}


def build_chart_data(bounds):
    trunc = trend_bucket(bounds["start_date"], bounds["end_date"])
    sales = series_from_queryset(Sale.objects.filter(created_at__range=(bounds["start_dt"], bounds["end_dt"])), trunc)
    purchases = series_from_queryset(Purchase.objects.filter(created_at__range=(bounds["start_dt"], bounds["end_dt"])), trunc)
    sold_costs = series_from_queryset(
        SaleLine.objects.filter(sale__created_at__range=(bounds["start_dt"], bounds["end_dt"])).annotate(
            line_cost=ExpressionWrapper(
                F("quantity") * F("product__purchase_price"),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        ),
        trunc,
        "line_cost",
        "sale__created_at",
    )
    expenses = expense_series(bounds["start_date"], bounds["end_date"], trunc)
    labels = sorted(set(sales) | set(purchases) | set(sold_costs) | set(expenses))
    revenue_values = [sales.get(label, 0) for label in labels]
    purchase_values = [purchases.get(label, 0) for label in labels]
    sold_cost_values = [sold_costs.get(label, 0) for label in labels]
    expense_values = [expenses.get(label, 0) for label in labels]
    gross_values = [revenue_values[index] - sold_cost_values[index] for index in range(len(labels))]
    net_values = [gross_values[index] - expense_values[index] for index in range(len(labels))]

    sale_lines_with_totals = SaleLine.objects.filter(
        sale__created_at__range=(bounds["start_dt"], bounds["end_dt"])
    ).annotate(
        line_total=ExpressionWrapper(
            F("quantity") * F("unit_price"),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
    )

    sales_by_category = list(
        sale_lines_with_totals
        .values("product__category__name")
        .annotate(total=Sum("line_total"))
        .order_by("-total")[:10]
    )
    expenses_by_category = list(
        Expense.objects.filter(date__range=(bounds["start_date"], bounds["end_date"]))
        .values("category__name")
        .annotate(total=Sum("amount"))
        .order_by("-total")[:10]
    )

    return {
        "trend": {
            "labels": labels,
            "revenue": revenue_values,
            "purchases": purchase_values,
            "expenses": expense_values,
            "gross_profit": gross_values,
            "net_profit": net_values,
        },
        "sales_categories": {
            "labels": [row["product__category__name"] or _("Sans catégorie") for row in sales_by_category],
            "values": [float(row["total"] or 0) for row in sales_by_category],
        },
        "expense_categories": {
            "labels": [row["category__name"] or _("Sans catégorie") for row in expenses_by_category],
            "values": [float(row["total"] or 0) for row in expenses_by_category],
        },
    }


def dashboard_context(request):
    bounds = get_period_bounds(request)
    today = timezone.localdate()
    today_start, today_end = local_day_bounds(today)

    sales_qs = Sale.objects.filter(created_at__range=(bounds["start_dt"], bounds["end_dt"]))
    purchases_qs = Purchase.objects.filter(created_at__range=(bounds["start_dt"], bounds["end_dt"]))
    sale_lines_qs = SaleLine.objects.filter(
        sale__created_at__range=(bounds["start_dt"], bounds["end_dt"])
    ).annotate(
        line_total=ExpressionWrapper(
            F("quantity") * F("unit_price"),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
        line_profit=ExpressionWrapper(
            F("quantity") * (F("unit_price") - F("product__purchase_price")),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
    )
    purchase_lines_qs = PurchaseLine.objects.filter(purchase__created_at__range=(bounds["start_dt"], bounds["end_dt"]))
    expenses_qs = Expense.objects.filter(date__range=(bounds["start_date"], bounds["end_date"]))

    period_revenue = money_value(sales_qs.aggregate(total=Sum("total"))["total"])
    previous_revenue = sales_total_between(bounds["previous_start_dt"], bounds["previous_end_dt"])
    sales_count = sales_qs.count()
    purchases_total = money_value(purchases_qs.aggregate(total=Sum("total"))["total"])
    expenses_total = money_value(expenses_qs.aggregate(total=Sum("amount"))["total"])
    gross_profit = period_revenue - cogs_between(bounds["start_dt"], bounds["end_dt"])
    net_profit = gross_profit - expenses_total
    previous_gross = previous_revenue - cogs_between(bounds["previous_start_dt"], bounds["previous_end_dt"])
    previous_expenses = expenses_total_between(bounds["previous_start_date"], bounds["previous_end_date"])
    previous_net = previous_gross - previous_expenses

    stock_value = money_value(
        Product.objects.aggregate(
            total=Sum(ExpressionWrapper(F("purchase_price") * F("quantity"), output_field=DecimalField(max_digits=14, decimal_places=2)))
        )["total"]
    )

    out_of_stock = Product.objects.filter(quantity=0).count()
    low_stock = Product.objects.filter(quantity__gt=0, quantity__lte=F("minimum_stock")).count()
    near_stockout = Product.objects.filter(quantity__gt=F("minimum_stock"), quantity__lte=F("minimum_stock") + 5).count()
    unpaid_invoices = outstanding_count(Sale, bounds["start_dt"], bounds["end_dt"])
    pending_supplier_payments = outstanding_count(Purchase, bounds["start_dt"], bounds["end_dt"])
    important_expenses = expenses_qs.filter(amount__gte=IMPORTANT_EXPENSE_THRESHOLD).count()
    notification_count = out_of_stock + low_stock + near_stockout + unpaid_invoices + pending_supplier_payments + important_expenses

    top_products = (
        sale_lines_qs.values("product__name")
        .annotate(quantity=Sum("quantity"), total=Sum("line_total"))
        .order_by("-quantity")[:10]
    )
    top_clients = sales_qs.values("client__name").annotate(total=Sum("total"), count=Count("pk")).order_by("-total")[:10]
    top_suppliers = purchases_qs.values("supplier__name").annotate(total=Sum("total"), count=Count("pk")).order_by("-total")[:10]
    profitable_products = (
        sale_lines_qs.values("product__name")
        .annotate(profit=Sum("line_profit"), quantity=Sum("quantity"))
        .order_by("-profit")[:10]
    )
    least_sold_products = (
        Product.objects.annotate(
            sold_quantity=Coalesce(
                Sum("saleline__quantity", filter=Q(saleline__sale__created_at__range=(bounds["start_dt"], bounds["end_dt"]))),
                Value(0),
            )
        )
        .order_by("sold_quantity", "name")[:10]
    )

    chart_data = build_chart_data(bounds)

    return {
        **bounds,
        "period_options": [
            ("today", _("Aujourd'hui")),
            ("yesterday", _("Hier")),
            ("week", _("Cette semaine")),
            ("month", _("Ce mois")),
            ("year", _("Cette année")),
            ("custom", _("Période personnalisée")),
        ],
        "sales_today": sales_total_between(today_start, today_end),
        "period_revenue": period_revenue,
        "sales_count": sales_count,
        "average_basket": period_revenue / sales_count if sales_count else ZERO,
        "gross_profit": gross_profit,
        "expenses_total": expenses_total,
        "net_profit": net_profit,
        "stock_value": stock_value,
        "total_products": Product.objects.count(),
        "total_clients": Client.objects.count(),
        "total_suppliers": Supplier.objects.count(),
        "purchases_total": purchases_total,
        "period_sales": period_revenue,
        "products_sold": quantity_sum(sale_lines_qs),
        "products_purchased": quantity_sum(purchase_lines_qs),
        "out_of_stock": out_of_stock,
        "low_stock": low_stock,
        "near_stockout": near_stockout,
        "unpaid_invoices": unpaid_invoices,
        "pending_supplier_payments": pending_supplier_payments,
        "important_expenses": important_expenses,
        "notification_count": notification_count,
        "comparisons": {
            "revenue": comparison_payload(period_revenue, previous_revenue),
            "gross_profit": comparison_payload(gross_profit, previous_gross),
            "net_profit": comparison_payload(net_profit, previous_net),
            "expenses": comparison_payload(expenses_total, previous_expenses),
        },
        "top_products": top_products,
        "top_clients": top_clients,
        "top_suppliers": top_suppliers,
        "profitable_products": profitable_products,
        "least_sold_products": least_sold_products,
        "expense_categories": expenses_qs.values("category__name").annotate(total=Sum("amount")).order_by("-total")[:10],
        "chart_data": chart_data,
        "chart_data_json": json.dumps(chart_data),
    }
