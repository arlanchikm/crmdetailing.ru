#!/usr/bin/env python3
"""Switch encrypted CRM backups to a private Yandex Object Storage bucket.

Run interactively with sudo. Credentials are read without echo and are never
passed through command-line arguments or printed.
"""
from __future__ import annotations

import getpass
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone


PROJECT_DIR = Path("/opt/crm-detailing")
CONFIG_DIR = Path("/etc/crm-detailing")
CONFIG_FILE = CONFIG_DIR / "backup.env"
PASSWORD_FILE = CONFIG_DIR / "restic-password"
PRODUCTION_ENV = PROJECT_DIR / ".env.production"
REPOSITORY = "s3:https://storage.yandexcloud.net/crmdetailing-backup/crm-detailing/production"
TIMER = "crm-detailing-backup.timer"


def run(*args: str, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, env=env, check=check, text=True)


def write_private(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def update_support_email() -> None:
    if not PRODUCTION_ENV.exists():
        raise RuntimeError(f"Не найден {PRODUCTION_ENV}")
    lines = PRODUCTION_ENV.read_text(encoding="utf-8").splitlines()
    key = "DJANGO_SUPPORT_EMAIL"
    replacement = f"{key}=info.crmdetailing@yandex.ru"
    updated = False
    for index, line in enumerate(lines):
        if line.startswith(key + "="):
            lines[index] = replacement
            updated = True
            break
    if not updated:
        lines.append(replacement)
    write_private(PRODUCTION_ENV, "\n".join(lines) + "\n")


def main() -> int:
    if os.geteuid() != 0:
        print("Запустите скрипт через sudo.", file=sys.stderr)
        return 1
    for required in (PROJECT_DIR / "bin/backup-offsite", PASSWORD_FILE, PRODUCTION_ENV):
        if not required.exists():
            raise RuntimeError(f"Не найден обязательный файл: {required}")

    print("Настраивается закрытый бакет crmdetailing-backup.")
    access_key = getpass.getpass("Access Key ID (ввод скрыт): ").strip()
    secret_key = getpass.getpass("Secret Access Key (ввод скрыт): ").strip()
    if not access_key or not secret_key:
        raise RuntimeError("Оба значения ключа обязательны.")

    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)
    previous = CONFIG_FILE.read_bytes() if CONFIG_FILE.exists() else None
    previous_mode = CONFIG_FILE.stat().st_mode & 0o777 if CONFIG_FILE.exists() else 0o600
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if previous is not None:
        archived = CONFIG_DIR / f"backup.env.before-yandex-{timestamp}"
        archived.write_bytes(previous)
        os.chmod(archived, 0o600)

    config = "\n".join([
        f"RESTIC_REPOSITORY={shlex.quote(REPOSITORY)}",
        f"RESTIC_PASSWORD_FILE={shlex.quote(str(PASSWORD_FILE))}",
        f"AWS_ACCESS_KEY_ID={shlex.quote(access_key)}",
        f"AWS_SECRET_ACCESS_KEY={shlex.quote(secret_key)}",
        "RESTIC_RETENTION='true'",
        "",
    ])
    restic_env = {
        **os.environ,
        "RESTIC_REPOSITORY": REPOSITORY,
        "RESTIC_PASSWORD_FILE": str(PASSWORD_FILE),
        "AWS_ACCESS_KEY_ID": access_key,
        "AWS_SECRET_ACCESS_KEY": secret_key,
    }

    run("systemctl", "stop", TIMER, check=False)
    try:
        write_private(CONFIG_FILE, config)
        probe = run("restic", "cat", "config", env=restic_env, check=False)
        if probe.returncode != 0:
            print("Создаётся новый зашифрованный restic-репозиторий в бакете...")
            run("restic", "init", env=restic_env)
        update_support_email()
        print("Создаётся и отправляется первая резервная копия...")
        run(str(PROJECT_DIR / "bin/backup-offsite"))
        run("restic", "check", env=restic_env)
        run("systemctl", "daemon-reload")
        run("systemctl", "enable", "--now", TIMER)
        run("systemctl", "list-timers", TIMER, "--no-pager")
    except Exception:
        if previous is None:
            CONFIG_FILE.unlink(missing_ok=True)
        else:
            CONFIG_FILE.write_bytes(previous)
            os.chmod(CONFIG_FILE, previous_mode)
        run("systemctl", "start", TIMER, check=False)
        raise

    print("Готово: новые зашифрованные копии отправляются в Yandex Object Storage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
