const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('static/js/client-location.js', 'utf8');

function setup() {
    const elements = {};
    function element(name) {
        return elements[name] ||= {value: '', maxLength: 1000, dataset: {}, listeners: {},
            addEventListener(event, callback) { this.listeners[event] = callback; },
            setAttribute() {}, removeAttribute() {}};
    }
    const panel = element('client-location');
    for (const key of ['pending', 'imprecise', 'timeout', 'weak', 'found', 'fallback', 'denied']) panel.dataset[key] = key;
    const form = element('client-form');
    form.elements = {namedItem: element};
    element('address').value = 'Existing address';
    element('latitude').value = 'old-coordinate';
    const timers = new Map(), calls = [], cleared = [];
    let serial = 0, success, failure;
    const window = {isSecureContext: true, addEventListener() {}};
    vm.runInNewContext(source, {
        document: {getElementById: element}, window, Number, URLSearchParams, AbortController,
        setTimeout: (fn, ms) => { timers.set(++serial, {fn, ms}); return serial; },
        clearTimeout: id => timers.delete(id),
        navigator: {geolocation: {
            watchPosition(ok, fail, options) { success = ok; failure = fail; assert.equal(options.maximumAge, 0); assert.equal(options.enableHighAccuracy, true); return 0; },
            clearWatch: id => cleared.push(id),
        }},
        fetch: async (url, options) => { calls.push(options.body.toString()); return {ok: true, json: async () => ({formatted_address: 'Selected address', place_id: 'mock'})}; },
    });
    element('detect-location').listeners.click();
    return {element, calls, cleared, failure: code => failure({code}),
        emit: (accuracy, latitude = 36) => success({coords: {accuracy, latitude, longitude: 3}}),
        expire: () => [...timers.values()].find(t => t.ms === 12000)?.fn(),
        flush: () => new Promise(resolve => setImmediate(resolve)),
    };
}

test('waits for best fix and makes exactly one lookup; ignores late callbacks', async () => {
    const s = setup();
    [1200, 400, 80].forEach(a => s.emit(a));
    assert.equal(s.calls.length, 0);
    s.emit(25, 37); s.emit(10, 38); s.expire(); await s.flush();
    assert.equal(s.calls.length, 1);
    assert.equal(s.calls[0], 'latitude=37&longitude=3');
    assert.equal(s.element('location_accuracy').value, 25);
    assert.deepEqual(s.cleared, [0]);
});
test('deadline uses best acceptable fix, not the last fix', async () => {
    const s = setup();
    [500, 250, 90, 300].forEach(a => s.emit(a));
    s.expire(); await s.flush();
    assert.equal(s.element('location_accuracy').value, 90);
    assert.equal(s.element('location-status').textContent, 'weak');
    assert.equal(s.calls.length, 1);
});
test('poor fixes preserve address and coordinates without Google request', async () => {
    const s = setup(); [1500, 1200, 800].forEach(a => s.emit(a));
    s.expire(); await s.flush();
    assert.equal(s.calls.length, 0);
    assert.equal(s.element('address').value, 'Existing address');
    assert.equal(s.element('latitude').value, 'old-coordinate');
    assert.equal(s.element('location-status').textContent, 'imprecise');
});
test('permission denial and empty deadline clean up watch', () => {
    for (const denied of [true, false]) {
        const s = setup(); if (denied) s.failure(1); else s.expire();
        assert.equal(s.element('location-status').textContent, denied ? 'denied' : 'timeout');
        assert.equal(s.calls.length, 0); assert.deepEqual(s.cleared, [0]);
    }
});
test('manual edits cancel acquisition; stale fixes cannot overwrite', async () => {
    const s = setup(); s.element('address').value = 'Manual';
    s.element('address').listeners.input(); s.emit(10); await s.flush();
    assert.equal(s.calls.length, 0); assert.equal(s.element('address').value, 'Manual');
});
test('accuracy thresholds and invalid measurements', async () => {
    for (const accuracy of [50, 100, 100.01]) {
        const s = setup(); s.emit(NaN); s.emit(-1); s.emit(accuracy);
        assert.equal(s.calls.length, accuracy === 50 ? 1 : 0);
        s.expire(); await s.flush();
        assert.equal(s.calls.length, accuracy <= 100 ? 1 : 0);
    }
});
test('transient failure does not discard a later precise fix', async () => {
    const s = setup(); s.failure(2); s.emit(20); await s.flush();
    assert.equal(s.calls.length, 1);
    assert.equal(s.element('location-status').textContent, 'found');
});
