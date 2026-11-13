const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = name => fs.readFileSync(path.join(__dirname, '../../static/js', name + '.js'), 'utf8');

test('print controls bind externally and preserve button/child click behavior', () => {
    let listener, prints = 0, prevented = 0;
    vm.runInNewContext(source('print-controls'), {
        document: {addEventListener: (event, callback) => {assert.equal(event, 'click'); listener = callback;}},
        window: {print: () => prints++},
    });
    listener({target: {closest: () => null}, preventDefault: () => prevented++});
    assert.equal(prints, 0);
    listener({target: {closest: selector => {assert.equal(selector, '[data-print-page]'); return {};}},
              preventDefault: () => prevented++});
    assert.equal(prints, 1);
    assert.equal(prevented, 1);
});

test('invoice auto print and delayed printer test keep their load behavior', () => {
    for (const [script, expectedDelay] of [['auto-print', 0], ['printer-test', 300]]) {
        let listener, prints = 0, delay = 0;
        const window = {
            addEventListener: (event, callback) => {assert.equal(event, 'load'); listener = callback;},
            print: () => prints++,
            setTimeout: (callback, milliseconds) => {delay = milliseconds; callback();},
        };
        vm.runInNewContext(source(script), {window});
        listener();
        assert.equal(prints, 1);
        assert.equal(delay, expectedDelay);
    }
});

test('dashboard reads translated labels as data, not executable template code', () => {
    let listener;
    const charts = [];
    const dataset = {revenue: 'Translated revenue', purchases: 'Translated purchases',
                     expenses: 'Translated expenses', grossProfit: 'Translated gross', netProfit: 'Translated net'};
    const data = {trend: {labels: [], revenue: [], purchases: [], expenses: [], gross_profit: [], net_profit: []},
                  sales_categories: {labels: [], values: []}, expense_categories: {labels: [], values: []}};
    vm.runInNewContext(source('dashboard'), {
        document: {
            addEventListener: (event, callback) => {listener = callback;},
            querySelectorAll: () => [],
            getElementById: id => id === 'dashboardPeriod' ? null :
                id === 'dashboard-data' ? {textContent: JSON.stringify(data)} : {dataset},
        },
        Chart: function (element, config) {charts.push(config);},
    });
    listener();
    assert.equal(charts.length, 3);
    assert.deepEqual(Array.from(charts[0].data.datasets, item => item.label), Object.values(dataset));
});
