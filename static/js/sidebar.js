document.addEventListener('DOMContentLoaded', () => {
    const sidebar = document.getElementById('appSidebar');
    const sidebarLinks = document.querySelectorAll('[data-sidebar-link]');
    const isMobileSidebar = () => window.matchMedia('(max-width: 991.98px)').matches;

    sidebarLinks.forEach((link) => {
        link.addEventListener('click', () => {
            if (!sidebar || !isMobileSidebar() || !window.bootstrap) {
                return;
            }
            const instance = bootstrap.Offcanvas.getInstance(sidebar);
            if (instance) {
                instance.hide();
            }
        });
    });

    if (sidebar && window.bootstrap) {
        sidebar.addEventListener('shown.bs.offcanvas', () => {
            if (!history.state || !history.state.sidebarOpen) {
                history.pushState({ sidebarOpen: true }, '');
            }
        });

        window.addEventListener('popstate', () => {
            const instance = bootstrap.Offcanvas.getInstance(sidebar);
            if (instance) {
                instance.hide();
            }
        });

        sidebar.addEventListener('hidden.bs.offcanvas', () => {
            if (history.state && history.state.sidebarOpen) {
                history.replaceState(null, '');
            }
        });
    }
});
