# Зашифрованные копии CRM в Google Drive

Этот вариант использует место обычного Google Диска, включая оплаченный пользователем тариф 5 ТБ. Google Cloud Storage не требуется. restic шифрует дамп PostgreSQL, архив media и .env.production **до** отправки через rclone. Копии запускаются ежедневно в 03:00 по времени VPS (с задержкой до 15 минут). После успешной загрузки сохраняются 7 дневных, 4 недельных и 3 месячных снимка. Следите за фактическим размером и свободным местом даже на большом тарифе.

Google Drive — зарубежное хранилище. До первой отправки реальных данных клиентов выясните, допустима ли она для вашего оператора персональных данных и требуется ли уведомление о трансграничной передаче. Само шифрование не отменяет необходимость такой оценки. Не присылайте в чат файл rclone.conf, пароль restic или .env.production.

## 1. Подготовить Google Drive на Mac

1. Желательно использовать отдельный Google-аккаунт только для резервных копий. Убедитесь, что на нём достаточно свободного места и включена двухэтапная проверка входа.
2. [Скачайте rclone для macOS](https://rclone.org/downloads/) под архитектуру Mac (uname -m показывает arm64 либо x86_64). Распакуйте архив и в Terminal перейдите в каталог с программой. Выделите для CRM отдельный конфигурационный файл: выполните RCLONE_CONFIG="$HOME/crm-rclone.conf" ./rclone config.
3. Создайте новый remote с именем **crm-drive**, типом **Google Drive**. Поля client ID и client secret оставьте пустыми. Для scope выберите **drive.file**. Подтвердите использование общего client ID rclone. Остальные расширенные параметры оставьте по умолчанию. На вопрос об авторизации через браузер ответьте y, войдите в нужный Google-аккаунт и разрешите доступ. Shared Drive не нужен. Сохраните remote.
4. Проверьте подключение командой RCLONE_CONFIG="$HOME/crm-rclone.conf" ./rclone about crm-drive:; она должна вывести доступное место. Файл ~/crm-rclone.conf содержит OAuth-токен, поэтому не открывайте к нему общий доступ и не переносите другие ваши rclone-настройки на VPS.

Это временный вариант без Google Cloud и публикации приложения. Общий client ID rclone запланирован к отключению в 2026 году. Не считайте его постоянным решением для единственной резервной копии: регулярно проверяйте успешность службы и планируйте переход на [собственный ID](https://rclone.org/drive/#making-your-own-client-id) или другое хранилище. Scope drive.file даёт доступ только к файлам, созданным данным OAuth-приложением: при смене client ID новый клиент с тем же scope может не увидеть старый репозиторий. Для перехода потребуется отдельный план миграции, например новая копия в новом репозитории; старые снимки нельзя удалять до проверки новой копии. Приложение Google Drive for desktop на Mac тоже работает без собственного Cloud-проекта, но официальной версии для Linux/VPS нет; при таком варианте файлы с VPS придётся переносить через Mac.

## 2. Перенести файлы через Tabby на VPS

Подключитесь как crmadmin. В терминале Tabby:

    mkdir -p ~/crm-backup-setup
    chmod 700 ~/crm-backup-setup

Через файловую панель Tabby (SFTP) загрузите в этот каталог **без переименования**:

- bin/backup
- bin/backup-offsite
- deploy/backup.env.example
- deploy/crm-detailing-backup.service
- deploy/crm-detailing-backup.timer
- файл ~/crm-rclone.conf с Mac

Затем в Tabby:

    sudo apt update
    sudo apt install restic rclone
    sudo install -m 755 ~/crm-backup-setup/backup ~/crm-backup-setup/backup-offsite /opt/crm-detailing/bin/
    sudo install -d -m 700 /etc/crm-detailing
    sudo install -m 600 ~/crm-backup-setup/backup.env.example /etc/crm-detailing/backup.env
    sudo install -m 600 ~/crm-backup-setup/crm-rclone.conf /etc/crm-detailing/rclone.conf
    sudo install -d -m 700 /opt/crm-detailing/backups
    rm ~/crm-backup-setup/crm-rclone.conf

Создайте отдельный случайный пароль шифрования:

    sudo sh -c 'umask 077; openssl rand -base64 48 > /etc/crm-detailing/restic-password'

Сохраните **этот пароль вне VPS**, например в менеджере паролей. Один раз покажите его в приватном терминале командой sudo cat /etc/crm-detailing/restic-password; вывод не присылайте в переписку. Без пароля копии невозможно восстановить. Также сохраните доступ к Google-аккаунту. Файл rclone.conf на VPS должен остаться доступным только root.

Если имя remote отличается от crm-drive, исправьте RESTIC_REPOSITORY в /etc/crm-detailing/backup.env до инициализации. Параметр RCLONE_DRIVE_USE_TRASH=false нужен для освобождения квоты: при удалении старых снимков файлы удаляются сразу, не попадая в корзину. Восстановить удалённые старые снимки будет нельзя. Скрипт удаляет их только после успешной загрузки нового снимка.

## 3. Проверить подключение и первую копию

Сначала проверьте, что именно VPS может подключиться к Google Drive:

    sudo env RCLONE_CONFIG=/etc/crm-detailing/rclone.conf rclone about crm-drive:

Если эта команда не работает, не включайте таймер: проверьте OAuth и исходящее соединение с Google с VPS. После успешного ответа:

    sudo sh -c 'set -a; . /etc/crm-detailing/backup.env; set +a; restic init'
    sudo install -m 644 ~/crm-backup-setup/crm-detailing-backup.service /etc/systemd/system/
    sudo install -m 644 ~/crm-backup-setup/crm-detailing-backup.timer /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl start crm-detailing-backup.service
    sudo journalctl -u crm-detailing-backup.service -n 80 --no-pager

Убедитесь, что служба завершилась успешно, затем проверьте снимок и данные:

    sudo sh -c 'set -a; . /etc/crm-detailing/backup.env; set +a; restic snapshots --tag crm-detailing; restic check --read-data'

Первая полная проверка может занять время. После неё включите ежедневный запуск:

    sudo systemctl enable --now crm-detailing-backup.timer
    systemctl list-timers crm-detailing-backup.timer

Периодически проверяйте systemctl status crm-detailing-backup.service, journalctl -u crm-detailing-backup.service и свободное место в Google Drive. Если объём CRM превысит доступное место, новые копии прекратятся даже с указанным сроком хранения.

## 4. Проверить восстановление отдельно от работающей CRM

После первой копии возьмите ID из restic snapshots --tag crm-detailing и восстановите его в отдельный каталог:

    sudo install -d -m 700 /root/crm-restore-test
    sudo sh -c 'set -a; . /etc/crm-detailing/backup.env; set +a; restic restore ИД-СНИМКА --target /root/crm-restore-test'
    sudo find /root/crm-restore-test -type f

В каталоге должны быть .env.production, database-*.dump и media-*.tar.gz. Проверка файлов:

    sudo sh -c 'docker run --rm -i postgres:17-alpine pg_restore -l < ПУТЬ-К-DUMP > /dev/null'
    sudo tar -tzf ПУТЬ-К-MEDIA-АРХИВУ > /dev/null

Замените пути на найденные командой find. Полное восстановление CRM лучше отдельно испытать на тестовом сервере. При потере VPS для восстановления нужны пароль restic, доступ к Google Drive, новая конфигурация rclone и приложение restic. Доступ сервера к Google Drive позволяет удалять копии, поэтому этот вариант не обеспечивает защиту от удаления копий при взломе VPS; для более сильной защиты потребуется второе независимое хранилище.

Документация: [restic через rclone](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html#other-services-via-rclone), [хранение снимков](https://restic.readthedocs.io/en/stable/060_forget.html), [rclone Google Drive](https://rclone.org/drive/), [закон о персональных данных](https://ips.pravo.gov.ru/api/ips/legislation/document?baseid=None&hash=98490812b3409e2a8d78a11ca9010f434ea3d9250a11dbbdb78690cd5551bdd6).
