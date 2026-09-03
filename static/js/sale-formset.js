(function () {
    'use strict';

    function initializeSaleFormset() {
        const body = document.getElementById('sale-lines-body');
        const template = document.getElementById('sale-line-template');
        const addButton = document.getElementById('add-sale-line');
        const totalForms = document.getElementById('id_lines-TOTAL_FORMS');

        if (!body || !template || !addButton || !totalForms) {
            return;
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
        }

        body.querySelectorAll('.sale-line-row').forEach(bindRemoveButton);

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
