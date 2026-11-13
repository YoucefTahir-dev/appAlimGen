'use strict';

// No executable HTML attributes: works with script-src-attr 'none', including
// standalone invoice/ticket/report pages that do not extend the ERP base.
document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-print-page]');
    if (button) {
        event.preventDefault();
        window.print();
    }
});
