import openpyxl
from django.contrib import messages
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.accounts.permissions import manager_required
from apps.core.export_security import excel_safe_text, pdf_safe_text
from apps.core.pagination import paginate_queryset

from .forms import ExpenseCategoryForm, ExpenseForm
from .models import Expense


def filter_expenses(request):
    expenses = Expense.objects.select_related('category', 'supplier', 'created_by').all()
    query = request.GET.get('q', '').strip()
    category = request.GET.get('category', '').strip()
    period = request.GET.get('period', '').strip()
    today = timezone.localdate()

    if query:
        expenses = expenses.filter(description__icontains=query) | expenses.filter(number__icontains=query)
    if category:
        expenses = expenses.filter(category_id=category)
    if period == 'day':
        expenses = expenses.filter(date=today)
    elif period == 'month':
        expenses = expenses.filter(date__year=today.year, date__month=today.month)
    elif period == 'year':
        expenses = expenses.filter(date__year=today.year)

    return expenses.order_by('-date', '-pk')


@manager_required
def expense_list(request):
    expenses = filter_expenses(request)
    total_amount = expenses.aggregate(total=Sum('amount'))['total'] or 0
    page_obj, pagination_query = paginate_queryset(request, expenses)
    return render(
        request,
        'expenses/expense_list.html',
        {
            'expenses': page_obj,
            'page_obj': page_obj,
            'pagination_query': pagination_query,
            'total_amount': total_amount,
        },
    )


@manager_required
def expense_create(request):
    form = ExpenseForm(request.POST or None, request.FILES or None)
    if form.is_valid():
        expense = form.save(commit=False)
        expense.created_by = request.user
        expense.save()
        messages.success(request, _('Charge enregistrée avec succès.'))
        return redirect('expense_list')
    return render(request, 'expenses/expense_form.html', {'form': form, 'title': _('Nouvelle charge')})


@manager_required
def expense_update(request, pk):
    expense = get_object_or_404(Expense, pk=pk)
    form = ExpenseForm(request.POST or None, request.FILES or None, instance=expense)
    if form.is_valid():
        form.save()
        messages.success(request, _('Charge mise à jour.'))
        return redirect('expense_list')
    return render(request, 'expenses/expense_form.html', {'form': form, 'title': _('Modifier la charge')})


@manager_required
def expense_delete(request, pk):
    expense = get_object_or_404(Expense, pk=pk)
    if request.method == 'POST':
        expense.delete()
        messages.success(request, _('Charge supprimée.'))
        return redirect('expense_list')
    return render(request, 'expenses/expense_confirm_delete.html', {'expense': expense})


@manager_required
def expense_category_create(request):
    form = ExpenseCategoryForm(request.POST or None)
    if form.is_valid():
        form.save()
        messages.success(request, _('Catégorie ajoutée.'))
        return redirect('expense_list')
    return render(request, 'expenses/expense_category_form.html', {'form': form})


@manager_required
def expense_export_excel(request):
    expenses = filter_expenses(request)
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = str(_('Charges'))[:31]
    sheet.append([
        _('Numéro'),
        _('Date'),
        _('Catégorie'),
        _('Description'),
        _('Montant'),
        _('Paiement'),
        _('Fournisseur'),
        _('Utilisateur'),
        _('Observation'),
    ])
    for expense in expenses:
        sheet.append([
            excel_safe_text(expense.number),
            excel_safe_text(expense.date.isoformat()),
            excel_safe_text(expense.category.name),
            excel_safe_text(expense.description),
            float(expense.amount),
            excel_safe_text(expense.get_payment_method_display()),
            excel_safe_text(expense.supplier.name if expense.supplier else ''),
            excel_safe_text(expense.created_by.get_username()),
            excel_safe_text(expense.observation),
        ])
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename=charges.xlsx'
    workbook.save(response)
    return response


@manager_required
def expense_export_pdf(request):
    expenses = list(filter_expenses(request))
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename=charges.pdf'
    doc = SimpleDocTemplate(response, pagesize=A4, rightMargin=12 * mm, leftMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm)
    styles = getSampleStyleSheet()
    story = [Paragraph(_('Rapport des charges'), styles['Title']), Spacer(1, 8 * mm)]
    data = [[_('Numéro'), _('Date'), _('Catégorie'), _('Description'), _('Montant')]]
    for expense in expenses:
        data.append([
            expense.number,
            expense.date.strftime('%d/%m/%Y'),
            expense.category.name,
            Paragraph(pdf_safe_text(expense.description), styles['BodyText']),
            f"{expense.amount:.2f} {_('DZD')}",
        ])
    table = Table(data, colWidths=[30 * mm, 24 * mm, 34 * mm, 70 * mm, 28 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#163b2f')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#cbd5dc')),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    story.append(table)
    doc.build(story)
    return response


@manager_required
def expense_print(request):
    expenses = filter_expenses(request)
    total_amount = expenses.aggregate(total=Sum('amount'))['total'] or 0
    return render(request, 'expenses/expense_print.html', {'expenses': expenses, 'total_amount': total_amount})
