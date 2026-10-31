(() => {
    'use strict';

    const form = document.getElementById('login-form');
    if (!form) return;

    let submitting = false;
    form.addEventListener('submit', (event) => {
        if (event.defaultPrevented) return;
        if (submitting) {
            event.preventDefault();
            return;
        }
        submitting = true;
        form.setAttribute('aria-busy', 'true');
        form.querySelectorAll('[type="submit"]').forEach((button) => {
            button.disabled = true;
        });
    });

    // A login rotates Django's CSRF secret. A form restored by the Back button
    // can therefore contain an old token; request a fresh GET before reuse.
    window.addEventListener('pageshow', (event) => {
        if (event.persisted) window.location.reload();
    });
})();
