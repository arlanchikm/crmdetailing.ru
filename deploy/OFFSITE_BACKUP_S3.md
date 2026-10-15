# Резервные копии CRM через S3 (альтернативный вариант)

`bin/backup-offsite` ежедневно создаёт дамп PostgreSQL и архив `media`, затем отправляет их вместе с `.env.production` в зашифрованный репозиторий restic. Пароль шифрования и ключ Object Storage хранятся отдельно от копии. После успешной отправки временные локальные файлы удаляются; если отправка не удалась, они остаются в `backups/offsite-*` для разбора ошибки. Никакое удаление старых удалённых копий не настроено: перед выбором срока хранения нужно оценить объём и стоимость.

Пример ниже использует Yandex Object Storage. Скрипт подходит и для другого S3-совместимого хранилища: меняется только `RESTIC_REPOSITORY`.

## 1. Создать бакет и ключ в Yandex Cloud

1. В консоли Yandex Cloud откройте нужный каталог → **Object Storage** → **Создать бакет**. Придумайте уникальное имя из латинских букв, цифр и дефисов, без точек. Начальный лимит **50 ГБ** можно оставить, затем увеличить по мере роста копий; следите за заполнением, иначе новые копии перестанут загружаться. Выберите класс **Стандартное**. Для чтения объектов, списка и настроек укажите **С авторизацией**. Метки не нужны, ключ KMS пока не выбирайте: restic шифрует данные перед отправкой. Версионирование бакета на первом этапе оставьте выключенным; к его включению можно вернуться после проверки первой копии и оценки дополнительных расходов.
2. В **Identity and Access Management** создайте отдельный сервисный аккаунт, например `crm-backup`.
3. Откройте созданный бакет → **Безопасность** → **Привязки прав доступа** → **Назначить роли**. Дайте аккаунту `crm-backup` роль `storage.editor` **только на этом бакете**.
4. Вернитесь в сервисный аккаунт → **Создать новый ключ** → **Создать статический ключ доступа**. Сохраните идентификатор и секрет. Секрет показывается один раз.
5. Зафиксируйте имя бакета. Секретный ключ и пароль restic не отправляйте в чат и не включайте в Git.

Создание бакета может потребовать платёжного аккаунта; хранение и операции могут тарифицироваться. Проверьте лимиты и уведомления о расходах в облачной консоли.

Официальные инструкции: [создать бакет](https://yandex.cloud/ru/docs/storage/operations/buckets/create), [назначить роль на бакет](https://yandex.cloud/ru/docs/storage/operations/buckets/iam-access), [создать статический ключ](https://yandex.cloud/ru/docs/iam/operations/authentication/manage-access-keys), [цены Object Storage](https://yandex.cloud/ru/docs/storage/pricing).

## 2. Установить файлы на VPS

В Tabby под `crmadmin` создайте каталог `mkdir -p ~/crm-backup-setup`. Через файловую панель Tabby загрузите туда пять файлов из локального проекта: `bin/backup`, `bin/backup-offsite`, `deploy/backup.env.s3.example`, `deploy/crm-detailing-backup.service`, `deploy/crm-detailing-backup.timer`. Затем выполните:

```sh
sudo apt update
sudo apt install restic
sudo install -m 755 ~/crm-backup-setup/backup ~/crm-backup-setup/backup-offsite /opt/crm-detailing/bin/
sudo install -d -m 700 /etc/crm-detailing
sudo install -m 600 ~/crm-backup-setup/backup.env.s3.example /etc/crm-detailing/backup.env
sudo nano /etc/crm-detailing/backup.env
```

В `backup.env` замените только значения в кавычках: имя бакета, идентификатор и секретный ключ. Для Yandex Object Storage адрес имеет вид `s3:https://storage.yandexcloud.net/ИМЯ-БАКЕТА`.

Создайте отдельный пароль шифрования и сохраните его копию **вне VPS**, например в менеджере паролей:

```sh
sudo install -m 600 /dev/null /etc/crm-detailing/restic-password
openssl rand -base64 48 | sudo tee /etc/crm-detailing/restic-password >/dev/null
sudo chmod 600 /etc/crm-detailing/backup.env /etc/crm-detailing/restic-password
```

Для занесения пароля в менеджер паролей его можно один раз просмотреть командой `sudo cat /etc/crm-detailing/restic-password` в приватной сессии. Не присылайте вывод команды в переписку. Без этого пароля восстановить копии невозможно.

## 3. Инициализировать и проверить первую копию

```sh
sudo sh -c 'set -a; . /etc/crm-detailing/backup.env; set +a; restic init'
sudo install -m 644 ~/crm-backup-setup/crm-detailing-backup.service /etc/systemd/system/
sudo install -m 644 ~/crm-backup-setup/crm-detailing-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start crm-detailing-backup.service
sudo journalctl -u crm-detailing-backup.service -n 80 --no-pager
```

В журнале должны быть успешный снимок restic и сообщение об отправке. Затем проверьте целостность удалённого репозитория:

```sh
sudo sh -c 'set -a; . /etc/crm-detailing/backup.env; set +a; restic snapshots --tag crm-detailing; restic check --read-data'
```

После успешной проверки включите ежедневный таймер:

```sh
sudo systemctl enable --now crm-detailing-backup.timer
systemctl list-timers crm-detailing-backup.timer
```

Время `03:00` берётся из часового пояса сервера; запуск может смещаться случайным образом до 15 минут. `Persistent=true` запускает пропущенную копию после включения сервера. После любого сбоя смотрите `journalctl -u crm-detailing-backup.service`.

## 4. Проверить восстановление без изменения работающей CRM

Выберите идентификатор снимка из `restic snapshots --tag crm-detailing` и восстановите его в отдельную папку, не поверх production:

```sh
sudo install -d -m 700 /root/crm-restore-test
sudo sh -c 'set -a; . /etc/crm-detailing/backup.env; set +a; restic restore ИДЕНТИФИКАТОР-СНИМКА --target /root/crm-restore-test'
sudo find /root/crm-restore-test -type f
```

Убедитесь, что в восстановленной папке есть `.env.production`, `database-*.dump` и `media-*.tar.gz`. Для проверки дампа можно использовать уже загруженный образ PostgreSQL 17: `sudo sh -c 'docker run --rm -i postgres:17-alpine pg_restore -l < ПУТЬ-К-DUMP > /dev/null'`. Для архива: `sudo tar -tzf ПУТЬ-К-MEDIA-АРХИВУ > /dev/null`. Замените пути на найденные на предыдущем шаге. Полное восстановление на отдельном тестовом сервере следует проверить до того, как полагаться на копии при аварии.

Пароль restic нужно хранить вне VPS. При утрате сервера для восстановления понадобятся ещё адрес бакета и ключ доступа к нему. Ключ с правами записи на VPS может удалить удалённые копии при компрометации сервера; для дополнительной защиты можно включить версионирование бакета, оценив его стоимость и проверив восстановление предыдущих версий.

Документация restic: [репозиторий S3](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html#s3-compatible-storage), [проверка целостности](https://restic.readthedocs.io/en/stable/045_working_with_repos.html), [восстановление](https://restic.readthedocs.io/en/stable/050_restore.html).
