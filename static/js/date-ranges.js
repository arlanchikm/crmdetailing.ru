(() => {
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.date-range-field').forEach(field => {
      const input = field.querySelector('input[type="date"]');
      const value = field.querySelector('.date-range-value');
      if (!input || !value) return;
      const update = () => {
        const parts = input.value.split('-');
        value.textContent = parts.length === 3 ? `${parts[2]}.${parts[1]}.${parts[0]}` : 'дд.мм.гггг';
        field.classList.toggle('is-empty', !input.value);
      };
      update();
      field.classList.add('is-enhanced');
      input.addEventListener('input', update);
      input.addEventListener('change', update);
      input.addEventListener('click', () => {
        try { input.showPicker?.(); } catch { /* Native date input remains usable. */ }
      });
      input.form?.addEventListener('reset', () => requestAnimationFrame(update));
    });
  });
})();
