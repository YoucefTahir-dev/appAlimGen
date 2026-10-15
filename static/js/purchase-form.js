(function () {
    'use strict';
    const purchase = document.getElementById('purchase-form');
    if (!purchase) return;
    const lines = document.getElementById('purchase-lines');
    const template = document.getElementById('purchase-line-template');
    const total = purchase.querySelector('input[name$="-TOTAL_FORMS"]');
    const quickForm = document.getElementById('quick-product-form');
    const modalElement = document.getElementById('quick-product-modal');
    const status = document.getElementById('purchase-product-status');
    let targetRow = null;
    let pending = false;

    const money = value => `${new Intl.NumberFormat(document.documentElement.lang || 'fr', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    }).format(Number.isFinite(value) ? value : 0)} DZD`;

    function numberValue(input) {
        const value = Number.parseFloat(String(input?.value || '').replace(',', '.'));
        return Number.isFinite(value) ? value : 0;
    }

    function refreshRow(row) {
        const quantity = numberValue(row.querySelector('[name$="-quantity"]'));
        const price = numberValue(row.querySelector('[name$="-purchase_price"]'));
        const unit = row.querySelector('[name$="-packaging"]')?.selectedOptions[0];
        const factor = Number.parseInt(unit?.dataset.factor || '1', 10) || 1;
        const deleted = row.querySelector('[name$="-DELETE"]')?.checked;
        row.querySelector('.purchase-stock-quantity').textContent = deleted ? '—' : String(quantity * factor);
        row.querySelector('.purchase-line-total').textContent = money(deleted ? 0 : quantity * price);
    }

    function refreshTotals() {
        let subtotal = 0;
        lines.querySelectorAll('.purchase-line-row').forEach(row => {
            refreshRow(row);
            if (!row.querySelector('[name$="-DELETE"]')?.checked) {
                subtotal += numberValue(row.querySelector('[name$="-quantity"]'))
                    * numberValue(row.querySelector('[name$="-purchase_price"]'));
            }
        });
        const taxRate = numberValue(document.getElementById('id_tax_rate'));
        const tax = subtotal * taxRate / 100;
        document.getElementById('purchase-total-ht').textContent = money(subtotal);
        document.getElementById('purchase-tax-total').textContent = money(tax);
        document.getElementById('purchase-total-ttc').textContent = money(subtotal + tax);
    }

    purchase.addEventListener('input', refreshTotals);
    purchase.addEventListener('change', function (event) {
        if (event.target.matches('[name$="-packaging"]')) {
            const price = event.target.selectedOptions[0]?.dataset.purchasePrice;
            const priceInput = event.target.closest('tr').querySelector('[name$="-purchase_price"]');
            if (price !== undefined && price !== '') priceInput.value = price;
        }
        refreshTotals();
    });

    document.getElementById('add-purchase-line').addEventListener('click', function () {
        const index = Number.parseInt(total.value, 10);
        const maximum = Number.parseInt(purchase.querySelector('input[name$="-MAX_NUM_FORMS"]').value, 10);
        if (!Number.isInteger(index) || index >= maximum) return;
        lines.insertAdjacentHTML('beforeend', template.innerHTML.replace(/__prefix__/g, String(index)));
        total.value = String(index + 1);
        refreshTotals();
    });

    refreshTotals();

    if (!quickForm) return;
    function clearErrors() {
        quickForm.querySelectorAll('[data-errors]').forEach(element => { element.textContent = ''; });
        quickForm.querySelectorAll('[aria-invalid]').forEach(element => element.removeAttribute('aria-invalid'));
    }
    lines.addEventListener('click', function (event) {
        const trigger = event.target.closest('.quick-product-open');
        if (!trigger || pending) return;
        targetRow = trigger.closest('.purchase-line-row');
        quickForm.reset();
        const productSearch = targetRow.querySelector('.product-search');
        const quickName = quickForm.querySelector('input[name="quick-name"]');
        if (productSearch && quickName) quickName.value = productSearch.value.trim();
        clearErrors();
        status.textContent = '';
        if (!window.bootstrap || !window.bootstrap.Modal) {
            status.textContent = quickForm.dataset.error;
            return;
        }
        window.bootstrap.Modal.getOrCreateInstance(modalElement).show();
    });
    modalElement.addEventListener('shown.bs.modal', function () {
        if (!quickForm.contains(document.activeElement)) {
            quickForm.querySelector('input[name="quick-name"]').focus();
        }
    });
    modalElement.addEventListener('hide.bs.modal', function (event) {
        if (pending) event.preventDefault();
    });
    quickForm.addEventListener('submit', async function (event) {
        event.preventDefault();
        if (pending || !targetRow || !targetRow.isConnected) return;
        pending = true;
        clearErrors();
        const submit = quickForm.querySelector('[type="submit"]');
        submit.disabled = true;
        quickForm.setAttribute('aria-busy', 'true');
        try {
            const data = new FormData(quickForm);
            const response = await fetch(quickForm.action, {
                method: 'POST', body: data, credentials: 'same-origin',
                headers: {'Accept': 'application/json', 'X-CSRFToken': data.get('csrfmiddlewaretoken')},
            });
            const payload = await response.json();
            if (!response.ok || !payload.success) {
                if (!payload.errors) throw new Error('Creation failed');
                Object.entries(payload.errors).forEach(([field, errors]) => {
                    const container = Array.from(quickForm.querySelectorAll('[data-errors]'))
                        .find(element => element.dataset.errors === field)
                        || quickForm.querySelector('[data-errors="__all__"]');
                    container.textContent = errors.map(error => error.message).join(' ');
                    const input = quickForm.elements.namedItem(`quick-${field}`);
                    if (input) {
                        input.setAttribute('aria-invalid', 'true');
                        input.setAttribute('aria-describedby', container.id);
                    }
                });
                return;
            }
            const product = payload.product;
            window.ProductAutocomplete.select(targetRow.querySelector('.product-picker'), product);
            pending = false;
            window.bootstrap.Modal.getOrCreateInstance(modalElement).hide();
            status.textContent = quickForm.dataset.success;
        } catch (_error) {
            quickForm.querySelector('[data-errors="__all__"]').textContent = quickForm.dataset.error;
        } finally {
            pending = false;
            submit.disabled = false;
            quickForm.removeAttribute('aria-busy');
        }
    });
})();
