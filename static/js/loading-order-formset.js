(function () {
    'use strict';

    function initializeLoadingOrderFormset() {
        const body = document.getElementById('loading-lines-body');
        const template = document.getElementById('loading-line-template');
        const addButton = document.getElementById('add-loading-line');
        const totalForms = document.getElementById('id_lines-TOTAL_FORMS');
        if (!body || !template || !addButton || !totalForms) return;

        function visibleRows() {
            return Array.from(body.querySelectorAll('.loading-line-row:not([hidden])'));
        }

        addButton.addEventListener('click', function () {
            const index = Number.parseInt(totalForms.value, 10);
            const maxInput = document.getElementById('id_lines-MAX_NUM_FORMS');
            const maximum = Number.parseInt(maxInput ? maxInput.value : '1000', 10);
            if (!Number.isInteger(index) || index >= maximum) return;
            body.insertAdjacentHTML('beforeend', template.innerHTML.replace(/__prefix__/g, String(index)));
            totalForms.value = String(index + 1);
            const input = body.lastElementChild.querySelector('.product-search');
            if (input) input.focus();
        });

        body.addEventListener('click', function (event) {
            const button = event.target.closest('.remove-loading-line');
            if (!button) return;
            const row = button.closest('.loading-line-row');
            const deleteInput = row.querySelector('input[name$="-DELETE"]');
            if (deleteInput) deleteInput.checked = true;
            row.hidden = true;
            const input = row.querySelector('.product-search');
            if (input) input.setCustomValidity('');
            const picker = row.querySelector('.product-picker');
            if (picker && window.ProductAutocomplete) window.ProductAutocomplete.close(picker);
            if (!visibleRows().length) addButton.click();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeLoadingOrderFormset);
    } else {
        initializeLoadingOrderFormset();
    }
})();
