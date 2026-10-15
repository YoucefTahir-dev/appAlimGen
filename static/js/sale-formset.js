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

        const clientType = document.getElementById('sale-client-type');
        const labels = {
            loading: saleForm ? saleForm.dataset.loadingLabel : 'Chargement…',
            error: saleForm ? saleForm.dataset.priceErrorLabel : 'Impossible de récupérer le prix du produit.',
            selectClient: saleForm ? saleForm.dataset.selectClientLabel : 'Sélectionnez d’abord un client.',
            baseUnit: saleForm ? saleForm.dataset.baseUnitLabel : 'Unité de base',
        };
        let requestSequence = 0;

        const money = value => `${new Intl.NumberFormat(document.documentElement.lang || 'fr', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        }).format(Number.isFinite(value) ? value : 0)} DZD`;

        function numberValue(input) {
            const value = Number.parseFloat(String(input?.value || '').replace(',', '.'));
            return Number.isFinite(value) ? value : 0;
        }

        function refreshTotals() {
            let subtotal = 0;
            body.querySelectorAll('.sale-line-row').forEach(function (row) {
                const quantity = numberValue(row.querySelector('[name$="-quantity"]'));
                const price = numberValue(row.querySelector('[name$="-unit_price"]'));
                const unit = row.querySelector('[name$="-packaging"]')?.selectedOptions[0];
                const factor = Number.parseInt(unit?.dataset.factor || '1', 10) || 1;
                const deleted = row.querySelector('[name$="-DELETE"]')?.checked;
                const stockQuantity = row.querySelector('.sale-stock-quantity');
                const lineTotal = row.querySelector('.sale-line-total');
                if (stockQuantity) stockQuantity.textContent = deleted ? '—' : String(quantity * factor);
                if (lineTotal) lineTotal.textContent = money(deleted ? 0 : quantity * price);
                if (!deleted) subtotal += quantity * price;
            });
            const taxRate = numberValue(document.getElementById('id_tax_rate'));
            const discount = numberValue(document.getElementById('id_discount'));
            const tax = subtotal * taxRate / 100;
            document.getElementById('sale-total-ht').textContent = money(subtotal);
            document.getElementById('sale-tax-total').textContent = money(tax);
            document.getElementById('sale-discount-total').textContent = money(discount);
            document.getElementById('sale-total-ttc').textContent = money(subtotal + tax - discount);
        }

        function setFeedback(row, message, isError) {
            const feedback = row.querySelector('.sale-price-feedback');
            if (!feedback) {
                return;
            }
            feedback.textContent = message || '';
            feedback.classList.toggle('text-danger', Boolean(isError));
        }

        function updatePackagingOptions(select, packagings, selectedValue, baseAllowed = true) {
            if (!select) {
                return;
            }
            const fragment = document.createDocumentFragment();
            if (baseAllowed) {
                const baseOption = document.createElement('option');
                baseOption.value = '';
                baseOption.textContent = labels.baseUnit;
                baseOption.dataset.factor = '1';
                fragment.appendChild(baseOption);
            }
            packagings.forEach(function (packaging) {
                const option = document.createElement('option');
                option.value = String(packaging.id);
                option.textContent = `${packaging.name} (×${packaging.conversion_factor})`;
                option.dataset.factor = String(packaging.conversion_factor);
                fragment.appendChild(option);
            });
            select.replaceChildren(fragment);
            select.value = selectedValue || '';
            if (!baseAllowed && select.selectedIndex < 0 && select.options.length) {
                select.selectedIndex = 0;
            }
            select.disabled = false;
            return select.value;
        }

        async function updateRowPrice(row) {
            if (!autoPricing || !priceUrl || !clientSelect) {
                return;
            }
            const productSelect = row.querySelector('.product-id');
            const packagingSelect = row.querySelector('select[name$="-packaging"]');
            const priceInput = row.querySelector('input[name$="-unit_price"]');
            const productMeta = row.querySelector('.sale-product-meta');
            if (!productSelect || !priceInput) {
                return;
            }
            if (!productSelect.value) {
                row.dataset.priceRequest = '';
                priceInput.value = '';
                if (packagingSelect) {
                    updatePackagingOptions(packagingSelect, [], '');
                    packagingSelect.disabled = true;
                }
                if (productMeta) {
                    productMeta.textContent = '';
                }
                setFeedback(row, '', false);
                return;
            }
            if (!clientSelect.value) {
                row.dataset.priceRequest = '';
                priceInput.value = '';
                setFeedback(row, labels.selectClient, true);
                return;
            }
            const packagingValue = packagingSelect ? packagingSelect.value : '';
            const requestKey = `${clientSelect.value}:${productSelect.value}:${packagingValue}:${++requestSequence}`;
            row.dataset.priceRequest = requestKey;
            priceInput.value = '';
            const query = new URLSearchParams({
                client_id: clientSelect.value,
                product_id: productSelect.value,
            });
            if (packagingValue) {
                query.set('packaging_id', packagingValue);
            }
            priceInput.setAttribute('aria-busy', 'true');
            setFeedback(row, labels.loading, false);
            try {
                const response = await fetch(`${priceUrl}?${query.toString()}`, {
                    headers: {'Accept': 'application/json'},
                    credentials: 'same-origin',
                });
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                const payload = await response.json();
                if (row.dataset.priceRequest === requestKey) {
                    const selectedPackaging = updatePackagingOptions(
                        packagingSelect,
                        payload.packagings || [],
                        packagingValue,
                        payload.base_unit_allowed !== false,
                    );
                    const selectedUnit = (payload.packagings || []).find(
                        item => String(item.id) === String(selectedPackaging),
                    );
                    priceInput.value = selectedUnit && !packagingValue
                        ? selectedUnit.price
                        : payload.price;
                    if (clientType) {
                        clientType.textContent = payload.customer_type_label || '';
                    }
                    if (productMeta) {
                        productMeta.textContent = `${payload.reference} · ${saleForm.dataset.stockLabel}: ${payload.stock}`;
                    }
                    setFeedback(row, '', false);
                    refreshTotals();
                }
            } catch (_error) {
                if (row.dataset.priceRequest === requestKey) {
                    setFeedback(row, labels.error, true);
                }
            } finally {
                if (row.dataset.priceRequest === requestKey) {
                    priceInput.removeAttribute('aria-busy');
                }
            }
        }

        body.addEventListener('click', function (event) {
            const removeButton = event.target.closest('.remove-sale-line');
            if (removeButton) {
                const row = removeButton.closest('.sale-line-row');
                const deleteInput = row.querySelector('input[name$="-DELETE"]');
                if (deleteInput) {
                    deleteInput.checked = true;
                    row.hidden = true;
                    row.dataset.priceRequest = '';
                    row.querySelector('.product-search').setCustomValidity('');
                    window.ProductAutocomplete.close(row.querySelector('.product-picker'));
                    refreshTotals();
                } else {
                    row.remove();
                }
            }
        });

        body.addEventListener('change', function (event) {
            const row = event.target.closest('.sale-line-row');
            if (!row) {
                return;
            }
            if (event.target.matches('.product-id')) {
                const packagingSelect = row.querySelector('select[name$="-packaging"]');
                if (packagingSelect) {
                    updatePackagingOptions(packagingSelect, [], '');
                }
                updateRowPrice(row);
            } else if (event.target.matches('select[name$="-packaging"]')) {
                updateRowPrice(row);
            }
            refreshTotals();
        });

        if (clientSelect && autoPricing) {
            clientSelect.addEventListener('change', function () {
                body.querySelectorAll('.sale-line-row:not([hidden])').forEach(updateRowPrice);
            });
        }

        saleForm.addEventListener('input', function (event) {
            if (event.target.matches('input[name$="-unit_price"]')) {
                event.target.closest('.sale-line-row').dataset.priceRequest = '';
                event.target.removeAttribute('aria-busy');
            }
            refreshTotals();
        });

        addButton.addEventListener('click', function () {
            const index = Number.parseInt(totalForms.value, 10);
            if (!Number.isInteger(index)) {
                return;
            }
            const markup = template.innerHTML.replace(/__prefix__/g, String(index));
            body.insertAdjacentHTML('beforeend', markup);
            totalForms.value = String(index + 1);
            const packagingSelect = body.lastElementChild.querySelector('select[name$="-packaging"]');
            if (packagingSelect) {
                updatePackagingOptions(packagingSelect, [], '');
                packagingSelect.disabled = true;
            }
            refreshTotals();
        });

        refreshTotals();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeSaleFormset);
    } else {
        initializeSaleFormset();
    }
})();
