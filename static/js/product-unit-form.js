(() => {
    'use strict';
    const form = document.getElementById('product-unit-form');
    if (!form) return;

    const byId = (id) => document.getElementById(id);
    const mode = byId('id_unit_mode');
    const factor = byId('id_carton-conversion_factor');
    const stockQuantity = byId('id_quantity');
    const initialCartons = byId('id_initial_cartons');
    const initialPieces = byId('id_initial_pieces');
    const purchasePiece = byId('id_purchase_price');
    const purchaseCarton = byId('id_carton-reference_purchase_price');
    const retailPiece = byId('id_retail_price');
    const retailCarton = byId('id_carton-retail_price');
    const name = byId('id_name');
    const category = byId('id_category');
    const translated = (key, fallback) => form.dataset[key] || fallback;
    const fill = (template, values) => Object.entries(values).reduce(
        (result, [key, value]) => result.replaceAll(`__${key}__`, String(value)),
        template,
    );

    const decimalValue = (input) => {
        const value = Number.parseFloat((input?.value || '').replace(',', '.'));
        return Number.isFinite(value) ? value : 0;
    };
    const integerValue = (input) => Math.max(0, Math.trunc(decimalValue(input)));
    const centsValue = (input) => {
        const raw = (input?.value || '').trim().replace(',', '.');
        if (!/^\d+(?:\.\d{0,2})?$/.test(raw)) return null;
        const [units, decimals = ''] = raw.split('.');
        return Number.parseInt(units, 10) * 100 + Number.parseInt(decimals.padEnd(2, '0') || '0', 10);
    };
    const setCents = (input, cents) => {
        if (!input || cents === null || cents < 0) return;
        input.value = (cents / 100).toFixed(2);
        input.dispatchEvent(new Event('input', { bubbles: true }));
    };
    const money = (input) => decimalValue(input).toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    }) + ' DZD';
    const text = (id, value) => {
        const node = byId(id);
        if (node) node.textContent = value;
    };
    const cartonEnabled = () => ['carton_only', 'carton_and_piece'].includes(mode?.value);
    const factorValue = () => integerValue(factor);

    function convertPrice(container, direction, onlyWhenEmpty = false) {
        const piece = byId(container?.dataset.pieceId);
        const carton = byId(container?.dataset.cartonId);
        const currentFactor = factorValue();
        if (!piece || !carton || currentFactor < 2) return false;
        const source = direction === 'piece-to-carton' ? piece : carton;
        const target = direction === 'piece-to-carton' ? carton : piece;
        if (onlyWhenEmpty && target.value.trim()) return false;
        const sourceCents = centsValue(source);
        if (sourceCents === null) return false;
        const targetCents = direction === 'piece-to-carton'
            ? sourceCents * currentFactor
            : Math.floor((sourceCents + currentFactor / 2) / currentFactor);
        setCents(target, targetCents);
        return true;
    }

    function update() {
        const enabled = cartonEnabled();
        document.querySelectorAll('.carton-field').forEach((node) => { node.hidden = !enabled; });
        document.querySelectorAll('.initial-carton-field').forEach((node) => { node.hidden = !enabled; });

        const currentFactor = factorValue();
        const cartons = enabled ? integerValue(initialCartons) : 0;
        const pieces = initialPieces ? integerValue(initialPieces) : integerValue(stockQuantity);
        const baseStock = enabled && currentFactor > 0 ? cartons * currentFactor + pieces : pieces;
        const equivalence = currentFactor > 0
            ? fill(translated('equivalenceTemplate', '1 carton = __FACTOR__ pièce(s)'), { FACTOR: currentFactor })
            : translated('factorPrompt', 'Renseignez le nombre de pièces par carton.');
        text('carton-equivalence', equivalence);
        text('preview-factor', equivalence);

        const conversion = enabled && currentFactor > 0
            ? fill(
                translated('mixedConversionTemplate', '__CARTONS__ carton(s) × __FACTOR__ + __PIECES__ pièce(s) = __BASE__ pièce(s)'),
                { CARTONS: cartons, FACTOR: currentFactor, PIECES: pieces, BASE: baseStock },
            )
            : fill(translated('pieceConversionTemplate', '__BASE__ pièce(s)'), { BASE: baseStock });
        text('stock-conversion-preview', conversion);
        text('preview-stock', conversion);

        if (currentFactor > 0 && purchaseCarton?.value) {
            text('piece-cost-hint', fill(
                translated('costTemplate', 'Coût indicatif : __COST__ DZD/pièce. Le prix pièce saisi reste inchangé.'),
                { COST: (decimalValue(purchaseCarton) / currentFactor).toFixed(2) },
            ));
        } else {
            text('piece-cost-hint', translated('costPrompt', 'Le coût pièce indicatif sera calculé sans remplacer votre saisie.'));
        }

        text('preview-name', name?.value.trim() || translated('newProduct', 'Nouveau produit'));
        text('preview-category', category?.selectedOptions[0]?.text || translated('noCategory', 'Sans catégorie'));
        text('preview-mode', mode?.selectedOptions[0]?.text || '—');
        text('preview-purchase-piece', money(purchasePiece));
        text('preview-purchase-carton', money(purchaseCarton));
        text('preview-retail-piece', money(retailPiece));
        text('preview-retail-carton', money(retailCarton));
    }

    document.querySelectorAll('[data-price-convert]').forEach((button) => {
        button.addEventListener('click', () => {
            convertPrice(button.closest('[data-piece-id][data-carton-id]'), button.dataset.priceConvert);
            update();
        });
    });

    document.querySelectorAll('[data-piece-id][data-carton-id]').forEach((container) => {
        const piece = byId(container.dataset.pieceId);
        const carton = byId(container.dataset.cartonId);
        piece?.addEventListener('change', () => convertPrice(container, 'piece-to-carton', true));
        carton?.addEventListener('change', () => convertPrice(container, 'carton-to-piece', true));
    });

    byId('generate-product-barcode')?.addEventListener('click', async (event) => {
        const button = event.currentTarget;
        const barcode = byId('id_barcode');
        button.disabled = true;
        try {
            const response = await fetch(form.dataset.generateBarcodeUrl, {
                headers: { Accept: 'application/json' }, credentials: 'same-origin',
            });
            if (!response.ok) throw new Error('barcode generation failed');
            const payload = await response.json();
            barcode.value = payload.barcode;
            barcode.dispatchEvent(new Event('input', { bubbles: true }));
        } finally {
            button.disabled = false;
        }
    });

    form.addEventListener('input', update);
    form.addEventListener('change', update);
    update();
})();
