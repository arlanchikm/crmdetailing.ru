"""Run interactively as root in Tabby. The password never appears in output/argv."""
import getpass
import json
import os
from pathlib import Path
import re
import smtplib
import ssl
import stat
import tempfile
from datetime import datetime

from container_rollout import inspect, rollout

def main():
    if os.geteuid()!=0:
        raise SystemExit('Запустите через sudo или из сеанса root.')
    account='no-reply.crmdetailing@yandex.ru'
    password=''.join(getpass.getpass(f'Пароль приложения Яндекса для {account}: ').split())
    if not password or any(ch.isspace() for ch in password):
        raise SystemExit('Ожидается пароль приложения Яндекса. Настройки не изменены.')
    try:
        with smtplib.SMTP_SSL('smtp.yandex.ru',465,timeout=15,context=ssl.create_default_context()) as connection:
            connection.login(account,password)
    except smtplib.SMTPAuthenticationError:
        raise SystemExit('Яндекс отклонил пароль. Создайте новый пароль приложения именно в no-reply.crmdetailing@yandex.ru. Настройки не изменены.')
    except Exception as exc:
        raise SystemExit(f'Не удалось подключиться к Gmail ({type(exc).__name__}). Настройки не изменены.')
    values={'DJANGO_EMAIL_BACKEND':'django.core.mail.backends.smtp.EmailBackend',
        'DJANGO_EMAIL_HOST':'smtp.yandex.ru','DJANGO_EMAIL_PORT':'465','DJANGO_EMAIL_USE_TLS':'0','DJANGO_EMAIL_USE_SSL':'1',
        'DJANGO_EMAIL_HOST_USER':account,'DJANGO_EMAIL_HOST_PASSWORD':password,
        'DJANGO_DEFAULT_FROM_EMAIL':f'CRM Detailing <{account}>'}
    path=Path('/opt/crm-detailing/.env.production')
    metadata=path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid!=0 or stat.S_IMODE(metadata.st_mode)!=0o600:
        raise SystemExit('Ожидается обычный файл конфигурации root с правами 600. Настройки не изменены.')
    content=path.read_text()
    for key,value in values.items():
        line=key+'='+json.dumps(value)
        if re.search(r'^'+key+r'=.*$',content,re.M): content=re.sub(r'^'+key+r'=.*$',lambda match:line,content,flags=re.M)
        else: content=content.rstrip()+'\n'+line+'\n'
    fd,temp_path=tempfile.mkstemp(prefix='.mail-config-',dir=path.parent)
    with os.fdopen(fd,'w') as handle: handle.write(content)
    os.chmod(temp_path,0o600);os.replace(temp_path,path)
    image=inspect('crm-detailing-prod-web-1')['Config']['Image']
    rollout(image,'mail-'+datetime.now().strftime('%Y%m%d-%H%M%S'),env_updates=values)
    print('SMTP_AUTH_OK. Почта сохранена; web и worker перезапущены. Пароль не выводился.')

if __name__=='__main__': main()
