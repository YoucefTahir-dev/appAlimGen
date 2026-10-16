(function () {
    'use strict';
    const modalElement = document.getElementById('quick-category-modal');
    const openButton = document.getElementById('quick-category-open');
    const saveButton = document.getElementById('quick-category-save');
    const nameInput = document.getElementById('quick-category-name');
    const select = document.getElementById('id_category');
    const error = document.getElementById('quick-category-modal-error');
    if (!modalElement || !openButton || !saveButton || !nameInput || !select || !window.bootstrap) return;
    const modal = window.bootstrap.Modal.getOrCreateInstance(modalElement);
    openButton.addEventListener('click', function () { error.textContent = ''; nameInput.value = ''; modal.show(); });
    modalElement.addEventListener('shown.bs.modal', function () { nameInput.focus(); });
    saveButton.addEventListener('click', async function () {
        error.textContent = '';
        saveButton.disabled = true;
        const data = new FormData();
        data.append('name', nameInput.value);
        data.append('csrfmiddlewaretoken', document.querySelector('[name="csrfmiddlewaretoken"]')?.value || '');
        try {
            const response = await fetch(modalElement.dataset.createUrl, {method: 'POST', body: data, headers: {'X-Requested-With': 'XMLHttpRequest'}});
            const payload = await response.json();
            if (!response.ok) {
                const details = payload.errors?.name;
                error.textContent = Array.isArray(details) ? (details[0]?.message || details[0]) : 'Impossible de créer la catégorie.';
                return;
            }
            let option = Array.from(select.options).find(item => item.value === String(payload.id));
            if (!option) { option = new Option(payload.name, payload.id, true, true); select.add(option); }
            select.value = String(payload.id);
            select.dispatchEvent(new Event('change', {bubbles: true}));
            modal.hide();
        } catch (_) { error.textContent = 'Impossible de créer la catégorie.'; }
        finally { saveButton.disabled = false; }
    });
})();
