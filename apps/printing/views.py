from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from apps.accounts.permissions import permission_required

from .forms import PrinterProfileForm
from .models import PrinterProfile
from .services import printer_test_payload


@permission_required('printing.view_printerprofile')
def printer_list(request):
    return render(request, 'printing/printer_list.html', {'printers': PrinterProfile.objects.all()})


@permission_required('printing.add_printerprofile')
def printer_create(request):
    form = PrinterProfileForm(request.POST or None)
    if form.is_valid():
        form.save()
        messages.success(request, _('Imprimante ajoutée.'))
        return redirect('printer_list')
    return render(request, 'printing/printer_form.html', {'form': form, 'title': _('Ajouter une imprimante')})


@permission_required('printing.change_printerprofile')
def printer_update(request, pk):
    printer = get_object_or_404(PrinterProfile, pk=pk)
    form = PrinterProfileForm(request.POST or None, instance=printer)
    if form.is_valid():
        form.save()
        messages.success(request, _('Imprimante mise à jour.'))
        return redirect('printer_list')
    return render(request, 'printing/printer_form.html', {'form': form, 'title': _('Modifier l’imprimante')})


@permission_required('printing.delete_printerprofile')
def printer_delete(request, pk):
    printer = get_object_or_404(PrinterProfile, pk=pk)
    if request.method == 'POST':
        printer.delete()
        messages.success(request, _('Imprimante supprimée.'))
        return redirect('printer_list')
    return render(request, 'printing/printer_confirm_delete.html', {'printer': printer})


@permission_required('printing.test_printerprofile')
def printer_test(request, pk):
    printer = get_object_or_404(PrinterProfile, pk=pk, is_active=True)
    result = printer_test_payload(printer)
    response = HttpResponse(result.payload, content_type='application/octet-stream')
    response['Content-Disposition'] = f'attachment; filename="printer-test-{printer.pk}.bin"'
    response['X-Printer-Protocol'] = result.protocol
    response['X-Arabic-Raster-Recommendation'] = 'true' if result.raster_arabic_recommended else 'false'
    return response
