(() => {
  const t = window.crmT || ((text) => text);
  function enhanceSelect(select) {
    if (select.dataset.enhanced === 'true' || select.multiple || select.closest('.booking-native')) return;
    select.dataset.enhanced = 'true';
    const wrapper = document.createElement('div');
    wrapper.className = 'custom-select';
    select.parentNode.insertBefore(wrapper, select);
    wrapper.appendChild(select);
    select.classList.add('enhanced-native-select');
    select.tabIndex=-1;select.setAttribute('aria-hidden','true');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'custom-select-button';
    button.setAttribute('aria-haspopup', 'listbox');
    button.setAttribute('aria-expanded','false');
    const fieldLabel=document.querySelector(`label[for="${select.id}"]`);
    button.setAttribute('aria-label',fieldLabel?.textContent||select.name);
    if(select.id){button.id=select.id+'-button';if(fieldLabel)fieldLabel.htmlFor=button.id;}
    const menu = document.createElement('div');
    menu.className = 'custom-select-menu';
    menu.setAttribute('role', 'listbox');
    menu.hidden = true;
    wrapper.append(button, menu);
    wrapper.selectMenu = menu;
    const downward = !!select.closest('.complaint-status-form');
    let menuSpace;

    function positionMenu() {
      const rect = button.getBoundingClientRect();
      if (downward) {
        wrapper.classList.remove('opens-up');
        menu.classList.add('select-menu-portal');
        document.body.appendChild(menu);
        const width = Math.min(Math.max(rect.width, 140), window.innerWidth - 24);
        Object.assign(menu.style, {position:'fixed', left:`${Math.max(12, Math.min(rect.left, window.innerWidth-width-12))}px`, right:'auto', bottom:'auto', top:`${Math.min(rect.bottom+6, window.innerHeight-12)}px`, width:`${width}px`, maxHeight:`${Math.max(0, Math.min(260, window.innerHeight-rect.bottom-18))}px`});
        return;
      }
      let top = 8, bottom = window.innerHeight - 8;
      for (let parent = wrapper.parentElement; parent; parent = parent.parentElement) {
        if (/(auto|scroll|hidden)/.test(getComputedStyle(parent).overflowY)) {
          const bounds = parent.getBoundingClientRect();
          top = Math.max(top, bounds.top + 8);
          bottom = Math.min(bottom, bounds.bottom - 8);
        }
      }
      const below = bottom - rect.bottom - 6;
      const above = rect.top - top - 6;
      const opensUp = below < Math.min(menu.scrollHeight, 220) && above > below;
      wrapper.classList.toggle('opens-up', opensUp);
      menu.style.maxHeight = `${Math.max(70, Math.min(260, opensUp ? above : below))}px`;
    }

    function selectedLabel() {
      const label = select.selectedOptions[0]?.text || t('Выберите');
      button.textContent = /^-+$/.test(label) ? t('Выберите') : t(label);
      button.disabled = select.disabled;
    }
    function buildMenu() {
      menu.innerHTML = '';
      Array.from(select.options).forEach((option) => {
        if (option.hidden) return;
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'custom-select-option';
        item.textContent = /^-+$/.test(option.text) ? t('Выберите') : t(option.text);
        item.disabled = option.disabled;
        item.dataset.value = option.value;
        item.setAttribute('role', 'option');
        item.setAttribute('aria-selected', String(option.selected));
        item.addEventListener('click', () => {
          select.value = option.value;
          select.dispatchEvent(new Event('change', {bubbles: true}));
          menu.hidden = true;
          wrapper.classList.remove('open');
          button.setAttribute('aria-expanded','false');button.focus();
          selectedLabel();
        });
        menu.appendChild(item);
      });
      if (select.dataset.addUrl) {
        const add = document.createElement('button');
        add.type = 'button'; add.className = 'custom-select-option custom-select-add';
        add.textContent = t(select.dataset.addLabel || '+ Добавить');
        add.addEventListener('click', () => {
          menu.hidden = true; wrapper.classList.remove('open');
          select.dispatchEvent(new CustomEvent('custom-select:add', {bubbles: true, detail: {url: select.dataset.addUrl}}));
        });
        menu.appendChild(add);
      }
    }
    button.addEventListener('click', () => {
      document.querySelectorAll('.custom-select.open').forEach((other) => {
        if (other !== wrapper) { other.classList.remove('open'); (other.selectMenu || other.querySelector('.custom-select-menu')).hidden = true;other.querySelector('.custom-select-button').setAttribute('aria-expanded','false'); }
      });
      selectedLabel();
      buildMenu();
      menu.hidden = !menu.hidden;
      wrapper.classList.toggle('open', !menu.hidden);
      button.setAttribute('aria-expanded',String(!menu.hidden));
      if (!menu.hidden) {
        if (downward) {
          const rect=button.getBoundingClientRect(), desired=Math.min(menu.scrollHeight || 150, 180);
          const deficit = Math.max(0, desired - (window.innerHeight-rect.bottom-18));
          const remaining = document.documentElement.scrollHeight - window.innerHeight - window.scrollY;
          if (deficit > remaining) {
            menuSpace = document.createElement('div'); menuSpace.style.height = `${deficit-remaining+12}px`;
            menuSpace.className = 'select-menu-space'; document.body.appendChild(menuSpace);
          }
          if (deficit) window.scrollBy({top:deficit,behavior:'instant'});
        }
        positionMenu();
      }
    });
    if (downward) {
      new MutationObserver(() => { if (menu.hidden) { menuSpace?.remove(); menuSpace = undefined; } }).observe(menu,{attributes:true,attributeFilter:['hidden']});
      window.addEventListener('scroll', () => { if (!menu.hidden) positionMenu(); }, true);
      window.addEventListener('resize', () => { if (!menu.hidden) positionMenu(); });
    }
    select.addEventListener('change', selectedLabel);
    new MutationObserver(selectedLabel).observe(select,{childList:true,subtree:true,attributes:true});
    wrapper.addEventListener('keydown',event=>{
      if(event.key==='Escape'){menu.hidden=true;wrapper.classList.remove('open');button.setAttribute('aria-expanded','false');button.focus();}
      if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){
        event.preventDefault();if(menu.hidden)button.click();
        const choices=[...menu.querySelectorAll('button:not(:disabled)')],index=choices.indexOf(document.activeElement);
        const next=event.key==='Home'?0:event.key==='End'?choices.length-1:event.key==='ArrowUp'?Math.max(0,index-1):Math.min(choices.length-1,index+1);
        choices[next]?.focus();
      }
    });
    selectedLabel();
  }

  window.enhanceSelects = (root = document) => {
    window.initPhoneInputs?.(root);
    root.querySelectorAll('select').forEach(enhanceSelect);
    root.querySelectorAll('input[type="number"]').forEach(input => {
      if (/^-?\d+\.\d+$/.test(input.value)) input.value = input.value.replace(/0+$/, '').replace(/\.$/, '');
    });
  };

  window.initAssigneePickers = (root = document) => root.querySelectorAll('[data-assignee-picker]').forEach((picker) => {
    if (picker.dataset.initialized) return;
    picker.dataset.initialized = 'true';
    const panel = picker.querySelector('[data-assignee-panel]');
    const label = picker.querySelector('[data-assignee-label]');
    const splitPanel = picker.querySelector('[data-split-panel]');
    const customSplit = picker.querySelector('[data-custom-split]');
    const checks = Array.from(picker.querySelectorAll('[data-assignee-check]'));
    const update = (resetShares = false) => {
      const selectedServices = Array.from(picker.closest('form')?.querySelectorAll('input[name="services"]:checked') || []).map(input => input.value);
      checks.forEach(check => {
        const row = check.closest('[data-service-ids]');
        const allowed = new Set((row?.dataset.serviceIds || '').split(' ').filter(Boolean));
        const incompatible = row?.dataset.capabilityMode === '1' && selectedServices.some(serviceId => !allowed.has(serviceId));
        check.disabled = incompatible;
        if (incompatible) check.checked = false;
        row?.classList.toggle('is-incompatible', incompatible);
        const warning = row?.querySelector('.assignee-capability-warning');
        if (warning) warning.hidden = !incompatible;
      });
      const selected = checks.filter(check => check.checked);
      label.textContent = selected.length ? selected.map(check => check.closest('label').querySelector('span').childNodes[0].textContent.trim()).join(', ') : t('Выберите исполнителей');
      checks.forEach(check => {
        const row = picker.querySelector(`[data-share-row] input[name="employee_share_${check.value}"]`)?.closest('[data-share-row]');
        if (row) row.hidden = !check.checked;
      });
      if (resetShares && selected.length) {
        const equal = Math.floor(10000 / selected.length) / 100;
        selected.forEach((check, index) => {
          const input = picker.querySelector(`input[name="employee_share_${check.value}"]`);
          if (input) input.value = index === 0 ? (100 - equal * (selected.length - 1)).toFixed(2) : equal.toFixed(2);
        });
      }
    };
    picker.querySelector('[data-assignee-toggle]')?.addEventListener('click', () => panel.hidden = !panel.hidden);
    document.addEventListener('click', (event) => { if (!picker.contains(event.target)) panel.hidden = true; });
    picker.querySelector('[data-split-toggle]')?.addEventListener('click', () => {
      splitPanel.hidden = !splitPanel.hidden;
      customSplit.value = splitPanel.hidden ? '0' : '1';
      update(true);
    });
    checks.forEach(check => check.addEventListener('change', () => update(true)));
    picker.closest('form')?.querySelectorAll('input[name="services"]').forEach(input => {
      input.addEventListener('change', () => update(true));
    });
    update(false);
  });

  window.initAppointmentEdit = (root) => {
    const form = root.querySelector('[data-appointment-edit]');
    if (!form || form.dataset.initialized) return;
    form.dataset.initialized = 'true';
    window.initAssigneePickers(root);
    const client = form.querySelector('#id_client');
    const car = form.querySelector('#id_car');
    const vehicleClass = form.querySelector('#id_car_vehicle_class');
    const start = form.querySelector('#id_start_at');
    const end = form.querySelector('#id_end_at');
    const services = Array.from(form.querySelectorAll('input[name="services"]'));
    const otherPrice = form.querySelector('#id_other_price');
    const otherDuration = form.querySelector('#id_other_duration_minutes');
    const otherFields = form.querySelector('[data-other-service-fields]');
    const comment = form.querySelector('#id_comment');
    const mapNode = root.querySelector('#modal-client-car-map');
    const carMap = mapNode ? JSON.parse(mapNode.textContent) : {};
    const classMapNode = root.querySelector('#modal-car-class-map');
    const carClassMap = classMapNode ? JSON.parse(classMapNode.textContent) : {};
    const priceNode = root.querySelector('#modal-service-price-data');
    const priceData = priceNode ? JSON.parse(priceNode.textContent) : {};
    const durationNode = root.querySelector('#modal-service-duration-data');
    const durationData = durationNode ? JSON.parse(durationNode.textContent) : {};
    const classSelects = Array.from(form.querySelectorAll('.service-class-select'));
    function filterCars() {
      Array.from(car.options).forEach((option) => {
        if (!option.value) return;
        const visible = !client.value || carMap[option.value] === client.value;
        option.hidden = !visible;
        option.disabled = !visible;
      });
      if (car.selectedOptions[0]?.disabled) car.value = '';
    }
    function formatDate(date) {
      const pad = (value) => String(value).padStart(2, '0');
      return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
    }
    function calculate(updateEnd = false) {
      const selected = services.filter((input) => input.checked);
      const hasOther = selected.some((input) => input.dataset.systemKey === 'other');
      if (otherFields) otherFields.hidden = !hasOther;
      if (otherPrice) otherPrice.required = hasOther;
      if (otherDuration) otherDuration.required = hasOther;
      if (comment) comment.required = hasOther;
      const serviceDuration = input => Number(durationData[input.value]?.[input.closest('.service-select-card')?.querySelector('.service-class-select')?.value] ?? input.dataset.duration);
      const minutes = selected.reduce((sum, input) => sum + Number(input.dataset.systemKey === 'other' ? otherDuration?.value || 0 : serviceDuration(input)), 0);
      const servicePrice = (input) => {
        const select = input.closest('.service-select-card')?.querySelector('.service-class-select');
        return Number(priceData[input.value]?.[select?.value] ?? input.dataset.price.replace(',', '.'));
      };
      services.forEach((input) => {
        const card = input.closest('.service-select-card');
        const label = card?.querySelector('[data-service-price-label]');
        const durationLabel = card?.querySelector('[data-service-duration-label]');
        if (durationLabel) durationLabel.textContent = serviceDuration(input);
        const classChoice = card?.querySelector('.service-class-choice');
        if (classChoice && card.dataset.hasClassChoice === '1' && input.dataset.systemKey !== 'other') {
          classChoice.classList.toggle('is-hidden', !input.checked);
        }
        if (label) label.textContent = `${new Intl.NumberFormat('ru-RU').format(servicePrice(input))} ₽`;
      });
      const price = selected.reduce((sum, input) => sum + Number(input.dataset.systemKey === 'other' ? otherPrice?.value || 0 : servicePrice(input)), 0);
      form.querySelector('[data-selected-count]').textContent = selected.length;
      form.querySelector('[data-selected-duration]').textContent = document.documentElement.lang.startsWith('en') ? `${minutes} min.` : `${minutes} мин.`;
      form.querySelector('[data-selected-price]').textContent = `${new Intl.NumberFormat('ru-RU').format(price)} ₽`;
      if (updateEnd && start.value && minutes) { const finish = new Date(start.value); finish.setMinutes(finish.getMinutes() + minutes); end.value = formatDate(finish); }
    }
    client.addEventListener('change', filterCars);
    car.addEventListener('change', () => {
      if (!carClassMap[car.value]) return;
      vehicleClass.value = carClassMap[car.value];
      classSelects.forEach((select) => { select.value = vehicleClass.value; select.dispatchEvent(new Event('change', {bubbles:true})); });
      calculate(true);
    });
    vehicleClass.addEventListener('change', () => {
      classSelects.forEach((select) => { select.value = vehicleClass.value; select.dispatchEvent(new Event('change', {bubbles:true})); });
      calculate(true);
    });
    start.addEventListener('input', () => calculate(true));
    services.forEach((input) => input.addEventListener('change', () => calculate(true)));
    classSelects.forEach((select) => select.addEventListener('change', () => calculate(true)));
    otherPrice?.addEventListener('input', () => calculate(false));
    otherDuration?.addEventListener('input', () => calculate(true));
    filterCars(); calculate(false);
  };

  window.initAppointmentCreate = (root = document) => {
    const form = root.querySelector('[data-appointment-create]');
    if (!form || form.dataset.initialized) return;
    form.dataset.initialized = 'true';
    window.initAssigneePickers(root);
    const suggestionNode = root.querySelector('#client-suggestion-data');
    const priceNode = root.querySelector('#appointment-service-price-data');
    const durationNode = root.querySelector('#appointment-service-duration-data');
    const durationData = durationNode ? JSON.parse(durationNode.textContent) : {};
    const suggestions = suggestionNode ? JSON.parse(suggestionNode.textContent) : [];
    const priceData = priceNode ? JSON.parse(priceNode.textContent) : {};
    const box = form.querySelector('[data-client-suggestions]');
    const fieldNames = ['client_first_name', 'client_last_name', 'client_phone', 'car_plate', 'car_description'];
    const fields = Object.fromEntries(
      [...fieldNames, 'car_make', 'car_model'].map(name => [name, form.querySelector(`#id_${name}`)])
    );
    const clientId = form.querySelector('#id_client');
    const carId = form.querySelector('#id_car');
    const vehicleClass = form.querySelector('#id_car_vehicle_class');
    const normalize = value => (value || '').toLocaleLowerCase('ru').replace(/[\s()+-]/g, '');
    let filling = false;
    const renderMatches = () => {
      const queries = fieldNames.map(name => normalize(fields[name]?.value)).filter(value => value.length >= 2);
      if (!queries.length) { box.hidden = true; box.innerHTML = ''; return; }
      const matches = suggestions.filter(item => queries.every(query => normalize(Object.values(item).join(' ')).includes(query))).slice(0, 8);
      box.innerHTML = '';
      const activeField = document.activeElement;
      const anchor = form.querySelector('[data-client-autocomplete]');
      if (anchor && fieldNames.some(name => fields[name] === activeField)) {
        box.style.top = `${activeField.getBoundingClientRect().bottom - anchor.getBoundingClientRect().top + 6}px`;
      }
      matches.forEach(item => {
        const button = document.createElement('button'); button.type = 'button';
        button.dataset.clientId = item.client_id || '';
        button.dataset.carId = item.car_id || '';
        button.innerHTML = `<strong>${item.label}</strong><small>${item.make || item.model ? `${item.make} ${item.model}` : t('Клиент без автомобиля')}</small>`;
        button.addEventListener('click', () => {
          filling = true;
          const setField = (name, value) => { if (fields[name]) fields[name].value = value || ''; };
          setField('client_first_name', item.first_name);
          setField('client_last_name', item.last_name);
          setField('client_phone', item.phone);
          setField('car_plate', item.plate);
          setField('car_description', item.car_description || `${item.make || ''} ${item.model || ''}`.trim());
          setField('car_make', item.make);
          setField('car_model', item.model);
          clientId.value = item.client_id || '';
          carId.value = item.car_id || '';
          if (vehicleClass) {
            vehicleClass.value = item.vehicle_class_id || '';
            vehicleClass.dispatchEvent(new Event('change', {bubbles: true}));
          }
          filling = false; box.hidden = true;
        });
        box.appendChild(button);
      });
      box.hidden = !matches.length;
    };
    fieldNames.forEach(name => fields[name]?.addEventListener('input', () => {
      if (!filling) { clientId.value = ''; carId.value = ''; renderMatches(); }
    }));
    const syncCarDescription = () => {
      if (carId.value) return;
      const parts = (fields.car_description?.value || '').trim().split(/\s+/);
      fields.car_make.value = parts[0] || '';
      fields.car_model.value = parts.slice(1).join(' ');
    };
    fields.car_description?.addEventListener('input', syncCarDescription);
    form.addEventListener('submit', syncCarDescription);
    form.querySelector('[data-client-clear]')?.addEventListener('click', () => {
      filling = true;
      [...fieldNames, 'car_make', 'car_model'].forEach(name => { if (fields[name]) fields[name].value = ''; });
      clientId.value = '';
      carId.value = '';
      if (vehicleClass) {
        vehicleClass.value = '';
        vehicleClass.dispatchEvent(new Event('change', {bubbles: true}));
      }
      box.hidden = true;
      box.innerHTML = '';
      filling = false;
      fields.client_first_name?.focus();
    });

    const start = form.querySelector('#id_start_at');
    const end = form.querySelector('#id_end_at');
    const services = Array.from(form.querySelectorAll('input[name="services"]'));
    const classSelects = Array.from(form.querySelectorAll('.service-class-select'));
    const otherPrice = form.querySelector('#id_other_price');
    const otherDuration = form.querySelector('#id_other_duration_minutes');
    const otherFields = form.querySelector('[data-other-service-fields]');
    const comment = form.querySelector('#id_comment');
    const discountKind = form.querySelector('#id_discount_kind');
    const discountValue = form.querySelector('#id_discount_value');
    const discountReason = form.querySelector('[data-discount-reason]');
    const freeReason = form.querySelector('[data-free-reason]');
    const formatter = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 2});
    const formatDate = date => {
      const pad = value => String(value).padStart(2, '0');
      return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
    };
    const servicePrice = input => {
      const select = input.closest('.service-select-card')?.querySelector('.service-class-select');
      return Number(priceData[input.value]?.[select?.value] ?? input.dataset.price.replace(',', '.'));
    };
    const serviceQuantity = input => {
      if (input.dataset.calculationType !== 'unit') return 1;
      const field = input.closest('.service-select-card')?.querySelector('[data-service-quantity] input');
      const quantity = Math.max(1, Math.trunc(Number(field?.value) || 1));
      if (field && field.value !== String(quantity)) field.value = quantity;
      return quantity;
    };
    const calculate = (updateEnd = false) => {
      const selected = services.filter(input => input.checked);
      const hasOther = selected.some(input => input.dataset.systemKey === 'other');
      if (otherFields) otherFields.hidden = !hasOther;
      if (otherPrice) otherPrice.required = hasOther;
      if (otherDuration) otherDuration.required = hasOther;
      if (comment) comment.required = hasOther;
      const serviceDuration = input => Number(durationData[input.value]?.[input.closest('.service-select-card')?.querySelector('.service-class-select')?.value] ?? input.dataset.duration);
      const minutes = selected.reduce((sum, input) => sum + Number(
        input.dataset.systemKey === 'other'
          ? otherDuration?.value || 0
          : serviceDuration(input) * serviceQuantity(input)
      ), 0);
      services.forEach(input => {
        const card = input.closest('.service-select-card');
        const label = card?.querySelector('[data-service-price-label]');
        const durationLabel = card?.querySelector('[data-service-duration-label]');
        if (durationLabel) durationLabel.textContent = serviceDuration(input);
        const classChoice = card?.querySelector('.service-class-choice');
        const quantityControl = card?.querySelector('[data-service-quantity]');
        if (quantityControl) quantityControl.classList.toggle('is-hidden', !input.checked);
        if (classChoice && card.dataset.hasClassChoice === '1' && input.dataset.systemKey !== 'other') {
          classChoice.classList.toggle('is-hidden', !input.checked);
        }
        if (label) label.textContent = `${formatter.format(servicePrice(input))} ₽`;
      });
      const subtotal = selected.reduce((sum, input) => sum + Number(
        input.dataset.systemKey === 'other'
          ? otherPrice?.value || 0
          : servicePrice(input) * serviceQuantity(input)
      ), 0);
      const rawDiscount = Math.max(0, Number(discountValue?.value || 0));
      const discount = discountKind?.value === 'percent' ? subtotal * Math.min(rawDiscount, 100) / 100 : discountKind?.value === 'fixed' ? Math.min(rawDiscount, subtotal) : 0;
      const total = Math.max(0, subtotal - discount);
      form.querySelectorAll('[data-selected-count]').forEach(node => { node.textContent = selected.length; });
      form.querySelectorAll('[data-selected-duration]').forEach(node => { node.textContent = document.documentElement.lang.startsWith('en') ? `${minutes} min.` : `${minutes} мин.`; });
      form.querySelectorAll('[data-selected-price]').forEach(node => { node.textContent = `${formatter.format(total)} ₽`; });
      const breakdown = form.querySelector('[data-price-breakdown]');
      if (breakdown) { breakdown.hidden = discount <= 0; breakdown.textContent = document.documentElement.lang.startsWith('en') ? `Before discount ${formatter.format(subtotal)} ₽ · discount ${formatter.format(discount)} ₽` : `До скидки ${formatter.format(subtotal)} ₽ · скидка ${formatter.format(discount)} ₽`; }
      if (discountReason) discountReason.hidden = !(discountKind?.value !== 'none' && rawDiscount > 0);
      if (freeReason) freeReason.hidden = !(selected.length && total === 0);
      if (updateEnd && start?.value && minutes) { const finish = new Date(start.value); finish.setMinutes(finish.getMinutes() + minutes); end.value = formatDate(finish); }
    };
    start?.addEventListener('input', () => calculate(true));
    services.forEach(input => input.addEventListener('change', () => {
      if (!input.checked && input.dataset.calculationType === 'unit') {
        const quantity = input.closest('.service-select-card')?.querySelector('[data-service-quantity] input');
        if (quantity) quantity.value = '1';
      }
      calculate(true);
    }));
    form.querySelectorAll('[data-service-quantity]').forEach(control => {
      const field = control.querySelector('input');
      control.querySelector('[data-quantity-minus]')?.addEventListener('click', () => {
        field.value = Math.max(1, (Math.trunc(Number(field.value)) || 1) - 1);
        calculate(true);
      });
      control.querySelector('[data-quantity-plus]')?.addEventListener('click', () => {
        field.value = (Math.trunc(Number(field.value)) || 1) + 1;
        calculate(true);
      });
      field?.addEventListener('input', () => calculate(true));
      field?.addEventListener('change', () => calculate(true));
    });
    classSelects.forEach(select => select.addEventListener('change', () => calculate(true)));
    otherPrice?.addEventListener('input', () => calculate(false));
    otherDuration?.addEventListener('input', () => calculate(true));
    discountKind?.addEventListener('change', () => calculate(false));
    discountValue?.addEventListener('input', () => calculate(false));
    vehicleClass?.addEventListener('change', () => {
      classSelects.forEach(select => { select.value = vehicleClass.value; select.dispatchEvent(new Event('change', {bubbles: true})); });
      calculate(false);
    });
    calculate(false);
  };

  window.initCalendar = (root = document) => {
    const board = root.querySelector('[data-calendar-board]');
    if (!board || board.dataset.initialized) return;
    board.dataset.initialized = 'true';
    const container = document.querySelector('[data-calendar-container]');
    const storageKey = 'crm-calendar-resource-kind';
    const createUrl = board.dataset.createUrl;
    const resourceKind = board.dataset.resourceKind;
    const csrf = board.querySelector('[data-calendar-csrf] input[name="csrfmiddlewaretoken"]')?.value;
    const selectedDateValue = board.dataset.selectedDate;
    const localKind = localStorage.getItem(storageKey);
    const toISODate = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;

    const refreshBoard = async (date = selectedDateValue, kind = resourceKind, history = true) => {
      if (!container) return;
      const params = new URLSearchParams({date, resource_kind: kind, partial: '1'});
      container.setAttribute('aria-busy', 'true');
      try {
        const response = await fetch(`${window.location.pathname}?${params}`, {headers: {'X-Requested-With': 'XMLHttpRequest'}});
        if (!response.ok) throw new Error('calendar');
        container.innerHTML = await response.text();
        localStorage.setItem(storageKey, kind);
        if (history) {
          const url = new URL(window.location.href);
          url.searchParams.set('date', date);
          url.searchParams.delete('resource_kind');
          url.searchParams.delete('partial');
          window.history.replaceState({}, '', url);
        }
        window.initCalendar(container);
      } catch (_) {
        window.location.assign(`${window.location.pathname}?date=${date}`);
      } finally {
        container.removeAttribute('aria-busy');
      }
    };
    window.refreshCalendarBoard = () => {
      const current = document.querySelector('[data-calendar-board]');
      if (current) return refreshBoard(current.dataset.selectedDate, current.dataset.resourceKind, false);
    };

    if (localKind && localKind !== resourceKind && ['employee', 'post'].includes(localKind)) {
      refreshBoard(selectedDateValue, localKind, false);
      return;
    }
    board.querySelectorAll('[data-calendar-resource-kind]').forEach(button => button.addEventListener('click', () => {
      const kind = button.dataset.calendarResourceKind;
      if (kind !== resourceKind) refreshBoard(selectedDateValue, kind);
    }));
    board.querySelectorAll('[data-calendar-date-link]').forEach(link => link.addEventListener('click', event => {
      event.preventDefault();
      const url = new URL(link.href, window.location.href);
      refreshBoard(url.searchParams.get('date') || toISODate(new Date()), resourceKind);
    }));

    const dateTrigger = board.querySelector('[data-date-trigger]');
    const datePopover = board.querySelector('[data-date-popover]');
    const dateDays = board.querySelector('[data-date-days]');
    const monthLabel = board.querySelector('[data-month-label]');
    const selectedDate = new Date(`${selectedDateValue}T12:00:00`);
    let shownMonth = new Date(selectedDate.getFullYear(), selectedDate.getMonth(), 1);
    const renderDatePicker = () => {
      if (!dateDays) return;
      monthLabel.textContent = new Intl.DateTimeFormat(document.documentElement.lang.startsWith('en') ? 'en-US' : 'ru-RU', {month: 'long', year: 'numeric'}).format(shownMonth);
      dateDays.innerHTML = '';
      const firstOffset = (shownMonth.getDay() + 6) % 7;
      const first = new Date(shownMonth.getFullYear(), shownMonth.getMonth(), 1 - firstOffset);
      for (let offset = 0; offset < 42; offset += 1) {
        const date = new Date(first); date.setDate(first.getDate() + offset);
        const button = document.createElement('button'); button.type = 'button'; button.textContent = date.getDate();
        button.className = 'date-day';
        if (date.getMonth() !== shownMonth.getMonth()) button.classList.add('outside');
        if (toISODate(date) === selectedDateValue) button.classList.add('selected');
        if (toISODate(date) === toISODate(new Date())) button.classList.add('today');
        button.addEventListener('click', () => refreshBoard(toISODate(date), resourceKind));
        dateDays.appendChild(button);
      }
    };
    const positionDatePopover = () => {
      if (!datePopover || window.innerWidth > 760 || datePopover.hidden) return;
      const triggerRect = dateTrigger.getBoundingClientRect();
      const viewportHeight = window.visualViewport?.height || window.innerHeight;
      const naturalTop = triggerRect.bottom + 8;
      const popoverHeight = datePopover.offsetHeight;
      const top = Math.max(12, Math.min(naturalTop, viewportHeight - popoverHeight - 12));
      datePopover.style.setProperty('--mobile-date-popover-top', `${top}px`);
    };
    dateTrigger?.addEventListener('click', event => {
      event.preventDefault();
      renderDatePicker();
      datePopover.hidden = !datePopover.hidden;
      dateTrigger.setAttribute('aria-expanded', datePopover.hidden ? 'false' : 'true');
      if (!datePopover.hidden) requestAnimationFrame(positionDatePopover);
    });
    window.__positionCalendarDatePopover = positionDatePopover;
    if (!window.__calendarPopoverPositionListeners) {
      window.__calendarPopoverPositionListeners = true;
      window.visualViewport?.addEventListener('resize', () => window.__positionCalendarDatePopover?.());
      window.addEventListener('orientationchange', () => window.__positionCalendarDatePopover?.());
    }
    board.querySelector('[data-month-prev]')?.addEventListener('click', () => { shownMonth = new Date(shownMonth.getFullYear(), shownMonth.getMonth() - 1, 1); renderDatePicker(); });
    board.querySelector('[data-month-next]')?.addEventListener('click', () => { shownMonth = new Date(shownMonth.getFullYear(), shownMonth.getMonth() + 1, 1); renderDatePicker(); });
    if (!window.__calendarOutsideClick) {
      window.__calendarOutsideClick = true;
      document.addEventListener('click', (event) => {
        const current = document.querySelector('[data-date-popover]');
        if (current && !event.target.closest('.date-picker-wrap')) {
          current.hidden = true;
          document.querySelector('[data-date-trigger]')?.setAttribute('aria-expanded', 'false');
        }
      });
    }

    const updateCurrentTime = () => {
      const lines = board.querySelectorAll('[data-current-time]');
      if (!lines.length) return;
      const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {timeZone: board.dataset.timezone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'}).formatToParts(new Date()).filter(part => part.type !== 'literal').map(part => [part.type, part.value]));
      const date = `${parts.year}-${parts.month}-${parts.day}`;
      const minutes = Number(parts.hour) * 60 + Number(parts.minute);
      const opening = Number(board.querySelector('.planner-column')?.dataset.openingMinutes || 480);
      const closing = Number(board.querySelector('.planner-column')?.dataset.closingMinutes || 1320);
      lines.forEach(line => {
      line.hidden = date !== selectedDateValue || minutes < opening || minutes > closing;
      if (!line.hidden) {
        const ppm = Number(board.querySelector('.planner-column')?.dataset.pixelsPerMinute || 1.6);
        line.style.top = `${(minutes - opening) * ppm}px`;
        line.querySelector('[data-current-time-label]').textContent = `${parts.hour}:${parts.minute}`;
      }
      });
    };
    updateCurrentTime();
    clearInterval(window.__calendarClock); window.__calendarClock = window.setInterval(updateCurrentTime, 60000);

    const modal = document.getElementById('appointment-modal');
    const panel = modal?.querySelector('.modal-panel');
    const loadModal = async (url) => {
      if (!modal || !panel) return;
      if (modal.hidden) delete modal.dataset.refresh;
      modal.hidden = false; document.body.classList.add('modal-open'); panel.innerHTML = `<div class="modal-loading">${t('Загрузка…')}</div>`;
      const response = await fetch(url, {headers: {'X-Requested-With': 'XMLHttpRequest'}}); panel.innerHTML = await response.text();
      window.enhanceSelects?.(panel); window.initAppointmentEdit?.(panel); window.initAppointmentCreate?.(panel);
      if (panel.querySelector('[data-appointment-created]')) modal.dataset.refresh = '1';
    };
    window.openAppointmentModal = loadModal;
    if (modal && !modal.dataset.calendarInitialized) {
      modal.dataset.calendarInitialized = 'true';
      const closeModal = () => {
        const shouldRefresh = modal.dataset.refresh === '1' || Boolean(panel?.querySelector('[data-appointment-created]'));
        modal.hidden = true; document.body.classList.remove('modal-open'); delete modal.dataset.refresh;
        if (shouldRefresh) window.refreshCalendarBoard?.();
      };
      modal.addEventListener('click', event => {
        if (event.target === modal || event.target.closest('[data-modal-close]')) { closeModal(); return; }
        const history = event.target.closest('[data-history-toggle]');
        if (history) { const target = panel.querySelector('[data-history-panel]'); target.hidden = !target.hidden; history.textContent = target.hidden ? t('История клиента') : t('Скрыть историю'); return; }
        const link = event.target.closest('.js-modal-load'); if (link) { event.preventDefault(); window.openAppointmentModal(link.dataset.url || link.href); }
      });
      modal.addEventListener('submit', async event => {
        if (event.defaultPrevented) return;
        const form = event.target.closest('.js-modal-form'); if (!form) return; event.preventDefault();
        const response = await fetch(form.action, {method: 'POST', body: new FormData(form), headers: {'X-Requested-With': 'XMLHttpRequest'}});
        panel.innerHTML = await response.text(); window.enhanceSelects?.(panel); window.initAppointmentEdit?.(panel); window.initAppointmentCreate?.(panel);
        if (response.ok) modal.dataset.refresh = '1';
      });
      document.addEventListener('keydown', event => { if (event.key === 'Escape' && !modal.hidden) closeModal(); });
    }
    document.querySelectorAll('.js-appointment-create').forEach(link => {
      if (link.dataset.calendarInitialized) return; link.dataset.calendarInitialized = 'true';
      link.addEventListener('click', event => { event.preventDefault(); window.openAppointmentModal(link.dataset.modalUrl || link.href); });
    });

    let dragged = null, wasDragging = false;
    board.querySelectorAll('.planner-column.is-clickable').forEach((column) => {
      const hover = column.querySelector('.slot-hover');
      const label = hover?.querySelector('span');
      const ppm = Number(column.dataset.pixelsPerMinute);
      const openingMinutes = Number(column.dataset.openingMinutes || Number(column.dataset.openingHour) * 60);
      const closingMinutes = Number(column.dataset.closingMinutes || Number(column.dataset.closingHour) * 60);
      const slotMinutes = 15;
      const slotFromPointer = (event) => Math.max(0, Math.min(Math.floor(((event.clientY - column.getBoundingClientRect().top) / ppm) / slotMinutes) * slotMinutes, closingMinutes - openingMinutes - slotMinutes));
      const formatTime = (offset) => { const total = openingMinutes + offset; return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`; };
      const slotIsAvailable = (offset, duration = slotMinutes) => {
        if (column.dataset.resourceDisabled || openingMinutes + offset + Number(duration) > Number(column.dataset.closingMinutes)) return false;
        if (!column.dataset.shiftStart) return true;
        const absoluteStart = openingMinutes + offset;
        return absoluteStart >= Number(column.dataset.shiftStart) && absoluteStart + Number(duration) <= Number(column.dataset.shiftEnd);
      };
      column.addEventListener('mousemove', (event) => {
        if (!hover || event.target.closest('.booking-card')) { if (hover) hover.hidden = true; return; }
        const offset = slotFromPointer(event); if (!slotIsAvailable(offset)) { hover.hidden = true; return; }
        hover.hidden = false; hover.style.top = `${offset * ppm}px`; hover.style.height = `${slotMinutes * ppm}px`;
        label.textContent = document.documentElement.lang.startsWith('en') ? `＋ Book at ${formatTime(offset)}` : `＋ Записать на ${formatTime(offset)}`;
      });
      column.addEventListener('mouseleave', () => { if (hover) hover.hidden = true; });
      column.addEventListener('click', (event) => {
        if (event.target.closest('.booking-card') || dragged) return;
        const offset = slotFromPointer(event); if (!slotIsAvailable(offset)) return;
        const params = new URLSearchParams({date: column.dataset.date, start: formatTime(offset), modal: '1'}); params.set(resourceKind, column.dataset.resource);
        window.openAppointmentModal(`${createUrl}?${params}`);
      });
      column.addEventListener('dragover', (event) => {
        if (!dragged) return; event.preventDefault(); const offset = slotFromPointer(event);
        if (!slotIsAvailable(offset, dragged.dataset.duration)) { if (hover) hover.hidden = true; return; }
        column.classList.add('drag-target'); if (hover) { hover.hidden = false; hover.style.top = `${offset * ppm}px`; hover.style.height = `${dragged.dataset.duration * ppm}px`; label.textContent = document.documentElement.lang.startsWith('en') ? `Move to ${formatTime(offset)}` : `Перенести на ${formatTime(offset)}`; }
      });
      column.addEventListener('dragleave', () => column.classList.remove('drag-target'));
      column.addEventListener('drop', async (event) => {
        if (!dragged) return; event.preventDefault(); const offset = slotFromPointer(event); if (!slotIsAvailable(offset, dragged.dataset.duration)) return;
        const data = new FormData(); data.set('csrfmiddlewaretoken', csrf); data.set('resource_kind', resourceKind); data.set(resourceKind, column.dataset.resource); data.set('date', column.dataset.date); data.set('start', formatTime(offset));
        if (resourceKind === 'employee' && dragged.dataset.assignment) data.set('assignment', dragged.dataset.assignment);
        const response = await fetch(dragged.dataset.moveUrl, {method: 'POST', body: data, headers: {'X-Requested-With': 'XMLHttpRequest'}});
        if (response.ok) window.refreshCalendarBoard?.(); else { const result = await response.json(); alert(t(result.error || 'Не удалось перенести запись.')); }
      });
    });
    board.querySelectorAll('.booking-card[draggable="true"]').forEach(card => {
      card.addEventListener('dragstart', event => { dragged = card; wasDragging = true; card.classList.add('is-dragging'); event.dataTransfer.effectAllowed = 'move'; });
      card.addEventListener('dragend', () => { card.classList.remove('is-dragging'); dragged = null; board.querySelectorAll('.drag-target').forEach(node => node.classList.remove('drag-target')); setTimeout(() => { wasDragging = false; }, 180); });
      card.addEventListener('click', event => { if (dragged || wasDragging) { event.preventDefault(); return; } event.preventDefault(); window.openAppointmentModal(card.dataset.modalUrl); });
    });
  };

  window.initPhotoLightbox = (root = document) => {
    const lightbox = document.querySelector('[data-damage-lightbox]');
    const image = lightbox?.querySelector('[data-lightbox-image]');
    if (!lightbox || !image) return;
    const buttons = Array.from(root.querySelectorAll('[data-damage-photo], [data-complaint-photo]'));
    buttons.forEach(button => {
      if (button.dataset.lightboxReady) return;
      button.dataset.lightboxReady = '1';
      button.addEventListener('click', () => {
        const current = buttons.indexOf(button);
        lightbox.dataset.index = String(Math.max(current, 0));
        image.src = button.dataset.src;
        lightbox.hidden = false;
      });
    });
    if (lightbox.dataset.controlsReady) return;
    lightbox.dataset.controlsReady = '1';
    const close = () => { lightbox.hidden = true; image.src = ''; };
    const show = delta => {
      const all = Array.from(document.querySelectorAll('[data-damage-photo], [data-complaint-photo]'));
      if (!all.length) return;
      const index = (Number(lightbox.dataset.index || 0) + delta + all.length) % all.length;
      lightbox.dataset.index = String(index); image.src = all[index].dataset.src;
    };
    lightbox.querySelector('[data-lightbox-close]')?.addEventListener('click', close);
    lightbox.querySelector('[data-lightbox-previous]')?.addEventListener('click', event => { event.stopPropagation(); show(-1); });
    lightbox.querySelector('[data-lightbox-next]')?.addEventListener('click', event => { event.stopPropagation(); show(1); });
    image.addEventListener('click', close);
    lightbox.addEventListener('click', event => { if (event.target === lightbox) close(); });
  };

  window.initComplaintList = () => {
    const modal = document.getElementById('complaint-modal');
    const panel = modal?.querySelector('.modal-panel');
    if (!modal || !panel || modal.dataset.initialized) return;
    modal.dataset.initialized = '1';
    const close = () => { modal.hidden = true; document.body.classList.remove('modal-open'); };
    const load = async url => {
      modal.hidden = false; document.body.classList.add('modal-open');
      panel.innerHTML = `<div class="modal-loading">${t('Загрузка…')}</div>`;
      const response = await fetch(url, {headers: {'X-Requested-With': 'XMLHttpRequest'}});
      panel.innerHTML = await response.text(); window.initPhotoLightbox(panel);
    };
    document.querySelectorAll('.js-complaint-modal').forEach(link => link.addEventListener('click', event => {
      event.preventDefault(); load(`${link.href}?modal=1`);
    }));
    document.querySelectorAll('.complaint-row').forEach(row => row.addEventListener('click', event => {
      if (event.target.closest('a,button,form')) return; load(row.dataset.complaintUrl);
    }));
    document.querySelectorAll('.complaint-status-form select').forEach(select => {
      if (select.dataset.statusReady) return;
      select.dataset.statusReady = 'true';
      let committedValue = select.value;
      select.addEventListener('change', async () => {
        const form = select.closest('form'); const saved = form.querySelector('.complaint-status-saved');
        const payload = new FormData(form);
        select.disabled = true; if (saved) saved.textContent = '';
        try {
          const response = await fetch(form.action, {method: 'POST', body: payload, headers: {'X-Requested-With': 'XMLHttpRequest'}});
          if (!response.ok) throw new Error('status');
          committedValue = select.value;
          if (saved) { saved.textContent = document.documentElement.lang.startsWith('en') ? 'Saved' : 'Сохранено'; setTimeout(() => { saved.textContent = ''; }, 1500); }
        } catch (_) {
          select.value = committedValue;
          const selectedText = select.selectedOptions[0]?.textContent;
          const customButton = select.closest('.custom-select')?.querySelector('.custom-select-button');
          if (customButton && selectedText) customButton.textContent = selectedText;
          if (saved) saved.textContent = document.documentElement.lang.startsWith('en') ? 'Error' : 'Ошибка';
        } finally {
          select.disabled = false;
          const customButton = select.closest('.custom-select')?.querySelector('.custom-select-button');
          if (customButton) customButton.disabled = false;
        }
      });
    });
    modal.addEventListener('click', event => { if (event.target === modal || event.target.closest('[data-complaint-close]')) close(); });
  };

  document.addEventListener('click', (event) => {
    const copyFeedback = event.target.closest('[data-copy-feedback]');
    if (copyFeedback) {
      const input = copyFeedback.closest('.copy-link-row')?.querySelector('input');
      if (input) navigator.clipboard.writeText(input.value).then(() => { copyFeedback.textContent = t('Скопировано'); });
    }
    if (!event.target.closest('.custom-select, .select-menu-portal')) document.querySelectorAll('.custom-select.open').forEach((node) => { node.classList.remove('open'); (node.selectMenu || node.querySelector('.custom-select-menu')).hidden = true; node.querySelector('.custom-select-button').setAttribute('aria-expanded','false'); });
  });

  function initRevenueLineChart() {
    const container = document.querySelector('[data-revenue-line-chart]');
    const source = document.getElementById('revenue-line-chart-data');
    if (!container || !source || container.dataset.ready === 'true') return;
    let rows;
    try { rows = JSON.parse(source.textContent); } catch { return; }
    if (!Array.isArray(rows) || !rows.length) return;
    container.dataset.ready = 'true';
    const shortMonths = ['янв.', 'февр.', 'мар.', 'апр.', 'мая', 'июн.', 'июл.', 'авг.', 'сен.', 'окт.', 'нояб.', 'дек.'];
    const longMonths = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
    const compactNumber = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 1});
    const integerNumber = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 0});
    const formatMoney = value => {
      const absolute = Math.abs(value);
      if (absolute >= 1000000) return `${compactNumber.format(value / 1000000)} млн ₽`;
      if (absolute >= 1000) return `${compactNumber.format(value / 1000)} тыс. ₽`;
      return `${integerNumber.format(value)} ₽`;
    };
    const parseDate = value => {
      const [year, month, day] = String(value || '').split('-').map(Number);
      return new Date(year, month - 1, day, 12);
    };
    const dateKey = value => [value.getFullYear(), String(value.getMonth() + 1).padStart(2, '0'), String(value.getDate()).padStart(2, '0')].join('-');
    const dayLabel = value => `${String(value.getDate()).padStart(2, '0')}.${String(value.getMonth() + 1).padStart(2, '0')}`;
    const fullDateLabel = value => `${value.getDate()} ${longMonths[value.getMonth()]}`;
    const rangeLabel = (start, end, long = false) => {
      const months = long ? longMonths : shortMonths;
      if (dateKey(start) === dateKey(end)) return `${start.getDate()} ${months[start.getMonth()]}`;
      if (start.getMonth() === end.getMonth() && start.getFullYear() === end.getFullYear()) {
        return `${start.getDate()}–${end.getDate()} ${months[end.getMonth()]}`;
      }
      return `${start.getDate()} ${months[start.getMonth()]}–${end.getDate()} ${months[end.getMonth()]}`;
    };
    const dailyPoints = rows.map(row => {
      const pointDate = parseDate(row.date);
      return {
        date: pointDate,
        start: pointDate,
        end: pointDate,
        label: dayLabel(pointDate),
        tooltip: fullDateLabel(pointDate),
        value: Math.max(0, Number(row.amount) || 0),
      };
    }).sort((left, right) => left.date - right.date);
    const weeklyPoints = () => {
      const weeks = new Map();
      dailyPoints.forEach(point => {
        const monday = new Date(point.date);
        monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7));
        const key = dateKey(monday);
        if (!weeks.has(key)) weeks.set(key, {start: point.date, end: point.date, value: 0});
        const week = weeks.get(key);
        week.end = point.date;
        week.value += point.value;
      });
      return [...weeks.values()].map(week => ({
        ...week,
        date: week.start,
        label: rangeLabel(week.start, week.end),
        tooltip: rangeLabel(week.start, week.end, true),
      }));
    };

    const render = () => {
      const mobile = window.matchMedia('(max-width: 760px)').matches;
      const aggregateByWeek = dailyPoints.length > (mobile ? 14 : 30);
      const points = aggregateByWeek ? weeklyPoints() : dailyPoints;
      const width = Math.max(320, Math.round(container.getBoundingClientRect().width || 640));
      const height = mobile ? 270 : Math.min(380, Math.max(300, Math.round(width * .35)));
      const padding = {top: 72, right: mobile ? 12 : 22, bottom: 48, left: mobile ? 72 : 108};
      const plotWidth = width - padding.left - padding.right;
      const plotHeight = height - padding.top - padding.bottom;
      const maximum = Math.max(0, ...points.map(point => point.value));
      const yMaximum = maximum || 1;
      const xAt = index => points.length === 1 ? padding.left + plotWidth / 2 : padding.left + plotWidth * index / (points.length - 1);
      const yAt = value => padding.top + plotHeight - (value / yMaximum) * plotHeight;
      const baseline = padding.top + plotHeight;
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
      svg.setAttribute('aria-hidden', 'true');
      svg.setAttribute('focusable', 'false');
      const add = (name, attributes, text, parent = svg) => {
        const node = document.createElementNS('http://www.w3.org/2000/svg', name);
        Object.entries(attributes || {}).forEach(([key, value]) => node.setAttribute(key, value));
        if (text !== undefined) node.textContent = text;
        parent.appendChild(node);
        return node;
      };

      const definitions = add('defs');
      const gradient = add('linearGradient', {id: 'revenue-chart-fill', x1: '0', y1: '0', x2: '0', y2: '1'}, undefined, definitions);
      add('stop', {offset: '0%', class: 'revenue-chart-area-stop-top'}, undefined, gradient);
      add('stop', {offset: '100%', class: 'revenue-chart-area-stop-bottom'}, undefined, gradient);

      const coordinates = points.map((point, index) => [xAt(index), yAt(point.value)]);
      coordinates.forEach(([x]) => add('line', {x1: x, y1: padding.top, x2: x, y2: baseline, class: 'revenue-chart-grid revenue-chart-grid-vertical'}));
      [0, .5, 1].forEach(ratio => {
        const y = padding.top + plotHeight * (1 - ratio);
        add('line', {x1: padding.left, y1: y, x2: width - padding.right, y2: y, class: 'revenue-chart-grid revenue-chart-grid-horizontal'});
        const label = maximum || ratio === 0 ? formatMoney(maximum * ratio) : '';
        if (label) add('text', {x: padding.left - 12, y: y + 4, class: 'revenue-chart-y-label', 'text-anchor': 'end'}, label);
      });

      if (coordinates.length > 1) {
        const areaPath = [`M ${coordinates[0][0]} ${baseline}`, ...coordinates.map(([x, y]) => `L ${x} ${y}`), `L ${coordinates[coordinates.length - 1][0]} ${baseline}`, 'Z'].join(' ');
        add('path', {d: areaPath, class: 'revenue-chart-area'});
        add('polyline', {points: coordinates.map(pair => pair.join(',')).join(' '), class: 'revenue-chart-line'});
      }

      const tooltip = document.createElement('div');
      tooltip.className = 'revenue-chart-tooltip';
      tooltip.hidden = true;
      tooltip.innerHTML = '<small></small><strong><i></i><span></span></strong>';
      let pinnedIndex = null;
      const visiblePoints = [];
      const hideTooltip = () => {
        tooltip.hidden = true;
        visiblePoints.forEach(point => point.classList.remove('is-active'));
      };
      const showTooltip = index => {
        const point = points[index];
        const [x, y] = coordinates[index];
        tooltip.querySelector('small').textContent = point.tooltip;
        tooltip.querySelector('strong span').textContent = formatMoney(point.value);
        tooltip.style.left = `${x / width * 100}%`;
        tooltip.style.top = `${y / height * 100}%`;
        tooltip.classList.toggle('align-left', x < 130);
        tooltip.classList.toggle('align-right', x > width - 130);
        tooltip.hidden = false;
        visiblePoints.forEach((node, pointIndex) => node.classList.toggle('is-active', pointIndex === index));
      };

      const desiredLabelWidth = aggregateByWeek ? 94 : (mobile ? 42 : 44);
      const maximumLabels = Math.max(2, Math.floor(plotWidth / desiredLabelWidth));
      const labelStep = points.length <= maximumLabels ? 1 : Math.ceil((points.length - 1) / (maximumLabels - 1));
      points.forEach((point, index) => {
        const [x, y] = coordinates[index];
        const visiblePoint = add('circle', {cx: x, cy: y, r: mobile ? 4.5 : 5.5, class: 'revenue-chart-point'});
        visiblePoints.push(visiblePoint);
        const hit = add('circle', {cx: x, cy: y, r: mobile ? 14 : 12, class: 'revenue-chart-hit', 'data-point-index': index});
        hit.addEventListener('pointerenter', event => { if (event.pointerType !== 'touch') showTooltip(index); });
        hit.addEventListener('pointerleave', () => { if (pinnedIndex !== index) hideTooltip(); });
        hit.addEventListener('click', event => {
          event.stopPropagation();
          if (pinnedIndex === index) { pinnedIndex = null; hideTooltip(); }
          else { pinnedIndex = index; showTooltip(index); }
        });
        const lastIndex = points.length - 1;
        const showLabel = index === 0 || index === lastIndex || (index % labelStep === 0 && index < lastIndex - Math.floor(labelStep / 2));
        if (showLabel) {
          const anchor = index === 0 ? 'start' : (index === lastIndex ? 'end' : 'middle');
          add('text', {x, y: height - 14, class: 'revenue-chart-x-label', 'text-anchor': anchor}, point.label);
        }
      });

      container.replaceChildren(svg, tooltip);
      container.dataset.granularity = aggregateByWeek ? 'week' : 'day';
      container.setAttribute('aria-label', `Динамика выручки: ${aggregateByWeek ? 'по неделям' : 'по дням'}`);
      container.onclick = event => {
        if (!event.target.closest('.revenue-chart-hit')) { pinnedIndex = null; hideTooltip(); }
      };
    };

    render();
    let lastWidth = Math.round(container.getBoundingClientRect().width);
    if ('ResizeObserver' in window) {
      new ResizeObserver(entries => {
        const width = Math.round(entries[0]?.contentRect.width || 0);
        if (width && Math.abs(width - lastWidth) > 1) { lastWidth = width; render(); }
      }).observe(container);
    } else {
      window.addEventListener('resize', () => {
        window.clearTimeout(container.revenueChartResizeTimer);
        container.revenueChartResizeTimer = window.setTimeout(render, 120);
      });
    }
  }

  function initPasswordVisibility() {
    document.querySelectorAll('[data-password-toggle]').forEach(button => {
      const input = document.getElementById(button.getAttribute('aria-controls'));
      if (!input) return;
      button.addEventListener('click', () => {
        const showPassword = input.type === 'password';
        input.type = showPassword ? 'text' : 'password';
        button.setAttribute('aria-pressed', String(showPassword));
        button.setAttribute('aria-label', showPassword ? 'Скрыть пароль' : 'Показать пароль');
        input.focus({preventScroll: true});
        input.setSelectionRange(input.value.length, input.value.length);
      });
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    const timezoneUrl = document.body.dataset.timezoneDetectUrl;
    const browserTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (timezoneUrl && browserTimezone) {
      const csrf = document.querySelector('.sidebar-user input[name="csrfmiddlewaretoken"]')?.value;
      const data = new FormData(); data.set('timezone', browserTimezone); data.set('csrfmiddlewaretoken', csrf || '');
      fetch(timezoneUrl, {method: 'POST', body: data, headers: {'X-Requested-With': 'XMLHttpRequest'}})
        .then(response => { if (response.ok && browserTimezone !== document.body.dataset.tenantTimezone) window.location.reload(); });
    }
    window.enhanceSelects(document);
    window.initAssigneePickers(document);
    window.initAppointmentEdit(document);
    window.initAppointmentCreate(document);
    window.initCalendar(document);
    window.initPhotoLightbox(document);
    window.initComplaintList();
    initRevenueLineChart();
    initPasswordVisibility();
  });
})();
