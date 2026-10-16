(function () {
    'use strict';
    const states = new WeakMap();
    function state(picker) {
        if (!states.has(picker)) states.set(picker, {revision: 0, timer: null, controller: null, index: -1, results: []});
        return states.get(picker);
    }
    function close(picker) {
        const s = state(picker);
        s.revision++;
        clearTimeout(s.timer);
        if (s.controller) s.controller.abort();
        picker.querySelector('.product-results').hidden = true;
        const input = picker.querySelector('.product-search');
        input.setAttribute('aria-expanded', 'false');
        input.removeAttribute('aria-activedescendant');
        input.removeAttribute('aria-busy');
        s.index = -1;
    }
    function select(picker, product) {
        close(picker);
        const input = picker.querySelector('.product-search');
        const hidden = picker.querySelector('.product-id');
        input.value = product.name;
        input.setCustomValidity('');
        hidden.value = String(product.id);
        picker.querySelector('.product-search-status').textContent = '';
        if (picker.dataset.context === 'purchase') {
            const row = picker.closest('tr');
            const priceInput = row.querySelector('[name$="-purchase_price"]');
            const unitSelect = row.querySelector('[name$="-packaging"]');
            if (unitSelect && Array.isArray(product.purchase_units)) {
                unitSelect.replaceChildren();
                product.purchase_units.forEach(unit => {
                    const option = document.createElement('option');
                    option.value = unit.id == null ? '' : String(unit.id);
                    option.textContent = unit.factor > 1 ? `${unit.name} (×${unit.factor})` : unit.name;
                    option.dataset.factor = String(unit.factor || 1);
                    option.dataset.purchasePrice = unit.purchase_price || '';
                    unitSelect.appendChild(option);
                });
                unitSelect.disabled = product.purchase_units.length === 0;
                unitSelect.dispatchEvent(new Event('change', {bubbles: true}));
            }
            if (priceInput) {
                const selectedUnit = unitSelect?.selectedOptions[0];
                priceInput.value = selectedUnit?.dataset.purchasePrice || product.purchase_price;
                priceInput.dispatchEvent(new Event('input', {bubbles: true}));
            }
        }
        if (picker.dataset.context === 'loading_order') {
            const row = picker.closest('tr');
            const unitSelect = row.querySelector('[name$="-packaging"]');
            if (unitSelect && Array.isArray(product.loading_units)) {
                unitSelect.replaceChildren();
                product.loading_units.forEach(unit => {
                    const option = document.createElement('option');
                    option.value = unit.id == null ? '' : String(unit.id);
                    option.textContent = unit.factor > 1 ? `${unit.name} (×${unit.factor})` : unit.name;
                    option.dataset.factor = String(unit.factor || 1);
                    unitSelect.appendChild(option);
                });
                unitSelect.disabled = product.loading_units.length === 0;
                unitSelect.dispatchEvent(new Event('change', {bubbles: true}));
            }
        }
        hidden.dispatchEvent(new Event('change', {bubbles: true}));
    }
    async function search(picker) {
        const s = state(picker), input = picker.querySelector('.product-search');
        const revision = s.revision, list = picker.querySelector('.product-results');
        const status = picker.querySelector('.product-search-status');
        const query = new URLSearchParams({q: input.value.trim(), context: picker.dataset.context});
        const client = document.getElementById('id_client');
        if (picker.dataset.context === 'sale' && client && client.value) query.set('client_id', client.value);
        s.controller = new AbortController();
        const controller = s.controller;
        const timeout = setTimeout(() => controller.abort(), 8000);
        input.setAttribute('aria-busy', 'true');
        status.textContent = picker.dataset.loading;
        try {
            const response = await fetch(`${picker.dataset.url}?${query}`, {
                credentials: 'same-origin', headers: {'Accept': 'application/json'}, signal: controller.signal,
            });
            if (!response.ok) throw new Error('Search failed');
            const payload = await response.json();
            if (s.revision !== revision || !picker.isConnected) return;
            if (!Array.isArray(payload.results)) throw new Error('Invalid results');
            s.results = payload.results.slice(0, 20);
            s.index = -1;
            list.replaceChildren();
            s.results.forEach((product, index) => {
                const option = document.createElement('button');
                option.type = 'button';
                option.className = 'list-group-item list-group-item-action product-result';
                option.setAttribute('role', 'option');
                option.setAttribute('aria-selected', 'false');
                option.tabIndex = -1;
                option.id = `${list.id}_${index}`;
                option.dataset.index = String(index);
                const price = product.purchase_price ?? product.price;
                if (picker.dataset.context === 'sale') {
                    option.textContent = `${product.name} · ${product.reference} · ${picker.dataset.stock}: ${product.stock} · ${picker.dataset.price}: ${price}`;
                } else if (picker.dataset.context === 'loading_order') {
                    option.textContent = `${product.name} · ${product.reference} · ${picker.dataset.stockAvailable}: ${product.stock}`;
                } else {
                    option.textContent = `${product.name} · ${product.reference} · ${picker.dataset.price}: ${price}`;
                }
                list.appendChild(option);
            });
            list.hidden = !s.results.length;
            input.setAttribute('aria-expanded', String(Boolean(s.results.length)));
            status.textContent = s.results.length ? '' : picker.dataset.empty;
        } catch (_error) {
            if (s.revision === revision) status.textContent = picker.dataset.error;
        } finally {
            clearTimeout(timeout);
            if (s.revision === revision) input.removeAttribute('aria-busy');
        }
    }
    document.addEventListener('input', event => {
        if (!event.target.matches('.product-search')) return;
        const picker = event.target.closest('.product-picker');
        close(picker);
        const hidden = picker.querySelector('.product-id');
        hidden.value = '';
        hidden.dispatchEvent(new Event('change', {bubbles: true}));
        event.target.setCustomValidity(event.target.value.trim() ? picker.dataset.invalid : '');
        const query = event.target.value.trim();
        picker.querySelector('.product-search-status').textContent = query.length < 2 ? picker.dataset.minimum : '';
        if (query.length >= 2 && !event.isComposing) state(picker).timer = setTimeout(() => search(picker), 300);
    });
    document.addEventListener('click', event => {
        const option = event.target.closest('.product-result');
        if (option) {
            const picker = option.closest('.product-picker');
            select(picker, state(picker).results[Number(option.dataset.index)]);
            picker.querySelector('.product-search').focus();
        }
        document.querySelectorAll('.product-picker').forEach(p => { if (!p.contains(event.target)) close(p); });
    });
    document.addEventListener('focusout', event => {
        const picker = event.target.closest('.product-picker');
        if (picker && !picker.contains(event.relatedTarget)) close(picker);
    });
    // Keep input focus until click selects a suggestion (mouse and touch).
    document.addEventListener('pointerdown', event => {
        if (event.target.closest('.product-result')) event.preventDefault();
    });
    document.addEventListener('keydown', event => {
        if (!event.target.matches('.product-search')) return;
        const picker = event.target.closest('.product-picker'), s = state(picker);
        if (event.key === 'Escape') { event.preventDefault(); close(picker); return; }
        if (picker.querySelector('.product-results').hidden) return;
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            s.index = (s.index + (event.key === 'ArrowDown' ? 1 : -1) + s.results.length) % s.results.length;
            picker.querySelectorAll('.product-result').forEach((option, i) => {
                option.classList.toggle('active', i === s.index);
                option.setAttribute('aria-selected', String(i === s.index));
                if (i === s.index) { event.target.setAttribute('aria-activedescendant', option.id); option.scrollIntoView({block: 'nearest'}); }
            });
        } else if (event.key === 'Enter') {
            event.preventDefault();
            if (s.index >= 0) select(picker, s.results[s.index]);
        }
    });
    document.addEventListener('change', event => {
        if (event.target.id === 'id_client') document.querySelectorAll('.product-picker').forEach(close);
        if (event.target.matches('[name$="-DELETE"]')) {
            const row = event.target.closest('tr'), input = row.querySelector('.product-search');
            if (input) input.setCustomValidity(event.target.checked ? '' : (input.value && !row.querySelector('.product-id').value ? row.querySelector('.product-picker').dataset.invalid : ''));
        }
    });
    window.ProductAutocomplete = {select, close};
})();

(function () {
    'use strict';
    const states = new WeakMap();

    function state(picker) {
        if (!states.has(picker)) {
            states.set(picker, {revision: 0, timer: null, controller: null, index: -1, results: []});
        }
        return states.get(picker);
    }

    function closeClientPicker(picker) {
        const current = state(picker);
        current.revision += 1;
        clearTimeout(current.timer);
        if (current.controller) current.controller.abort();
        picker.querySelector('.client-results').hidden = true;
        const input = picker.querySelector('.client-search');
        input.setAttribute('aria-expanded', 'false');
        input.removeAttribute('aria-activedescendant');
        input.removeAttribute('aria-busy');
        current.index = -1;
    }

    function clientLabel(client) {
        const detail = client.phone || client.tax_number;
        return detail ? `${client.name} — ${detail}` : client.name;
    }

    function selectClient(picker, client) {
        closeClientPicker(picker);
        const input = picker.querySelector('.client-search');
        const hidden = picker.querySelector('.client-id');
        input.value = clientLabel(client);
        input.setCustomValidity('');
        hidden.value = String(client.id);
        picker.querySelector('.client-search-status').textContent = '';
        const clientType = document.getElementById('sale-client-type');
        if (clientType) clientType.textContent = client.customer_type || '';
        hidden.dispatchEvent(new Event('change', {bubbles: true}));
    }

    async function searchClients(picker) {
        const current = state(picker);
        const input = picker.querySelector('.client-search');
        const list = picker.querySelector('.client-results');
        const status = picker.querySelector('.client-search-status');
        const revision = current.revision;
        current.controller = new AbortController();
        const controller = current.controller;
        const timeout = setTimeout(() => controller.abort(), 8000);
        input.setAttribute('aria-busy', 'true');
        status.textContent = picker.dataset.loading;
        try {
            const query = new URLSearchParams({q: input.value.trim()});
            const response = await fetch(`${picker.dataset.url}?${query}`, {
                credentials: 'same-origin', headers: {'Accept': 'application/json'}, signal: controller.signal,
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const payload = await response.json();
            if (current.revision !== revision || !picker.isConnected) return;
            if (!Array.isArray(payload.results)) throw new Error('Invalid results');
            current.results = payload.results.slice(0, 20);
            current.index = -1;
            list.replaceChildren();
            current.results.forEach((client, index) => {
                const option = document.createElement('button');
                option.type = 'button';
                option.className = 'list-group-item list-group-item-action client-result';
                option.setAttribute('role', 'option');
                option.setAttribute('aria-selected', 'false');
                option.tabIndex = -1;
                option.id = `${list.id}_${index}`;
                option.dataset.index = String(index);
                const title = document.createElement('span');
                title.className = 'd-block fw-semibold';
                title.textContent = client.name;
                const details = document.createElement('small');
                details.className = 'd-block text-muted';
                details.textContent = [client.phone, client.tax_number, client.customer_type].filter(Boolean).join(' · ');
                option.append(title, details);
                list.appendChild(option);
            });
            list.hidden = current.results.length === 0;
            input.setAttribute('aria-expanded', String(current.results.length > 0));
            status.textContent = current.results.length ? '' : picker.dataset.empty;
        } catch (_error) {
            if (current.revision === revision) status.textContent = picker.dataset.error;
        } finally {
            clearTimeout(timeout);
            if (current.revision === revision) input.removeAttribute('aria-busy');
        }
    }

    document.addEventListener('input', event => {
        if (!event.target.matches('.client-search')) return;
        const picker = event.target.closest('.client-picker');
        closeClientPicker(picker);
        const hidden = picker.querySelector('.client-id');
        hidden.value = '';
        hidden.dispatchEvent(new Event('change', {bubbles: true}));
        const clientType = document.getElementById('sale-client-type');
        if (clientType) clientType.textContent = '';
        const query = event.target.value.trim();
        event.target.setCustomValidity(query ? picker.dataset.invalid : '');
        picker.querySelector('.client-search-status').textContent = query.length < 2 ? picker.dataset.minimum : '';
        if (query.length >= 2 && !event.isComposing) {
            state(picker).timer = setTimeout(() => searchClients(picker), 300);
        }
    });

    document.addEventListener('click', event => {
        const option = event.target.closest('.client-result');
        if (option) {
            const picker = option.closest('.client-picker');
            selectClient(picker, state(picker).results[Number(option.dataset.index)]);
            picker.querySelector('.client-search').focus();
        }
        document.querySelectorAll('.client-picker').forEach(picker => {
            if (!picker.contains(event.target)) closeClientPicker(picker);
        });
    });

    document.addEventListener('pointerdown', event => {
        if (event.target.closest('.client-result')) event.preventDefault();
    });
    document.addEventListener('focusout', event => {
        const picker = event.target.closest('.client-picker');
        if (picker && !picker.contains(event.relatedTarget)) closeClientPicker(picker);
    });
    document.addEventListener('keydown', event => {
        if (!event.target.matches('.client-search')) return;
        const picker = event.target.closest('.client-picker');
        const current = state(picker);
        if (event.key === 'Escape') {
            event.preventDefault();
            closeClientPicker(picker);
            return;
        }
        if (picker.querySelector('.client-results').hidden) return;
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            current.index = (current.index + (event.key === 'ArrowDown' ? 1 : -1) + current.results.length) % current.results.length;
            picker.querySelectorAll('.client-result').forEach((option, index) => {
                const active = index === current.index;
                option.classList.toggle('active', active);
                option.setAttribute('aria-selected', String(active));
                if (active) {
                    event.target.setAttribute('aria-activedescendant', option.id);
                    option.scrollIntoView({block: 'nearest'});
                }
            });
        } else if (event.key === 'Enter' && current.results.length) {
            event.preventDefault();
            selectClient(picker, current.results[current.index >= 0 ? current.index : 0]);
        }
    });
})();
