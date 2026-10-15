import hashlib
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.hashers import check_password, make_password
from django.core.mail import send_mail
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache

from .models import AuthenticationAttempt, RecoveryCode, SecurityChallenge


def attempt_key(request, email):
    return hashlib.sha256((email.lower().strip() + '|' + request.META.get('REMOTE_ADDR', '')).encode()).hexdigest()


def login_blocked(request, email):
    return AuthenticationAttempt.objects.filter(key=attempt_key(request,email), failures__gte=5,
        updated_at__gt=timezone.now()-timedelta(minutes=15)).exists()


def failed_login(request, email):
    row, _ = AuthenticationAttempt.objects.get_or_create(key=attempt_key(request,email))
    row.failures = row.failures+1 if row.updated_at > timezone.now()-timedelta(minutes=15) else 1
    row.save()


def security_email(user, tenant=None):
    if tenant is None:
        from apps.tenants.models import PlatformAccess
        email = PlatformAccess.objects.filter(user=user, active=True).values_list('email', flat=True).first()
        if email:
            return email
    return user.email


def issue_challenge(user, purpose, tenant=None, *, allow_fallback=False):
    if not settings.EMAIL_HOST and settings.EMAIL_BACKEND.endswith('smtp.EmailBackend') and not allow_fallback:
        raise ValueError('Отправка писем не настроена.')
    if SecurityChallenge.objects.filter(user=user, purpose=purpose,
        sent_at__gt=timezone.now()-timedelta(seconds=60), consumed_at__isnull=True).exists():
        raise ValueError('Подождите минуту перед повторной отправкой кода.')
    code=f'{secrets.randbelow(1000000):06}'
    delivery_failed=False
    try:
        send_mail('Код подтверждения CRM Detailing',
            f'Ваш код: {code}\nОн действует 10 минут. Если вы не запрашивали код, не используйте его.',
            settings.DEFAULT_FROM_EMAIL, [security_email(user, tenant)], fail_silently=False)
    except Exception:
        if not allow_fallback:
            raise ValueError('Не удалось отправить код. Попробуйте позже; 2FA не изменена.')
        delivery_failed=True
    SecurityChallenge.objects.filter(user=user,purpose=purpose,tenant=tenant,consumed_at__isnull=True).update(consumed_at=timezone.now())
    challenge=SecurityChallenge.objects.create(user=user,tenant=tenant,purpose=purpose,
        code_hash=make_password(code),auth_hash=user.get_session_auth_hash(),expires_at=timezone.now()+timedelta(minutes=10))
    challenge.delivery_failed=delivery_failed
    return challenge


def recovery_hash(code):
    value=re.sub(r'[\s-]', '', code).upper()
    return salted_hmac('crm-recovery-code', value, algorithm='sha256').hexdigest()


def check_challenge(challenge, code, *, allow_recovery=False):
    """Caller holds the row lock; unsuccessful attempts remain bounded across resends."""
    if challenge.consumed_at or challenge.expires_at <= timezone.now() or challenge.attempts >= 5:
        return False, 'Код истёк или исчерпан лимит попыток. Начните заново.'
    if challenge.auth_hash and challenge.user.get_session_auth_hash()!=challenge.auth_hash:
        return False,'Пароль аккаунта изменился. Начните подтверждение заново.'
    valid=bool(re.fullmatch(r'\d{6}', code)) and check_password(code,challenge.code_hash)
    if not valid and allow_recovery:
        recovery=RecoveryCode.objects.select_for_update().filter(user=challenge.user,
            code_hash=recovery_hash(code),used_at__isnull=True).first()
        if recovery:
            recovery.used_at=timezone.now();recovery.save(update_fields=['used_at']);valid=True
    if not valid:
        challenge.attempts+=1;challenge.save(update_fields=['attempts'])
        return False, 'Неверный код.'
    challenge.consumed_at=timezone.now();challenge.save(update_fields=['consumed_at'])
    return True, ''


def new_recovery_codes(user):
    RecoveryCode.objects.filter(user=user).delete()
    codes=[secrets.token_hex(8).upper() for _ in range(10)]
    RecoveryCode.objects.bulk_create([RecoveryCode(user=user,code_hash=recovery_hash(code)) for code in codes])
    return [code[:8]+'-'+code[8:] for code in codes]


def resend_challenge(challenge):
    if challenge.consumed_at or challenge.attempts>=5 or challenge.resends>=3:
        raise ValueError('Начните подтверждение заново.')
    if challenge.sent_at>timezone.now()-timedelta(seconds=60):
        raise ValueError('Повторная отправка доступна через минуту.')
    code=f'{secrets.randbelow(1000000):06}'
    try:
        send_mail('Новый код CRM Detailing',f'Ваш код: {code}. Действует 10 минут.',settings.DEFAULT_FROM_EMAIL,[security_email(challenge.user, challenge.tenant)])
    except Exception:
        raise ValueError('Не удалось отправить код. Попробуйте позже.')
    challenge.code_hash=make_password(code);challenge.expires_at=timezone.now()+timedelta(minutes=10)
    challenge.sent_at=timezone.now();challenge.resends+=1
    challenge.save(update_fields=['code_hash','expires_at','sent_at','resends'])


@never_cache
def verify_login(request, *, platform=False):
    purpose='platform' if platform else 'login'
    login_url='platform-login' if platform else 'login'
    tenant=None if platform else request.tenant
    try:
        challenge=SecurityChallenge.objects.select_related('user').get(pk=request.session.get('login_challenge'),purpose=purpose,tenant=tenant)
    except (SecurityChallenge.DoesNotExist, ValueError, TypeError):
        return redirect(login_url)
    error=''
    if request.method=='POST':
        with transaction.atomic():
            challenge=SecurityChallenge.objects.select_for_update().select_related('user').get(pk=challenge.pk)
            if request.POST.get('action')=='resend':
                try: resend_challenge(challenge)
                except ValueError as exc: error=str(exc)
                else: messages.success(request,'Новый код отправлен.')
            else:
                valid,error=check_challenge(challenge,request.POST.get('code','').strip(),allow_recovery=True)
                user=challenge.user
                allowed=user.is_active
                if platform:
                    from apps.tenants.models import PlatformAccess
                    allowed=allowed and PlatformAccess.objects.filter(user=user,active=True).exists()
                else:
                    allowed=allowed and user.memberships.filter(tenant=tenant,status='active').exists()
                if valid and allowed:
                    login(request,user,backend='django.contrib.auth.backends.ModelBackend')
                    request.session['account_2fa_user']=str(user.pk)
                    request.session.pop('login_challenge',None)
                    if platform:
                        request.session['platform_2fa_user']=str(user.pk);request.session.set_expiry(3600)
                        from apps.tenants.models import PlatformAudit
                        PlatformAudit.objects.create(actor=user,action='login_2fa',details={})
                        return redirect('platform-dashboard')
                    target=request.session.pop('login_next','')
                    if not url_has_allowed_host_and_scheme(target,{request.get_host()},require_https=request.is_secure()):
                        target=reverse('dashboard')
                    return redirect(target)
                elif valid:
                    error='Доступ к аккаунту отключён.'
    return render(request,'platform/verify.html' if platform else 'registration/two_factor_verify.html',
        {'error':error,'email':security_email(challenge.user, tenant),'login_url':reverse(login_url)})


@never_cache
def security_settings(request):
    if not request.user.is_authenticated or (request.tenant and not request.membership):
        return HttpResponseForbidden('Нет доступа')
    user=request.user;platform=request.tenant is None
    if platform:
        from apps.tenants.models import PlatformAccess
        if not PlatformAccess.objects.filter(user=user,active=True).exists():
            return HttpResponseForbidden('Нет доступа')
    error='';codes=None;challenge=None
    current_id=request.session.get('security_challenge')
    if current_id:
        challenge=SecurityChallenge.objects.filter(pk=current_id,user=user,tenant=request.tenant,consumed_at__isnull=True).first()
    if request.method=='POST':
        action=request.POST.get('action','')
        if action=='recovery-disable':
            if not user.email_2fa or not user.check_password(request.POST.get('password','')):
                error='Неверный пароль или 2FA не включена.'
            else:
                with transaction.atomic():
                    recovery=RecoveryCode.objects.select_for_update().filter(user=user,
                        code_hash=recovery_hash(request.POST.get('code','')),used_at__isnull=True).first()
                    if not recovery: error='Неверный или уже использованный резервный код.'
                    else:
                        user.email_2fa=False;user.save(update_fields=['email_2fa'])
                        RecoveryCode.objects.filter(user=user).delete()
                        if platform:
                            from apps.tenants.models import PlatformAccess
                            PlatformAccess.objects.filter(user=user).update(email_2fa=False)
                        request.session.pop('security_challenge',None);challenge=None
                        messages.success(request,'2FA отключена с помощью резервного кода.')
        elif action=='cancel':
            if challenge: challenge.consumed_at=timezone.now();challenge.save(update_fields=['consumed_at'])
            request.session.pop('security_challenge',None);challenge=None
        elif action in {'enable','disable','regenerate','verify-email'}:
            if not user.check_password(request.POST.get('password','')):
                error='Неверный текущий пароль.'
            elif action=='enable' and user.email_2fa:
                error='2FA уже включена.'
            elif action in {'disable','regenerate'} and not user.email_2fa:
                error='2FA ещё не включена.'
            else:
                try: challenge=issue_challenge(user,action,request.tenant)
                except ValueError as exc: error=str(exc)
                else: request.session['security_challenge']=str(challenge.pk)
        elif challenge and action=='resend':
            with transaction.atomic():
                challenge=SecurityChallenge.objects.select_for_update().get(pk=challenge.pk)
                try: resend_challenge(challenge)
                except ValueError as exc: error=str(exc)
        elif challenge and action=='confirm':
            with transaction.atomic():
                challenge=SecurityChallenge.objects.select_for_update().get(pk=challenge.pk)
                valid,error=check_challenge(challenge,request.POST.get('code','').strip(),
                    allow_recovery=challenge.purpose in {'disable','regenerate'})
                if valid:
                    if challenge.purpose=='enable':
                        user.email_2fa=True;codes=new_recovery_codes(user)
                        user.email_verified_at=timezone.now()
                    elif challenge.purpose=='disable':
                        user.email_2fa=False;RecoveryCode.objects.filter(user=user).delete()
                    elif challenge.purpose=='regenerate': codes=new_recovery_codes(user)
                    else: user.email_verified_at=timezone.now()
                    user.save(update_fields=['email_2fa','email_verified_at'])
                    if platform:
                        from apps.tenants.models import PlatformAccess
                        PlatformAccess.objects.filter(user=user).update(email_2fa=False)
                    request.session['account_2fa_user']=str(user.pk)
                    request.session['platform_2fa_user']=str(user.pk)
                    request.session.pop('security_challenge',None);challenge=None
                    messages.success(request,'Настройки безопасности обновлены.')
    return render(request,'platform/security.html' if platform else 'core/security_settings.html',
        {'security_error':error,'challenge':challenge,'recovery_codes':codes,
         'remaining_recovery_codes':user.recovery_codes.filter(used_at__isnull=True).count()})
