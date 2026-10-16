(function () {
    'use strict';

    function initializeSaleFormset() {
        const body = document.getElementById('sale-lines-body');
        const template = document.getElementById('sale-line-template');
        const addButton = document.getElementById('add-sale-line');
        const totalForms = document.getElementById('id_lines-TOTAL_FORMS');
        const saleForm = document.getElementById('sale-form');
        const clientSelect = document.getElementById('id_client');
        const settlementSelect = document.getElementById('id_settlement_action');
        const amountReceivedInput = document.getElementById('id_amount_received');
        const amountReceivedField = document.getElementById('sale-amount-received-field');

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

        const money = cents => `${new Intl.NumberFormat(document.documentElement.lang || 'fr', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        }).format((Number.isSafeInteger(cents) ? cents : 0) / 100)} DZD`;

        function decimalParts(value) {
            const normalized = String(value || '').trim().replace(',', '.');
            const match = normalized.match(/^(-?)(\d*)(?:\.(\d*))?$/);
            if (!match || (!match[2] && !match[3])) return null;
            return {negative: match[1] === '-', whole: match[2] || '0', fraction: match[3] || ''};
        }

        function moneyCents(value) {
            const parts = decimalParts(value);
            if (!parts) return 0;
            const digits = `${parts.fraction}000`;
            let cents = Number.parseInt(parts.whole, 10) * 100 + Number.parseInt(digits.slice(0, 2), 10);
            if (Number.parseInt(digits[2], 10) >= 5) cents += 1;
            return parts.negative ? -cents : cents;
        }

        function percentBasisPoints(value) {
            const parts = decimalParts(value);
            if (!parts) return 0;
            const digits = `${parts.fraction}000`;
            let basisPoints = Number.parseInt(parts.whole, 10) * 100 + Number.parseInt(digits.slice(0, 2), 10);
            if (Number.parseInt(digits[2], 10) >= 5) basisPoints += 1;
            return parts.negative ? -basisPoints : basisPoints;
        }

        function inputValue(input) {
            return input?.value || '';
        }

        function setMoney(element, cents) {
            if (!element) return;
            element.textContent = money(cents);
            element.dataset.cents = String(cents);
        }

        function refreshTotals() {
            let subtotalCents = 0;
            body.querySelectorAll('.sale-line-row').forEach(function (row) {
                const quantity = Number.parseInt(inputValue(row.querySelector('[name$="-quantity"]')), 10) || 0;
                const priceCents = moneyCents(inputValue(row.querySelector('[name$="-unit_price"]')));
                const unit = row.querySelector('[name$="-packaging"]')?.selectedOptions[0];
                const factor = Number.parseInt(unit?.dataset.factor || '1', 10) || 1;
                const deleted = row.querySelector('[name$="-DELETE"]')?.checked;
                const stockQuantity = row.querySelector('.sale-stock-quantity');
                const lineTotal = row.querySelector('.sale-line-total');
                const lineTotalCents = deleted ? 0 : quantity * priceCents;
                if (stockQuantity) stockQuantity.textContent = deleted ? '—' : String(quantity * factor);
                setMoney(lineTotal, lineTotalCents);
                subtotalCents += lineTotalCents;
            });
            const taxRate = percentBasisPoints(inputValue(document.getElementById('id_tax_rate')));
            const discountCents = moneyCents(inputValue(document.getElementById('id_discount')));
            const taxCents = Math.round(subtotalCents * taxRate / 10000);
            const totalCents = subtotalCents + taxCents - discountCents;
            const existingPaidCents = moneyCents(saleForm?.dataset.existingPaid || '0');
            const newPaymentCents = settlementSelect?.value === 'pay_full'
                ? Math.max(totalCents - existingPaidCents, 0)
                : (settlementSelect?.value === 'pay_partial' ? moneyCents(inputValue(amountReceivedInput)) : 0);
            const paidCents = existingPaidCents + newPaymentCents;
            const dueCents = Math.max(totalCents - paidCents, 0);
            setMoney(document.getElementById('sale-total-ht'), subtotalCents);
            setMoney(document.getElementById('sale-tax-total'), taxCents);
            setMoney(document.getElementById('sale-discount-total'), discountCents);
            setMoney(document.getElementById('sale-total-ttc'), totalCents);
            setMoney(document.getElementById('sale-paid-total'), paidCents);
            setMoney(document.getElementById('sale-due-total'), dueCents);
            if (amountReceivedField) amountReceivedField.hidden = settlementSelect?.value !== 'pay_partial';
            if (amountReceivedInput) amountReceivedInput.required = settlementSelect?.value === 'pay_partial';
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

        if (settlementSelect) {
            settlementSelect.addEventListener('change', refreshTotals);
        }

        saleForm.addEventListener('input', function (event) {
            if (event.target.matches('input[name$="-unit_price"]')) {
                event.target.closest('.sale-line-row').dataset.priceRequest = '';
                event.target.removeAttribute('aria-busy');
            }
            refreshTotals();
        });

        // A rapid double click must not create the sale (and its initial
        // payment) twice.  Native constraint validation runs before this
        // event, so the guard is armed only for a genuinely submitted form.
        let submissionStarted = false;
        saleForm.addEventListener('submit', function (event) {
            if (submissionStarted) {
                event.preventDefault();
                return;
            }
            submissionStarted = true;
            saleForm.querySelectorAll('button[type="submit"], input[type="submit"]').forEach(function (button) {
                button.disabled = true;
            });
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
