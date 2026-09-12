(function () {
    'use strict';
    const panel = document.getElementById('client-location');
    if (!panel) return;
    const form = document.getElementById('client-form');
    const field = name => form.elements.namedItem(name);
    const status = document.getElementById('location-status');
    const button = document.getElementById('locate-client');
    const proposal = document.getElementById('location-proposal');
    const confirm = document.getElementById('confirm-location');
    const maps = document.getElementById('client-maps-link');
    let draft = null;
    let revision = 0;
    let busy = false;

    function updateMapLink() {
        const latitude = field('latitude').value;
        const longitude = field('longitude').value;
        maps.hidden = latitude === '' || longitude === '';
        maps.href = 'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(latitude + ',' + longitude);
    }
    function stopProposal() {
        revision++;
        draft = null;
        busy = false;
        button.disabled = false;
        proposal.hidden = true;
    }
    function applyPosition(replaceAddress) {
        if (!draft) return;
        if (replaceAddress && (!draft.formatted_address || draft.formatted_address.length > field('address').maxLength)) return;
        ['latitude', 'longitude', 'location_accuracy', 'formatted_address', 'place_id'].forEach(name => {
            field(name).value = draft[name] == null ? '' : draft[name];
        });
        if (replaceAddress) field('address').value = draft.formatted_address;
        stopProposal();
        updateMapLink();
        status.textContent = panel.dataset.confirmed;
        if (!replaceAddress) field('address').focus();
    }
    confirm.addEventListener('click', () => applyPosition(true));
    document.getElementById('keep-manual-address').addEventListener('click', () => applyPosition(false));
    document.getElementById('cancel-location').addEventListener('click', () => {
        stopProposal();
        status.textContent = '';
    });
    ['latitude', 'longitude'].forEach(name => field(name).addEventListener('input', () => {
        stopProposal();
        ['formatted_address', 'place_id', 'location_accuracy'].forEach(key => { field(key).value = ''; });
        updateMapLink();
    }));
    form.addEventListener('submit', event => {
        if (busy || draft) {
            event.preventDefault();
            status.textContent = panel.dataset.confirmFirst;
            document.getElementById('keep-manual-address').focus();
        }
    });
    button.addEventListener('click', function () {
        if (!window.isSecureContext || !navigator.geolocation) {
            status.textContent = panel.dataset.unsupported;
            return;
        }
        stopProposal();
        const current = revision;
        busy = true;
        button.disabled = true;
        status.textContent = panel.dataset.pending;
        navigator.geolocation.getCurrentPosition(async position => {
            if (current !== revision) return;
            const {latitude, longitude, accuracy} = position.coords;
            if (![latitude, longitude, accuracy].every(Number.isFinite) || Math.abs(latitude) > 90 || Math.abs(longitude) > 180 || accuracy < 0) {
                stopProposal();
                status.textContent = panel.dataset.unavailable;
                return;
            }
            draft = {latitude, longitude, location_accuracy: accuracy, formatted_address: '', place_id: ''};
            proposal.hidden = false;
            confirm.disabled = true;
            document.getElementById('detected-coordinates').textContent = latitude.toFixed(6) + ', ' + longitude.toFixed(6);
            document.getElementById('detected-accuracy').textContent = '± ' + accuracy.toFixed(1);
            document.getElementById('location-warning').textContent = accuracy > 100 ? panel.dataset.weak : '';
            document.getElementById('detected-address').textContent = panel.dataset.pending;
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 8000);
            try {
                const data = new URLSearchParams({latitude, longitude});
                const response = await fetch(panel.dataset.url, {
                    method: 'POST', body: data, credentials: 'same-origin', signal: controller.signal,
                    headers: {'X-CSRFToken': field('csrfmiddlewaretoken').value, 'Accept': 'application/json'},
                });
                if (!response.ok) throw new Error('Geocoding unavailable');
                const result = await response.json();
                if (current !== revision) return;
                if (typeof result.formatted_address !== 'string' || typeof result.place_id !== 'string') throw new Error('Invalid result');
                draft.formatted_address = result.formatted_address;
                draft.place_id = result.place_id;
                document.getElementById('detected-address').textContent = result.formatted_address || panel.dataset.fallback;
                confirm.disabled = !result.formatted_address || result.formatted_address.length > field('address').maxLength;
                status.textContent = result.formatted_address.length > field('address').maxLength ? panel.dataset.long : '';
            } catch (_error) {
                if (current === revision) {
                    document.getElementById('detected-address').textContent = panel.dataset.fallback;
                    status.textContent = panel.dataset.fallback;
                }
            } finally {
                clearTimeout(timeout);
                if (current === revision) { busy = false; button.disabled = false; }
            }
        }, error => {
            if (current !== revision) return;
            stopProposal();
            status.textContent = ({1: panel.dataset.denied, 2: panel.dataset.unavailable, 3: panel.dataset.timeout})[error.code] || panel.dataset.unavailable;
        }, {enableHighAccuracy: true, timeout: 15000, maximumAge: 30000});
    });
    updateMapLink();
})();
