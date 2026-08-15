import calendar
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce, ExtractMonth, ExtractYear, TruncDay, TruncMonth
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.commerce.models import Purchase, PurchaseLine, Sale, SaleLine
from apps.expenses.models import Expense
from apps.inventory.models import Client, Product, Supplier

from .forms import DashboardPeriodForm, PERIOD_CHOICES


ZERO = Decimal("0.00")
IMPORTANT_EXPENSE_THRESHOLD = Decimal("100000.00")


class DashboardPeriodError(ValueError):
    def __init__(self, messages):
        self.messages = messages
        super().__init__(" ".join(messages))


def money_value(value):
    return value or ZERO


def local_day_bounds(day):
    """Return a timezone-aware, half-open interval for one local calendar day."""
    current_tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time.min), current_tz)
    next_day = timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min), current_tz)
    return start, next_day


def _previous_month_start(day):
    if day.month == 1:
        return date(day.year - 1, 12, 1)
    return date(day.year, day.month - 1, 1)


def _previous_period_dates(period, start_date, end_date):
    if period == "week":
        return start_date - timedelta(days=7), end_date - timedelta(days=7)

    if period == "month":
        previous_start = _previous_month_start(start_date)
        previous_month_end = previous_start.replace(
            day=calendar.monthrange(previous_start.year, previous_start.month)[1]
        )
        elapsed_days = (end_date - start_date).days
        return previous_start, min(previous_start + timedelta(days=elapsed_days), previous_month_end)

    if period == "year":
        previous_start = date(start_date.year - 1, 1, 1)
        try:
            previous_end = end_date.replace(year=end_date.year - 1)
        except ValueError:  # 29 February compared with a non-leap year.
            previous_end = end_date.replace(year=end_date.year - 1, day=28)
        return previous_start, previous_end

    period_days = (end_date - start_date).days + 1
    previous_end = start_date - timedelta(days=1)
    return previous_end - timedelta(days=period_days - 1), previous_end


def _form_error_messages(form):
    return [str(message) for errors in form.errors.values() for message in errors]


def get_period_bounds(request, strict=False):
    today = timezone.localdate()
    form_data = request.GET if request.GET else {"period": "today"}
    period_form = DashboardPeriodForm(form_data)
    period_is_valid = period_form.is_valid()

    if not period_is_valid:
        period_errors = _form_error_messages(period_form)
        if strict:
            raise DashboardPeriodError(period_errors)
        period = "today"
        start_date = end_date = today
    else:
        period_errors = []
        period = period_form.cleaned_data["period"]
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
            start_date = period_form.cleaned_data["start_date"]
            end_date = period_form.cleaned_data["end_date"]
        else:
            start_date = end_date = today

    start_dt, _ = local_day_bounds(start_date)
    _, end_dt = local_day_bounds(end_date)
    previous_start_date, previous_end_date = _previous_period_dates(period, start_date, end_date)
    previous_start_dt, _ = local_day_bounds(previous_start_date)
    _, previous_end_dt = local_day_bounds(previous_end_date)

    export_params = {"period": period}
    if period == "custom":
        export_params.update({"start_date": start_date.isoformat(), "end_date": end_date.isoformat()})

    return {
        "period": period,
        "period_form": period_form,
        "period_is_valid": period_is_valid,
        "period_errors": period_errors,
        "export_querystring": urlencode(export_params),
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
        if current > 0:
            return Decimal("100.00")
        if current < 0:
            return Decimal("-100.00")
        return Decimal("0.00")
    return ((current - previous) / abs(previous) * Decimal("100")).quantize(Decimal("0.01"))


def comparison_payload(current, previous):
    current_value = current or 0
    previous_value = previous or 0
    return {
        "current": current_value,
        "previous": previous_value,
        "percent": percent_change(current_value, previous_value),
        "direction": "up" if current_value > previous_value else "down" if current_value < previous_value else "flat",
    }


def datetime_window(queryset, field_name, start_dt, end_dt):
    return queryset.filter(
        **{
            f"{field_name}__gte": start_dt,
            f"{field_name}__lt": end_dt,
        }
    )


def sales_total_between(start_dt, end_dt):
    queryset = datetime_window(Sale.objects.all(), "created_at", start_dt, end_dt)
    return money_value(queryset.aggregate(total=Sum("total"))["total"])


def purchases_total_between(start_dt, end_dt):
    queryset = datetime_window(Purchase.objects.all(), "created_at", start_dt, end_dt)
    return money_value(queryset.aggregate(total=Sum("total"))["total"])


def expenses_total_between(start_date, end_date):
    return money_value(Expense.objects.filter(date__range=(start_date, end_date)).aggregate(total=Sum("amount"))["total"])


def _cost_expression():
    return ExpressionWrapper(
        F("quantity") * F("unit_cost"),
        output_field=DecimalField(max_digits=18, decimal_places=2),
    )


def cogs_between(start_dt, end_dt):
    queryset = datetime_window(SaleLine.objects.all(), "sale__created_at", start_dt, end_dt)
    return money_value(queryset.aggregate(total=Sum(_cost_expression()))["total"])


def quantity_sum(queryset):
    return queryset.aggregate(total=Sum("quantity"))["total"] or 0


def outstanding_count(model, start_dt, end_dt):
    queryset = datetime_window(model.objects.all(), "created_at", start_dt, end_dt)
    if hasattr(model, 'payment_tracking_initialized'):
        queryset = queryset.filter(payment_tracking_initialized=True)
    return (
        queryset.annotate(
            paid=Coalesce(
                Sum("payments__amount"),
                Value(ZERO),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        )
        .filter(total__gt=F("paid"))
        .count()
    )


def trend_granularity(start_date, end_date):
    return "month" if (end_date - start_date).days > 90 else "day"


def bucket_key(value):
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.date().isoformat()
    return value.isoformat()


def chart_labels(start_date, end_date, granularity):
    labels = []
    cursor = start_date
    if granularity == "day":
        while cursor <= end_date:
            labels.append(cursor.isoformat())
            cursor += timedelta(days=1)
        return labels

    cursor = cursor.replace(day=1)
    final_month = end_date.replace(day=1)
    while cursor <= final_month:
        labels.append(cursor.isoformat())
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return labels


def series_from_queryset(queryset, granularity, total_field="total", date_field="created_at"):
    truncation = TruncMonth if granularity == "month" else TruncDay
    rows = (
        queryset.annotate(
            bucket=truncation(date_field, tzinfo=timezone.get_current_timezone())
        )
        .values("bucket")
        .annotate(total=Sum(total_field))
        .order_by("bucket")
    )
    return {bucket_key(row["bucket"]): float(row["total"] or 0) for row in rows}


def expense_series(start_date, end_date, granularity):
    queryset = Expense.objects.filter(date__range=(start_date, end_date))
    if granularity == "day":
        rows = queryset.values("date").annotate(total=Sum("amount")).order_by("date")
        return {row["date"].isoformat(): float(row["total"] or 0) for row in rows}

    rows = (
        queryset.annotate(bucket_year=ExtractYear("date"), bucket_month=ExtractMonth("date"))
        .values("bucket_year", "bucket_month")
        .annotate(total=Sum("amount"))
        .order_by("bucket_year", "bucket_month")
    )
    return {
        date(row["bucket_year"], row["bucket_month"], 1).isoformat(): float(row["total"] or 0)
        for row in rows
    }


def build_chart_data(bounds, expense_category_rows):
    granularity = trend_granularity(bounds["start_date"], bounds["end_date"])
    sales_qs = datetime_window(Sale.objects.all(), "created_at", bounds["start_dt"], bounds["end_dt"])
    purchases_qs = datetime_window(Purchase.objects.all(), "created_at", bounds["start_dt"], bounds["end_dt"])
    sold_lines_qs = datetime_window(
        SaleLine.objects.all(), "sale__created_at", bounds["start_dt"], bounds["end_dt"]
    ).annotate(line_cost=_cost_expression())

    sales = series_from_queryset(sales_qs, granularity)
    purchases = series_from_queryset(purchases_qs, granularity)
    sold_costs = series_from_queryset(sold_lines_qs, granularity, "line_cost", "sale__created_at")
    expenses = expense_series(bounds["start_date"], bounds["end_date"], granularity)
    labels = chart_labels(bounds["start_date"], bounds["end_date"], granularity)
    revenue_values = [sales.get(label, 0) for label in labels]
    purchase_values = [purchases.get(label, 0) for label in labels]
    sold_cost_values = [sold_costs.get(label, 0) for label in labels]
    expense_values = [expenses.get(label, 0) for label in labels]
    gross_values = [revenue_values[index] - sold_cost_values[index] for index in range(len(labels))]
    net_values = [gross_values[index] - expense_values[index] for index in range(len(labels))]

    sale_lines_with_totals = datetime_window(
        SaleLine.objects.all(), "sale__created_at", bounds["start_dt"], bounds["end_dt"]
    ).annotate(
        line_total=ExpressionWrapper(
            F("quantity") * F("unit_price"),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        )
    )
    sales_by_category = list(
        sale_lines_with_totals.values("product__category__name")
        .annotate(total=Sum("line_total"))
        .order_by("-total", "product__category__name")[:10]
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
            "labels": [row["category__name"] or _("Sans catégorie") for row in expense_category_rows],
            "values": [float(row["total"] or 0) for row in expense_category_rows],
        },
    }


def dashboard_context(request, strict_period=False):
    bounds = get_period_bounds(request, strict=strict_period)
    today = timezone.localdate()
    today_start, today_end = local_day_bounds(today)

    sales_qs = datetime_window(Sale.objects.all(), "created_at", bounds["start_dt"], bounds["end_dt"])
    purchases_qs = datetime_window(Purchase.objects.all(), "created_at", bounds["start_dt"], bounds["end_dt"])
    sale_lines_qs = datetime_window(
        SaleLine.objects.all(), "sale__created_at", bounds["start_dt"], bounds["end_dt"]
    ).annotate(
        line_total=ExpressionWrapper(
            F("quantity") * F("unit_price"),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        ),
        line_cost=_cost_expression(),
        line_profit=ExpressionWrapper(
            F("quantity") * (F("unit_price") - F("unit_cost")),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        ),
    )
    purchase_lines_qs = datetime_window(
        PurchaseLine.objects.all(), "purchase__created_at", bounds["start_dt"], bounds["end_dt"]
    )
    expenses_qs = Expense.objects.filter(date__range=(bounds["start_date"], bounds["end_date"]))

    sales_metrics = sales_qs.aggregate(total=Sum("total"), count=Count("pk"))
    period_revenue = money_value(sales_metrics["total"])
    sales_count = sales_metrics["count"]
    purchases_total = money_value(purchases_qs.aggregate(total=Sum("total"))["total"])
    line_metrics = sale_lines_qs.aggregate(products_sold=Sum("quantity"), cogs=Sum("line_cost"))
    products_sold = line_metrics["products_sold"] or 0
    products_purchased = quantity_sum(purchase_lines_qs)
    expense_metrics = expenses_qs.aggregate(
        total=Sum("amount"),
        important_count=Count("pk", filter=Q(amount__gte=IMPORTANT_EXPENSE_THRESHOLD)),
    )
    expenses_total = money_value(expense_metrics["total"])
    important_expenses = expense_metrics["important_count"]
    gross_profit = period_revenue - money_value(line_metrics["cogs"])
    net_profit = gross_profit - expenses_total

    previous_revenue = sales_total_between(bounds["previous_start_dt"], bounds["previous_end_dt"])
    previous_gross = previous_revenue - cogs_between(bounds["previous_start_dt"], bounds["previous_end_dt"])
    previous_expenses = expenses_total_between(bounds["previous_start_date"], bounds["previous_end_date"])
    previous_net = previous_gross - previous_expenses

    stock_expression = ExpressionWrapper(
        F("purchase_price") * F("quantity"),
        output_field=DecimalField(max_digits=18, decimal_places=2),
    )
    inventory_snapshot = Product.objects.aggregate(
        stock_value=Sum(stock_expression),
        total_products=Count("pk"),
        out_of_stock=Count("pk", filter=Q(quantity=0)),
        low_stock=Count("pk", filter=Q(quantity__gt=0, quantity__lte=F("minimum_stock"))),
        near_stockout=Count(
            "pk",
            filter=Q(quantity__gt=F("minimum_stock"), quantity__lte=F("minimum_stock") + 5),
        ),
    )
    out_of_stock = inventory_snapshot["out_of_stock"]
    low_stock = inventory_snapshot["low_stock"]
    near_stockout = inventory_snapshot["near_stockout"]
    unpaid_invoices = outstanding_count(Sale, bounds["start_dt"], bounds["end_dt"])
    pending_supplier_payments = outstanding_count(Purchase, bounds["start_dt"], bounds["end_dt"])
    notification_count = (
        out_of_stock
        + low_stock
        + near_stockout
        + unpaid_invoices
        + pending_supplier_payments
        + important_expenses
    )

    top_products = list(
        sale_lines_qs.values("product__name")
        .annotate(quantity=Sum("quantity"), total=Sum("line_total"))
        .order_by("-quantity", "product__name")[:10]
    )
    top_clients = list(
        sales_qs.values("client__name")
        .annotate(total=Sum("total"), count=Count("pk"))
        .order_by("-total", "client__name")[:10]
    )
    top_suppliers = list(
        purchases_qs.values("supplier__name")
        .annotate(total=Sum("total"), count=Count("pk"))
        .order_by("-total", "supplier__name")[:10]
    )
    profitable_products = list(
        sale_lines_qs.values("product__name")
        .annotate(profit=Sum("line_profit"), quantity=Sum("quantity"))
        .order_by("-profit", "product__name")[:10]
    )
    least_sold_products = list(
        Product.objects.annotate(
            sold_quantity=Coalesce(
                Sum(
                    "saleline__quantity",
                    filter=Q(
                        saleline__sale__created_at__gte=bounds["start_dt"],
                        saleline__sale__created_at__lt=bounds["end_dt"],
                    ),
                ),
                Value(0),
            )
        )
        .order_by("sold_quantity", "name")[:10]
    )
    expense_category_rows = list(
        expenses_qs.values("category__name")
        .annotate(total=Sum("amount"))
        .order_by("-total", "category__name")[:10]
    )
    chart_data = build_chart_data(bounds, expense_category_rows)

    sales_today = (
        period_revenue
        if bounds["start_date"] == today and bounds["end_date"] == today
        else sales_total_between(today_start, today_end)
    )

    return {
        **bounds,
        "period_options": PERIOD_CHOICES,
        "sales_today": sales_today,
        "period_revenue": period_revenue,
        "sales_count": sales_count,
        "average_basket": period_revenue / sales_count if sales_count else ZERO,
        "gross_profit": gross_profit,
        "expenses_total": expenses_total,
        "net_profit": net_profit,
        "stock_value": money_value(inventory_snapshot["stock_value"]),
        "total_products": inventory_snapshot["total_products"],
        "total_clients": Client.objects.count(),
        "total_suppliers": Supplier.objects.count(),
        "purchases_total": purchases_total,
        "period_sales": period_revenue,
        "products_sold": products_sold,
        "products_purchased": products_purchased,
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
        "expense_categories": expense_category_rows,
        "chart_data": chart_data,
    }
