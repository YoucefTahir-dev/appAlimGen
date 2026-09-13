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
            picker.closest('tr').querySelector('[name$="-purchase_price"]').value = product.purchase_price;
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
