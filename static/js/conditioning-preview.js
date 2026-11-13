(() => {
  const form = document.getElementById('conditioning-correction-form');
  const factor = document.getElementById('id_new_factor');
  const target = document.getElementById('corrected-stock-preview');
  const difference = document.getElementById('corrected-difference-preview');
  if (!form || !factor || form.dataset.requiresInventory === 'true') return;
  const refresh = () => {
    const value = Number.parseInt(factor.value, 10);
    if (!Number.isInteger(value) || value < 2) { target.textContent = '—'; difference.textContent = '—'; return; }
    const corrected = Number.parseInt(form.dataset.originalCartons, 10) * value;
    const current = Number.parseInt(form.dataset.currentStock, 10);
    target.textContent = String(corrected);
    difference.textContent = `${corrected - current >= 0 ? '+' : ''}${corrected - current}`;
  };
  factor.addEventListener('input', refresh);
  refresh();
})();
