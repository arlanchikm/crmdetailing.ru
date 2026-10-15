(function () {
  const form = document.querySelector('[data-legal-form]');
  if (!form) return;
  const editor = form.querySelector('[data-legal-editor]');
  const input = form.querySelector('[data-legal-input]');
  const sync = () => { input.value = editor.innerHTML; };
  editor.addEventListener('input', sync);
  form.addEventListener('submit', sync);
  form.querySelectorAll('[data-legal-command]').forEach(button => {
    button.addEventListener('mousedown', event => event.preventDefault());
    button.addEventListener('click', () => {
      editor.focus();
      document.execCommand(button.dataset.legalCommand, false, button.dataset.legalValue || null);
      sync();
    });
  });
})();
