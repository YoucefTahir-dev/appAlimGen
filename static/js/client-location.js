(function () {
    'use strict';
    const panel = document.getElementById('client-location');
    if (!panel) return;
    const form = document.getElementById('client-form');
    const field = name => form.elements.namedItem(name);
    const address = field('address');
    const status = document.getElementById('location-status');
    const button = document.getElementById('detect-location');
    const spinner = document.getElementById('location-spinner');
    const icon = document.getElementById('location-icon');
    let revision = 0;
    let busy = false;
    let controller = null;
    let gpsTimer = null;

    function finish() {
        revision++;
        busy = false;
        button.disabled = false;
        button.removeAttribute('aria-busy');
        spinner.hidden = true;
        icon.hidden = false;
        clearTimeout(gpsTimer);
        if (controller) controller.abort();
        controller = null;
    }
    // A late lookup must never overwrite an address typed or submitted meanwhile.
    address.addEventListener('input', function () {
        if (busy) finish();
        status.textContent = '';
    });
    form.addEventListener('submit', finish);

    button.addEventListener('click', function () {
        if (busy) return;
        if (!window.isSecureContext || !navigator.geolocation) {
            status.textContent = panel.dataset.unsupported;
            return;
        }
        const current = ++revision;
        busy = true;
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
        spinner.hidden = false;
        icon.hidden = true;
        status.textContent = panel.dataset.pending;
        const failure = error => {
            if (current !== revision) return;
            status.textContent = ({1: panel.dataset.denied, 2: panel.dataset.unavailable, 3: panel.dataset.timeout})[error.code] || panel.dataset.unavailable;
            finish();
        };
        gpsTimer = setTimeout(() => failure({code: 3}), 16000);
        try {
            navigator.geolocation.getCurrentPosition(async position => {
                if (current !== revision) return;
                clearTimeout(gpsTimer);
                const {latitude, longitude, accuracy} = position.coords;
                if (![latitude, longitude, accuracy].every(Number.isFinite) || Math.abs(latitude) > 90 || Math.abs(longitude) > 180 || accuracy < 0) {
                    failure({code: 2});
                    return;
                }
                controller = new AbortController();
                const requestController = controller;
                const timeout = setTimeout(() => requestController.abort(), 8000);
                const location = {latitude, longitude, location_accuracy: accuracy, formatted_address: '', place_id: ''};
                let detected = '';
                try {
                    const response = await fetch(panel.dataset.url, {
                        method: 'POST', body: new URLSearchParams({latitude, longitude}),
                        credentials: 'same-origin', signal: requestController.signal,
                        headers: {'X-CSRFToken': field('csrfmiddlewaretoken').value, 'Accept': 'application/json'},
                    });
                    if (!response.ok) throw new Error('Geocoding unavailable');
                    const result = await response.json();
                    if (typeof result.formatted_address !== 'string' || typeof result.place_id !== 'string'
                        || result.formatted_address.length > 1000 || result.place_id.length > 255) throw new Error('Invalid result');
                    location.formatted_address = result.formatted_address.trim();
                    location.place_id = result.place_id;
                    if (location.formatted_address.length <= address.maxLength) detected = location.formatted_address;
                } catch (_error) {
                    // GPS is still useful when the provider/key/network is unavailable.
                } finally {
                    clearTimeout(timeout);
                }
                if (current !== revision) return;
                Object.entries(location).forEach(([name, value]) => { field(name).value = value; });
                if (detected) address.value = detected;
                status.textContent = detected ? (accuracy > 100 ? panel.dataset.weak : panel.dataset.found) : panel.dataset.fallback;
                finish();
            }, failure, {enableHighAccuracy: true, timeout: 15000, maximumAge: 30000});
        } catch (_error) {
            failure({code: 2});
        }
    });
})();
