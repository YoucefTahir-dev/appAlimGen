(function () {
    'use strict';

    function initializeSaleFormset() {
        const body = document.getElementById('sale-lines-body');
        const template = document.getElementById('sale-line-template');
        const addButton = document.getElementById('add-sale-line');
        const totalForms = document.getElementById('id_lines-TOTAL_FORMS');
        const saleForm = document.getElementById('sale-form');
        const clientSelect = document.getElementById('id_client');

        if (!body || !template || !addButton || !totalForms) {
            return;
        }

        const autoPricing = saleForm && saleForm.dataset.autoPricing === 'true';
        const priceUrl = saleForm ? saleForm.dataset.priceUrl : '';

        async function updateRowPrice(row) {
            if (!autoPricing || !priceUrl || !clientSelect || !clientSelect.value) {
                return;
            }
            const productSelect = row.querySelector('select[name$="-product"]');
            const packagingSelect = row.querySelector('select[name$="-packaging"]');
            const priceInput = row.querySelector('input[name$="-unit_price"]');
            if (!productSelect || !productSelect.value || !priceInput) {
                return;
            }
            const requestKey = `${clientSelect.value}:${productSelect.value}`;
            row.dataset.priceRequest = requestKey;
            const query = new URLSearchParams({
                client_id: clientSelect.value,
                product_id: productSelect.value,
            });
            if (packagingSelect && packagingSelect.value) {
                query.set('packaging_id', packagingSelect.value);
            }
            try {
                const response = await fetch(`${priceUrl}?${query.toString()}`, {
                    headers: {'Accept': 'application/json'},
                    credentials: 'same-origin',
                });
                if (!response.ok) {
                    return;
                }
                const payload = await response.json();
                if (row.dataset.priceRequest === requestKey) {
                    priceInput.value = payload.price;
                }
            } catch (_error) {
                // The price remains manually editable if the lookup is unavailable.
            }
        }

        function bindRemoveButton(row) {
            const removeButton = row.querySelector('.remove-sale-line');
            if (!removeButton || removeButton.dataset.bound === 'true') {
                return;
            }
            removeButton.dataset.bound = 'true';
            removeButton.addEventListener('click', function () {
                const deleteInput = row.querySelector('input[name$="-DELETE"]');
                if (deleteInput) {
                    deleteInput.checked = true;
                    row.hidden = true;
                } else {
                    row.remove();
                }
            });
            const productSelect = row.querySelector('select[name$="-product"]');
            if (productSelect && productSelect.dataset.pricingBound !== 'true') {
                productSelect.dataset.pricingBound = 'true';
                productSelect.addEventListener('change', function () {
                    updateRowPrice(row);
                });
            }
            const packagingSelect = row.querySelector('select[name$="-packaging"]');
            if (packagingSelect && packagingSelect.dataset.pricingBound !== 'true') {
                packagingSelect.dataset.pricingBound = 'true';
                packagingSelect.addEventListener('change', function () {
                    updateRowPrice(row);
                });
            }
        }

        body.querySelectorAll('.sale-line-row').forEach(bindRemoveButton);

        if (clientSelect && autoPricing) {
            clientSelect.addEventListener('change', function () {
                body.querySelectorAll('.sale-line-row:not([hidden])').forEach(updateRowPrice);
            });
        }

        addButton.addEventListener('click', function () {
            const index = Number.parseInt(totalForms.value, 10);
            if (!Number.isInteger(index)) {
                return;
            }
            const markup = template.innerHTML.replace(/__prefix__/g, String(index));
            body.insertAdjacentHTML('beforeend', markup);
            totalForms.value = String(index + 1);
            bindRemoveButton(body.lastElementChild);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeSaleFormset);
    } else {
        initializeSaleFormset();
    }
})();
