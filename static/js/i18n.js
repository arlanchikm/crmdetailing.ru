(() => {
  const language = document.documentElement.lang || document.body?.dataset.uiLanguage || 'ru';
  if (!language.toLowerCase().startsWith('en')) {
    window.crmT = (text) => text;
    return;
  }

  const en = {
    'Общий норматив': 'Default norm',
    'Время, минут': 'Time, minutes',
    'Пустое время использует общий норматив. Время класса полностью заменяет общий норматив.': 'An empty time uses the default norm. Class time replaces the default norm.',
    'Список полностью заменяет общий норматив. Если список пуст, используется общий норматив.': 'This list replaces the default norm. An empty list uses the default norm.',
    'Расходник уже добавлен в этот норматив.': 'This supply is already included in this norm.',
    'Рабочее пространство': 'Workspace',
    'Записи': 'Appointments',
    'Клиенты': 'Clients',
    'Сотрудники': 'Employees',
    'График': 'Schedule',
    'Услуги': 'Services',
    'Расходники': 'Supplies',
    'Аналитика': 'Analytics',
    'Расчет зарплат': 'Payroll',
    'Качество': 'Quality',
    'Уведомления': 'Notifications',
    'Касса': 'Cash register',
    'Журнал действий': 'Activity log',
    'Рабочие посты': 'Work bays',
    'Логотип': 'Logo',
    "По сотрудникам": "By employees",
    "По рабочим постам": "By work bays",
    "Изменить посты": "Edit work bays",
    "Без поста": "No work bay",
    "Удалённые": "Deleted",
    "Действующие": "Active",
    "Восстановить": "Restore",
    "Удалённые автомобили": "Deleted vehicles",
    "Удаление и восстановление клиентов и автомобилей": "Delete and restore clients and vehicles",
    "Настроить форму": "Customize form",
    "Кнопки и акцент:": "Buttons and accent:",
    "Фон страницы:": "Page background:",
    "Блоки:": "Panels:",
    "Текст:": "Text:",
    "Затемнение фона:": "Background dimming:",
    "Изображение фона:": "Background image:",
    "Убрать свой фон": "Remove custom background",
    "Сбросить": "Reset",
    "Добавить пост": "Add work bay",
    "Оформление формы сохранено.": "Booking form appearance saved.",
    'Настройки профиля': 'Profile settings',
    'Выйти': 'Sign out',
    'Настройки': 'Settings',
    'Настройки пользователя': 'User settings',
    'Настройки компании': 'Company settings',
    'Интерфейс': 'Interface',
    'Интеграции': 'Integrations',
    'Каналы входящих заявок и внешние сервисы': 'Inbound request channels and external services',
    'Форма записи для сайта': 'Website booking form',
    'Готовый блок, который можно вставить на сайт компании': 'A ready-to-use block for your company website',
    'Включена': 'Enabled',
    'Выключена': 'Disabled',
    'Форма онлайн-записи включена': 'Online booking form enabled',
    'Заголовок формы': 'Form title',
    'Оформление на этом устройстве': 'Appearance on this device',
    'Выбор сохраняется только в этом браузере и не меняет интерфейс коллег.': 'This choice is saved only in this browser and does not change your colleagues’ interface.',
    'Светлая': 'Light',
    'Тёмная': 'Dark',
    'Оформление компании': 'Company appearance',
    'Язык и цвета применяются ко всем страницам компании после сохранения.': 'Language and colors apply to all company pages after saving.',
    'Язык и цвета компании, а также тема этого устройства': 'Company language and colors, plus this device’s theme',
    'Код для вставки': 'Embed code',
    'Копировать': 'Copy',
    'Вставьте этот код в HTML страницы. Высоту и скругление можно изменить прямо в коде.': 'Paste this code into the page HTML. You can adjust its height and corner radius in the code.',
    'Открыть форму': 'Open form',
    'Заявки и переписка из профиля компании': 'Requests and messages from the company profile',
    'Входящие звонки и карточка клиента': 'Inbound calls and customer profiles',
    'Готово к подключению': 'Ready to connect',
    'Для канала уже предусмотрены внешний идентификатор, исходные данные заявки и единый статус обработки.': 'The channel already supports external IDs, original request data, and a shared processing status.',
    'Канал сможет сохранять номер, идентификатор звонка и дополнительные данные провайдера в общем журнале.': 'The channel can store the phone number, call ID, and provider data in the shared log.',
    'Входящие заявки': 'Inbound requests',
    'Общий журнал для сайта, Avito и телефонии': 'Shared log for the website, Avito, and telephony',
    'Источник': 'Source',
    'Автомобиль и услуга': 'Vehicle and service',
    'Желаемое время': 'Preferred time',
    'Обработана': 'Processed',
    'Услуга не выбрана': 'No service selected',
    'Настройки формы онлайн-записи сохранены.': 'Online booking form settings saved.',
    'Заявок из внешних каналов пока нет.': 'No requests from external channels yet.',
    'Разделы настроек': 'Settings sections',
    'Личные данные и безопасность аккаунта': 'Personal details and account security',
    'Данные и рабочее время компании': 'Company details and business hours',
    'Язык, готовое оформление и цвета интерфейса': 'Language, theme and interface colors',
    'Профиль': 'Profile',
    'Эти данные видны в интерфейсе компании': 'These details are visible in the company interface',
    'Фотография профиля': 'Profile photo',
    'Удалить текущую фотографию': 'Remove current photo',
    'Имя': 'Name',
    'Профессия в компании': 'Position in the company',
    'Сохранить профиль': 'Save profile',
    'Смена пароля': 'Change password',
    'После сохранения текущий сеанс останется активным': 'Your current session will remain active after saving',
    'Изменить пароль': 'Change password',
    'Старый пароль': 'Current password',
    'Новый пароль': 'New password',
    'Подтверждение нового пароля': 'Confirm new password',
    'Название и логотип': 'Name and logo',
    'Логотип отображается в верхней части бокового меню': 'The logo is displayed at the top of the sidebar',
    'Логотип компании': 'Company logo',
    'Название компании': 'Company name',
    'Удалить текущий логотип': 'Remove current logo',
    'Сохранить данные компании': 'Save company details',
    'Часы работы автомойки': 'Business hours',
    'По этим границам строится временная шкала в записях, рабочих постах и графике': 'These hours define the timeline in appointments, work bays and schedules',
    'Открытие': 'Opening',
    'Закрытие': 'Closing',
    'Выходной': 'Closed',
    'Сохранить часы работы': 'Save business hours',
    'Оформление интерфейса': 'Interface appearance',
    'Готовую тему можно дополнить индивидуальными цветами компании': 'You can customize a preset theme with your company colors',
    'Язык интерфейса': 'Interface language',
    'Готовое оформление': 'Theme',
    'Светлая синяя': 'Light blue',
    'Тёмная': 'Dark',
    'Русский': 'Russian',
    'Владелец': 'Owner',
    'Администратор': 'Administrator',
    'Мастер': 'Technician',
    'Детейлер': 'Detailer',
    'Полировщик': 'Polisher',
    'Цвета': 'Colors',
    'Изменения применяются ко всем страницам компании после сохранения': 'Changes apply to every company page after saving',
    'Основной цвет и кнопки': 'Primary color and buttons',
    'Оттенок фоновой подложки': 'Background tint',
    'Оттенок прозрачных блоков': 'Glass panel tint',
    'Сохранить интерфейс': 'Save interface',
    'Расписание сотрудников. Нажмите на время или перетащите запись мышью': 'Employee schedule. Click a time slot or drag an appointment',
    'Новая запись': 'New appointment',
    'Предыдущий день': 'Previous day',
    'Следующий день': 'Next day',
    'Сегодня': 'Today',
    'Время': 'Time',
    'Смена ещё не началась': 'Shift has not started',
    'Смена завершена': 'Shift ended',
    'На эту дату компания закрыта. Часы работы можно изменить в настройках компании.': 'The company is closed on this date. You can change business hours in company settings.',
    'Добавьте хотя бы один активный рабочий пост.': 'Add at least one active work bay.',
    'На эту дату в графике нет сотрудников.': 'No employees are scheduled on this date.',
    'Открыть график': 'Open schedule',
    'Карточка записи': 'Appointment details',
    'Загрузка…': 'Loading…',
    'Загрузка': 'Loading',
    'Ожидание': 'Waiting',
    'В работе': 'In progress',
    'Завершено': 'Completed',
    'Не пришёл': 'No-show',
    'Не оплачено': 'Unpaid',
    'Частично оплачено': 'Partially paid',
    'Оплачено': 'Paid',
    'Возвращено': 'Refunded',
    'Клиент': 'Client',
    'Автомобиль': 'Vehicle',
    'Класс автомобиля': 'Vehicle class',
    'Класс автомобиля *': 'Vehicle class *',
    'Гос. номер': 'License plate',
    'Номер телефона *': 'Phone number *',
    'Имя *': 'First name *',
    'Фамилия': 'Last name',
    'Исполнители': 'Technicians',
    'Исполнитель': 'Technician',
    'Комментарий': 'Comment',
    'Стоимость': 'Price',
    'Стоимость записи': 'Appointment price',
    'Скидка': 'Discount',
    'Вид скидки': 'Discount type',
    'Без скидки': 'No discount',
    'Процент': 'Percent',
    'Сумма': 'Amount',
    'Причина скидки': 'Discount reason',
    'Причина бесплатной работы': 'Free service reason',
    'Рабочий пост': 'Work bay',
    'Не выбран': 'Not selected',
    'Без поста': 'No work bay',
    'Выберите': 'Select',
    'Выберите исполнителей': 'Select technicians',
    'Изменить распределение': 'Edit split',
    'Создать запись': 'Create appointment',
    'Редактирование записи': 'Edit appointment',
    'Изменение записи': 'Edit appointment',
    'Сохранить': 'Save',
    'Изменить': 'Edit',
    'Редактировать': 'Edit',
    'Удалить': 'Delete',
    'Отмена': 'Cancel',
    'Закрыть': 'Close',
    'Завершить': 'Complete',
    'Удалить запись': 'Delete appointment',
    'Причина удаления': 'Deletion reason',
    'История клиента': 'Client history',
    'Скрыть историю': 'Hide history',
    'История посещений': 'Visit history',
    'Затраты записи': 'Appointment costs',
    'Нормативные расходники': 'Planned supplies',
    'Начисленная зарплата': 'Accrued payroll',
    'Комиссия оплаты': 'Payment fee',
    'Оплата': 'Payment',
    'Операция': 'Transaction',
    'Возврат': 'Refund',
    'Способ оплаты': 'Payment method',
    'Выберите способ оплаты': 'Select payment method',
    'Наличные': 'Cash',
    'Карта': 'Card',
    'Перевод': 'Bank transfer',
    'Сохранить оплату': 'Save payment',
    'Удалить оплату': 'Delete payment',
    'Услуга': 'Service',
    'Добавить услугу': 'Add service',
    '+ Добавить услугу': '+ Add service',
    'Выбрано:': 'Selected:',
    'Длительность:': 'Duration:',
    'До скидки': 'Before discount',
    'Основной': 'Primary',
    'основной': 'primary',
    'Распределите участие исполнителей. В сумме должно быть 100%.': 'Allocate technician participation. The total must be 100%.',
    'Клиентская база и автомобили': 'Client database and vehicles',
    'Новый клиент': 'New client',
    'Добавить автомобиль': 'Add vehicle',
    '+ Добавить автомобиль': '+ Add vehicle',
    'Автомобили': 'Vehicles',
    'Телефон': 'Phone',
    'Госномер': 'License plate',
    'Марка и модель': 'Make and model',
    'Последний визит': 'Last visit',
    'Посещений': 'Visits',
    'Автомобилей': 'Vehicles',
    'Клиенты пока не добавлены.': 'No clients have been added yet.',
    'Сотрудник': 'Employee',
    'Добавить сотрудника': 'Add employee',
    '+ Добавить сотрудника': '+ Add employee',
    'Роли и права': 'Roles and permissions',
    'Новая роль': 'New role',
    'Должность': 'Position',
    'Активен': 'Active',
    'Отключен': 'Disabled',
    'Активна': 'Active',
    'Отключена': 'Disabled',
    'Скопировать прошлую неделю': 'Copy previous week',
    'Сохранить график': 'Save schedule',
    'Текущая неделя': 'Current week',
    'Предыдущая неделя': 'Previous week',
    'Следующая неделя': 'Next week',
    'Понедельник': 'Monday',
    'Вторник': 'Tuesday',
    'Среда': 'Wednesday',
    'Четверг': 'Thursday',
    'Пятница': 'Friday',
    'Суббота': 'Saturday',
    'Воскресенье': 'Sunday',
    'Пн': 'Mon', 'Вт': 'Tue', 'Ср': 'Wed', 'Чт': 'Thu', 'Пт': 'Fri', 'Сб': 'Sat', 'Вс': 'Sun',
    'Прайс и нормативы времени': 'Prices and standard times',
    'Новая услуга': 'New service',
    'Настроить классы': 'Configure classes',
    'Классы автомобилей': 'Vehicle classes',
    'Название': 'Name',
    'Цена': 'Price',
    'Длительность': 'Duration',
    'Нормативная себестоимость услуги': 'Planned service cost',
    'Расходник': 'Supply',
    'Материал': 'Material',
    'Категория': 'Category',
    'Категории': 'Categories',
    'Стоимость': 'Cost',
    'План': 'Plan',
    'Факт': 'Actual',
    'Минимум': 'Minimum',
    'Отклонение': 'Variance',
    'История движений': 'Movement history',
    'Добавить расходник': 'Add supply',
    '+ Добавить расходник': '+ Add supply',
    'Движений пока нет.': 'No movements yet.',
    'В норме': 'Normal',
    'Низкий остаток': 'Low stock',
    'Обзор': 'Overview',
    'Выручка': 'Revenue',
    'Средний чек': 'Average ticket',
    'Все затраты': 'Total costs',
    'Результат': 'Result',
    'Повторные клиенты': 'Returning clients',
    'Оценка': 'Rating',
    'Структура затрат': 'Cost structure',
    'Последние затраты': 'Recent costs',
    'Рассчитать': 'Calculate',
    'Начислено': 'Accrued',
    'Подробный расчет': 'Detailed calculation',
    'Претензии и переделки': 'Complaints and rework',
    'Новая претензия': 'New complaint',
    'Претензия': 'Complaint',
    'Причина': 'Reason',
    'Ответственный': 'Responsible employee',
    'Срок': 'Due date',
    'Статус': 'Status',
    'Описание': 'Description',
    'Решение': 'Resolution',
    'Фотографии': 'Photos',
    'Открыть запись': 'Open appointment',
    'Прочитано': 'Read',
    'Прочитать все': 'Mark all as read',
    'Уведомлений пока нет.': 'No notifications yet.',
    'Смена открыта': 'Shift is open',
    'Открыть смену': 'Open shift',
    'Закрыть и сверить смену': 'Close and reconcile shift',
    'Инкассация': 'Cash collection',
    'Ожидается наличных в кассе': 'Expected cash in register',
    'Фактические наличные в кассе': 'Actual cash in register',
    'Последние смены': 'Recent shifts',
    'Действие': 'Action',
    'Объект': 'Entity',
    'Изменения': 'Changes',
    'Дата': 'Date',
    'Пользователь': 'User',
    'Компания': 'Company',
    'Пост': 'Work bay',
    'Цвет': 'Color',
    'Новый пост': 'New work bay',
    'Добавить': 'Add',
    '+ Добавить': '+ Add',
    'Найти': 'Search',
    'Очистить': 'Clear',
    'Копировать': 'Copy',
    'Скопировано': 'Copied',
    'Нет прав': 'Permission denied',
    'Недостаточно прав': 'Insufficient permissions',
    'Загрузка постов': 'Work bay utilization',
    'Расход материалов': 'Material usage',
    'Сначала отметьте запись как оплаченную. Неоплаченную запись завершить нельзя.': 'Mark the appointment as paid first. An unpaid appointment cannot be completed.',
    'Настройки интерфейса сохранены.': 'Interface settings saved.',
    'Настройки пользователя сохранены.': 'User settings saved.',
    'Настройки компании сохранены.': 'Company settings saved.',
    'Часы работы сохранены.': 'Business hours saved.',
    'Пароль изменён.': 'Password changed.',
    'Запись создана.': 'Appointment created.',
    'Запись сохранена.': 'Appointment saved.',
    'Запись удалена.': 'Appointment deleted.',
    'Операция оплаты сохранена.': 'Payment transaction saved.',
    'Операция оплаты удалена.': 'Payment transaction deleted.',
    'Проверьте сумму и способ оплаты.': 'Check the amount and payment method.',
  };

  const patterns = [
    [/^Расходники — (.+)$/, (_, value) => `Supplies — ${en[value] || value}`],
    [/^Логотип\s+(.+)$/, (_, value) => `Logo ${value}`],
    [/^(Запись)\s+(.+)$/, (_, __, value) => `Appointment ${value}`],
    [/^Оплачено\s+(.+)\s+из\s+(.+)\s+·\s+комиссия\s+(.+)$/, (_, paid, total, fee) => `Paid ${paid} of ${total} · fee ${fee}`],
    [/^До скидки\s+(.+)\s+·\s+скидка\s+(.+)$/, (_, total, discount) => `Before discount ${total} · discount ${discount}`],
    [/^Выбрано:\s*(\d+)$/, (_, value) => `Selected: ${value}`],
    [/^Длительность:\s*(.+)$/, (_, value) => `Duration: ${value}`],
    [/^(\d+(?:[.,]\d+)?)\s*мин\.$/, (_, value) => `${value} min.`],
    [/^(.+)\s+·\s+основной$/, (_, value) => `${value} · primary`],
    [/^Статус записи:\s*(.+)\.$/, (_, value) => `Appointment status: ${window.crmT(value)}.`],
    [/^Ожидается\s+(.+)\s+наличных в кассе$/, (_, value) => `${value} cash expected in register`],
    [/^Спасибо за оценку\s+(.+)\s+из 5$/, (_, value) => `Thank you for your ${value} out of 5 rating`],
    [/^(Детейлер|Полировщик|Администратор|Мастер)\s+·\s+(.+)$/, (_, role, value) => `${en[role] || role} · ${value}`],
  ];

  function translate(text) {
    if (typeof text !== 'string') return text;
    const key = text.replace(/\s+/g, ' ').trim();
    if (!key) return text;
    if (en[key]) return en[key];
    if (key.endsWith(':') && en[key.slice(0, -1)]) return `${en[key.slice(0, -1)]}:`;
    for (const [pattern, replacement] of patterns) {
      if (pattern.test(key)) return key.replace(pattern, replacement);
    }
    if (/\d+(?:[.,]\d+)?\s+мин\./.test(key)) return key.replace(/(\d+(?:[.,]\d+)?)\s+мин\./g, '$1 min.');
    return text;
  }
  window.crmT = translate;

  function translateTextNode(node) {
    if (!node.nodeValue || ['SCRIPT', 'STYLE'].includes(node.parentElement?.tagName)) return;
    const original = node.nodeValue;
    const core = original.replace(/\s+/g, ' ').trim();
    let translated;
    if ((core === 'С' || core === 'По') && node.parentElement?.closest('label, .field')) {
      translated = core === 'С' ? 'From' : 'To';
    } else {
      translated = translate(core);
    }
    if (translated === core || translated === original) return;
    node.nodeValue = `${original.match(/^\s*/)?.[0] || ''}${translated}${original.match(/\s*$/)?.[0] || ''}`;
  }

  function translateElement(element) {
    if (!(element instanceof Element)) return;
    for (const attribute of ['aria-label', 'title', 'placeholder']) {
      if (element.hasAttribute(attribute)) {
        const value = element.getAttribute(attribute);
        const translated = translate(value);
        if (translated !== value) element.setAttribute(attribute, translated);
      }
    }
    if (element instanceof HTMLInputElement && ['button', 'submit', 'reset'].includes(element.type)) {
      element.value = translate(element.value);
    }
  }

  function translateTree(root) {
    if (root.nodeType === Node.TEXT_NODE) {
      translateTextNode(root);
      return;
    }
    if (!(root instanceof Element || root instanceof Document)) return;
    if (root instanceof Element) translateElement(root);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      if (node.nodeType === Node.TEXT_NODE) translateTextNode(node);
      else translateElement(node);
    }
  }

  const start = () => {
    document.title = document.title
      .replace(/^Настройки пользователя/, 'User settings')
      .replace(/^Настройки компании/, 'Company settings')
      .replace(/^Интерфейс/, 'Interface')
      .replace(/^Интеграции/, 'Integrations')
      .replace(/^Записи/, 'Appointments')
      .replace(/^Клиенты/, 'Clients')
      .replace(/^Сотрудники/, 'Employees')
      .replace(/^График/, 'Schedule')
      .replace(/^Услуги/, 'Services')
      .replace(/^Расходники/, 'Supplies')
      .replace(/^Аналитика/, 'Analytics')
      .replace(/^Качество/, 'Quality');
    translateTree(document);
    new MutationObserver((records) => {
      for (const record of records) record.addedNodes.forEach(translateTree);
    }).observe(document.body, {childList: true, subtree: true});
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
})();
