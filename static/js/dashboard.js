document.addEventListener('DOMContentLoaded', () => {
    const periodSelect = document.getElementById('dashboardPeriod');
    const customFields = document.querySelectorAll('.custom-period-field');
    const updateCustomFields = () => {
        const isCustom = periodSelect && periodSelect.value === 'custom';
        customFields.forEach((field) => {
            field.classList.toggle('d-none', !isCustom);
            const input = field.querySelector('input');
            if (input) input.required = isCustom;
        });
    };
    updateCustomFields();
    if (periodSelect) {
        periodSelect.addEventListener('change', updateCustomFields);
    }

    const labels = document.getElementById('financialTrendChart').dataset;
    const data = JSON.parse(document.getElementById('dashboard-data').textContent);
    const moneyTicks = (value) => `${Number(value).toLocaleString()} DZD`;
    const chartOptions = {
        responsive: true,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { position: 'bottom' } },
        scales: { y: { ticks: { callback: moneyTicks } } }
    };
    new Chart(document.getElementById('financialTrendChart'), {
        type: 'line',
        data: {
            labels: data.trend.labels,
            datasets: [
                { label: labels.revenue, data: data.trend.revenue, borderColor: '#198754', backgroundColor: 'rgba(25,135,84,.12)', tension: .35 },
                { label: labels.purchases, data: data.trend.purchases, borderColor: '#0d6efd', backgroundColor: 'rgba(13,110,253,.12)', tension: .35 },
                { label: labels.expenses, data: data.trend.expenses, borderColor: '#dc3545', backgroundColor: 'rgba(220,53,69,.12)', tension: .35 },
                { label: labels.grossProfit, data: data.trend.gross_profit, borderColor: '#ffc107', backgroundColor: 'rgba(255,193,7,.12)', tension: .35 },
                { label: labels.netProfit, data: data.trend.net_profit, borderColor: '#6f42c1', backgroundColor: 'rgba(111,66,193,.12)', tension: .35 },
            ]
        },
        options: chartOptions
    });
    new Chart(document.getElementById('salesCategoryChart'), {
        type: 'doughnut',
        data: { labels: data.sales_categories.labels, datasets: [{ data: data.sales_categories.values }] },
        options: { responsive: true, plugins: { legend: { position: 'bottom' } } }
    });
    new Chart(document.getElementById('expenseCategoryChart'), {
        type: 'doughnut',
        data: { labels: data.expense_categories.labels, datasets: [{ data: data.expense_categories.values }] },
        options: { responsive: true, plugins: { legend: { position: 'bottom' } } }
    });
});
