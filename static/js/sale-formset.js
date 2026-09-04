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

        function setFeedback(row, message, isError) {
            const feedback = row.querySelector('.sale-price-feedback');
            if (!feedback) {
                return;
            }
            feedback.textContent = message || '';
            feedback.classList.toggle('text-danger', Boolean(isError));
        }

        function updatePackagingOptions(select, packagings, selectedValue) {
            if (!select) {
                return;
            }
            const fragment = document.createDocumentFragment();
            const baseOption = document.createElement('option');
            baseOption.value = '';
            baseOption.textContent = labels.baseUnit;
            fragment.appendChild(baseOption);
            packagings.forEach(function (packaging) {
                const option = document.createElement('option');
                option.value = String(packaging.id);
                option.textContent = `${packaging.name} (×${packaging.conversion_factor})`;
                fragment.appendChild(option);
            });
            select.replaceChildren(fragment);
            select.value = selectedValue || '';
            select.disabled = false;
        }

        async function updateRowPrice(row) {
            if (!autoPricing || !priceUrl || !clientSelect) {
                return;
            }
            const productSelect = row.querySelector('select[name$="-product"]');
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
                    priceInput.value = payload.price;
                    updatePackagingOptions(packagingSelect, payload.packagings || [], packagingValue);
                    if (clientType) {
                        clientType.textContent = payload.customer_type_label || '';
                    }
                    if (productMeta) {
                        productMeta.textContent = `${payload.reference} · Stock: ${payload.stock}`;
                    }
                    setFeedback(row, '', false);
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
            if (event.target.matches('select[name$="-product"]')) {
                const packagingSelect = row.querySelector('select[name$="-packaging"]');
                if (packagingSelect) {
                    packagingSelect.value = '';
                }
                updateRowPrice(row);
            } else if (event.target.matches('select[name$="-packaging"]')) {
                updateRowPrice(row);
            }
        });

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
            const packagingSelect = body.lastElementChild.querySelector('select[name$="-packaging"]');
            if (packagingSelect) {
                updatePackagingOptions(packagingSelect, [], '');
                packagingSelect.disabled = true;
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeSaleFormset);
    } else {
        initializeSaleFormset();
    }
})();
