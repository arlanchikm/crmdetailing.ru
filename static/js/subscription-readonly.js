(() => {
  const dialog = document.getElementById('subscription-warning');
  if (!dialog) return;
  const show = () => { if (!dialog.open) dialog.showModal(); };
  const allowed = path => /^(\/(subscription\/|login\/|logout\/|password-reset\/)|\/notifications\/(?:[^/]+\/)?read\/|\/locations\/select\/|\/notifications\/push\/(?:un)?subscribe\/)/.test(path);
  const editLink = path => /\/(new|edit|delete|delete-permanently|transition|movement)(\/|$)/.test(path);
  document.addEventListener('submit', event => {
    const form = event.target;
    if ((form.method || 'get').toLowerCase() !== 'get' && !allowed(new URL(form.action, location.href).pathname)) {
      event.preventDefault(); event.stopImmediatePropagation(); show();
    }
  }, true);
  document.addEventListener('click', event => {
    if (event.target.closest('#subscription-warning')) return;
    const link = event.target.closest('a');
    const control = event.target.closest('button,input,select,textarea');
    const form = control?.closest('form');
    const blockedForm = form && (form.method || 'get').toLowerCase() !== 'get' && !allowed(new URL(form.action || location.href, location.href).pathname);
    if ((link && editLink(new URL(link.dataset.modalUrl || link.dataset.url || link.href, location.href).pathname)) || blockedForm || (event.target.closest('.planner-column.is-clickable') && !event.target.closest('.booking-card'))) {
      event.preventDefault(); event.stopImmediatePropagation(); show();
    }
  }, true);
  // Protect scripted requests too (dragging appointments, inline editing, file uploads).
  const originalFetch = window.fetch.bind(window);
  window.fetch = (resource, options = {}) => {
    const method = (options.method || resource?.method || 'GET').toUpperCase();
    const path = new URL(typeof resource === 'string' || resource instanceof URL ? resource : resource.url, location.href).pathname;
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && !allowed(path)) {
      show();
      return Promise.resolve(new Response(JSON.stringify({error:'subscription_expired'}), {status:403, headers:{'Content-Type':'application/json'}}));
    }
    return originalFetch(resource, options);
  };
  if (dialog.hasAttribute('data-auto-open')) show();
})();
