(() => {
  'use strict';

  const ERP_URL = 'https://gestio-stock-web.onrender.com/';
  const PROBE_URL = `${ERP_URL}static/img/logo.png`;
  // Render peut avoir besoin de plusieurs dizaines de secondes après une veille.
  const TIMEOUT_MS = 60000;

  const status = document.getElementById('status');
  const spinner = document.getElementById('spinner');
  const offline = document.getElementById('offline');
  const retry = document.getElementById('retry');

  function showOffline() {
    status.textContent = 'Impossible de joindre le serveur sécurisé.';
    spinner.hidden = true;
    offline.hidden = false;
    retry.focus();
  }

  function connect() {
    status.textContent = 'Connexion sécurisée à l’ERP…';
    spinner.hidden = false;
    offline.hidden = true;
    retry.disabled = true;

    const probe = new Image();
    const timer = window.setTimeout(() => {
      probe.src = '';
      showOffline();
      retry.disabled = false;
    }, TIMEOUT_MS);

    probe.onload = () => {
      window.clearTimeout(timer);
      window.location.replace(ERP_URL);
    };
    probe.onerror = () => {
      window.clearTimeout(timer);
      showOffline();
      retry.disabled = false;
    };
    probe.src = `${PROBE_URL}?desktop_probe=${Date.now()}`;
  }

  retry.addEventListener('click', connect);
  connect();
})();
