(function () {
    const button = document.getElementById('bluetoothScanButton');
    const nameInput = document.getElementById('id_bluetooth_name');
    const status = document.getElementById('bluetoothScanStatus');
    if (!button || !nameInput) return;

    button.addEventListener('click', async function () {
        if (!navigator.bluetooth || !navigator.bluetooth.requestDevice) {
            if (status) status.textContent = button.dataset.unsupported || '';
            return;
        }
        try {
            if (status) status.textContent = button.dataset.searching || '';
            const device = await navigator.bluetooth.requestDevice({acceptAllDevices: true});
            nameInput.value = device.name || '';
            nameInput.dispatchEvent(new Event('change', {bubbles: true}));
            if (status) status.textContent = device.name ? (button.dataset.selected || '') : (button.dataset.unnamed || '');
        } catch (error) {
            if (status) status.textContent = error && error.name === 'NotFoundError' ? (button.dataset.cancelled || '') : (button.dataset.unavailable || '');
        }
    });
}());
