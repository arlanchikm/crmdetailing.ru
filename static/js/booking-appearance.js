(() => {
  const data = document.getElementById('booking-appearance-config');
  if (!data) return;
  const initial = JSON.parse(data.textContent);
  const editor = document.getElementById('booking-appearance-editor');
  const preview = document.getElementById('booking-appearance-preview');
  const colorNames = ['accent_color', 'page_color', 'block_color', 'text_color'];
  const buttonText = hex => {
    const rgb = hex.slice(1).match(/../g).map(value => parseInt(value, 16) / 255).map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
    return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722 > .179 ? '#102C51' : '#FFFFFF';
  };
  const paint = config => {
    const root = document.body.style;
    colorNames.forEach(name => { if (/^#[a-f\d]{6}$/i.test(config[name])) root.setProperty(`--booking-${name.replaceAll('_', '-')}`, config[name]); });
    root.setProperty('--booking-button-text', buttonText(config.accent_color));
    root.setProperty('--booking-overlay', Math.max(0, Math.min(100, Number(config.overlay) || 0)) / 100);
    const url = config.background_url;
    const validUrl = typeof url === 'string' && (url.startsWith('/') && !url.startsWith('//') || url.startsWith(`blob:${location.origin}/`));
    root.setProperty('--booking-background-image', validUrl ? `url(${JSON.stringify(url)})` : 'none');
  };
  if (!editor) {
    paint(initial);
    if (document.body.dataset.bookingPreview === 'true') {
      document.getElementById('online-booking')?.addEventListener('submit', event => event.preventDefault());
      window.addEventListener('message', event => {
        if (event.origin !== location.origin || event.source !== window.parent || event.data?.type !== 'booking-appearance') return;
        paint({...initial, ...event.data.config});
      });
    }
    return;
  }
  let backgroundUrl = initial.background_url, objectUrl;
  const update = () => {
    const config = {...initial};
    colorNames.forEach(name => { if (editor.elements[name]) config[name] = editor.elements[name].value; });
    config.overlay = editor.elements.overlay.value;
    config.background_url = editor.elements.remove_background.checked ? initial.default_background_url : backgroundUrl;
    preview.contentWindow.postMessage({type: 'booking-appearance', config}, location.origin);
  };
  editor.addEventListener('input', update);
  editor.elements.background.addEventListener('change', () => {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    const file = editor.elements.background.files[0];
    objectUrl = file ? URL.createObjectURL(file) : undefined;
    backgroundUrl = objectUrl || initial.background_url;
    if (file) editor.elements.remove_background.checked = false;
    update();
  });
  preview.addEventListener('load', update);
  window.addEventListener('beforeunload', () => { if (objectUrl) URL.revokeObjectURL(objectUrl); });
})();
