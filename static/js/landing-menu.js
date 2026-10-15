(() => {
  const close = menu => { menu.open = false; };
  document.addEventListener('DOMContentLoaded', () => {
    const menu = document.querySelector('.landing-mobile-nav');
    if (!menu) return;
    menu.querySelectorAll('a').forEach(link => link.addEventListener('click', () => close(menu)));
    document.addEventListener('click', event => { if (menu.open && !menu.contains(event.target)) close(menu); });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && menu.open) { close(menu); menu.querySelector('summary')?.focus(); }
    });
  });
})();
