(() => {
  const mobileQuery = window.matchMedia('(max-width: 760px)');

  function revealActiveNavigation() {
    if (!mobileQuery.matches) return;
    const strip = document.querySelector('[data-mobile-nav-scroll]');
    const active = strip?.querySelector('[aria-current="page"]');
    if (!strip || !active) return;
    requestAnimationFrame(() => {
      const itemLeft = active.offsetLeft - strip.offsetLeft;
      const left = itemLeft - (strip.clientWidth - active.offsetWidth) / 2;
      strip.scrollTo({left: Math.max(0, left), behavior: 'auto'});
    });
  }

  function enableNavigationKeyboard() {
    const strip = document.querySelector('[data-mobile-nav-scroll]');
    if (!strip || strip.dataset.keyboardReady === 'true') return;
    strip.dataset.keyboardReady = 'true';
    strip.addEventListener('keydown', event => {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      const links = Array.from(strip.querySelectorAll('a[href]'));
      const currentIndex = links.indexOf(document.activeElement);
      const step = event.key === 'ArrowRight' ? 1 : -1;
      const next = links[Math.min(links.length - 1, Math.max(0, currentIndex + step))];
      if (!next) return;
      event.preventDefault();
      next.focus();
      next.scrollIntoView({block: 'nearest', inline: 'center'});
    });
  }

  function labelTable(table) {
    if (table.dataset.mobileCards === 'true') return;
    const headers = Array.from(table.querySelectorAll(':scope > thead > tr:first-child > th'))
      .map(cell => cell.textContent.trim());
    if (!headers.length) return;
    table.dataset.mobileCards = 'true';
    table.querySelectorAll(':scope > tbody > tr').forEach(row => {
      Array.from(row.children).forEach((cell, index) => {
        if (!cell.dataset.label && headers[index]) cell.dataset.label = headers[index];
      });
    });
  }

  function initTables(root = document) {
    root.querySelectorAll?.('.table-card table').forEach(labelTable);
    if (root.matches?.('.table-card table')) labelTable(root);
  }

  function selectCalendarColumn(board, index) {
    board.querySelectorAll('[data-mobile-calendar-column]').forEach(button => {
      const selected = button.dataset.mobileCalendarColumn === String(index);
      button.classList.toggle('active', selected);
      button.setAttribute('aria-selected', String(selected));
    });
    board.querySelectorAll('[data-calendar-column-index]').forEach(column => {
      column.classList.toggle('is-mobile-selected', column.dataset.calendarColumnIndex === String(index));
    });
  }

  function initCalendarResources(root = document) {
    const boards = root.matches?.('[data-calendar-board]') ? [root] : root.querySelectorAll?.('[data-calendar-board]') || [];
    boards.forEach(board => {
      if (board.dataset.mobileColumnsReady === 'true') return;
      board.dataset.mobileColumnsReady = 'true';
      board.querySelectorAll('[data-mobile-calendar-column]').forEach(button => {
        button.addEventListener('click', () => selectCalendarColumn(board, button.dataset.mobileCalendarColumn));
      });
      selectCalendarColumn(board, 0);
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.body.classList.remove('mobile-nav-open');
    initTables();
    initCalendarResources();
    revealActiveNavigation();
    enableNavigationKeyboard();
    new MutationObserver(records => {
      records.forEach(record => record.addedNodes.forEach(node => {
        if (!(node instanceof Element)) return;
        initTables(node);
        initCalendarResources(node);
      }));
    }).observe(document.body, {childList: true, subtree: true});
  });

  mobileQuery.addEventListener?.('change', revealActiveNavigation);
})();
