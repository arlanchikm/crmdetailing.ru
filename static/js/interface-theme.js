(() => {
  const key = 'crm-interface-theme';
  const valid = new Set(['light_blue', 'dark']);
  const read = () => {
    try { return valid.has(localStorage.getItem(key)) ? localStorage.getItem(key) : 'light_blue'; }
    catch { return 'light_blue'; }
  };
  const apply = theme => {
    const body = document.body;
    if (!body?.classList.contains('crm-layout')) return;
    body.classList.remove('theme-light_blue', 'theme-dark');
    body.classList.add(`theme-${theme}`);
    const themeColor = document.querySelector('#crm-theme-color');
    if (themeColor) {
      const pageColor = getComputedStyle(body).getPropertyValue('--crm-background-color').trim();
      themeColor.content = pageColor || (theme === 'dark' ? '#101a29' : '#dcebfa');
    }
    document.querySelectorAll('[data-interface-theme]').forEach(button => {
      const selected = button.dataset.interfaceTheme === theme;
      button.classList.toggle('selected', selected);
      button.setAttribute('aria-checked', String(selected));
    });
  };
  document.addEventListener('DOMContentLoaded', () => {
    apply(read());
    document.querySelectorAll('[data-interface-theme]').forEach(button => button.addEventListener('click', () => {
      const theme = button.dataset.interfaceTheme;
      if (!valid.has(theme)) return;
      try { localStorage.setItem(key, theme); } catch {}
      apply(theme);
    }));
  });
})();
