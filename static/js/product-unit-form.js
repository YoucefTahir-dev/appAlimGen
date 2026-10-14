(() => {
    'use strict';
    const form = document.getElementById('product-unit-form');
    if (!form) return;

    const byId = (id) => document.getElementById(id);
    const mode = byId('id_unit_mode');
    const factor = byId('id_carton-conversion_factor');
    const stockQuantity = byId('id_quantity');
    const stockUnit = byId('id_stock_unit');
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

    const numberValue = (input) => {
        const value = Number.parseFloat((input?.value || '').replace(',', '.'));
        return Number.isFinite(value) ? value : 0;
    };
    const money = (input) => numberValue(input).toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    }) + ' DZD';
    const text = (id, value) => {
        const node = byId(id);
        if (node) node.textContent = value;
    };
    const cartonEnabled = () => ['carton_only', 'carton_and_piece'].includes(mode?.value);

    function update() {
        const enabled = cartonEnabled();
        document.querySelectorAll('.carton-field').forEach((node) => { node.hidden = !enabled; });
        const cartonOption = stockUnit?.querySelector('option[value="carton"]');
        if (cartonOption) cartonOption.disabled = !enabled;
        if (!enabled && stockUnit?.value === 'carton') stockUnit.value = 'base';

        const factorValue = Math.max(0, Math.trunc(numberValue(factor)));
        const entered = Math.max(0, Math.trunc(numberValue(stockQuantity)));
        const baseStock = stockUnit?.value === 'carton' && factorValue > 0 ? entered * factorValue : entered;
        const equivalence = factorValue > 0
            ? fill(translated('equivalenceTemplate', '1 carton = __FACTOR__ pièce(s)'), { FACTOR: factorValue })
            : translated('factorPrompt', 'Renseignez le nombre de pièces par carton.');
        text('carton-equivalence', equivalence);
        text('preview-factor', equivalence);

        const conversion = stockUnit?.value === 'carton' && factorValue > 0
            ? fill(
                translated('cartonConversionTemplate', '__ENTERED__ carton(s) × __FACTOR__ = __BASE__ pièce(s)'),
                { ENTERED: entered, FACTOR: factorValue, BASE: baseStock },
            )
            : fill(translated('pieceConversionTemplate', '__BASE__ pièce(s)'), { BASE: baseStock });
        text('stock-conversion-preview', conversion);
        text('preview-stock', conversion);

        if (factorValue > 0 && purchaseCarton?.value) {
            text('piece-cost-hint', fill(
                translated('costTemplate', 'Coût indicatif : __COST__ DZD/pièce. Le prix pièce saisi reste inchangé.'),
                { COST: (numberValue(purchaseCarton) / factorValue).toFixed(2) },
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

    form.addEventListener('input', update);
    form.addEventListener('change', update);
    update();
})();
