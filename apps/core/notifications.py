import base64
import json
from urllib.parse import urlsplit

from django import forms
from django.conf import settings
from django.db.models import Q
from django.db import transaction
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from apps.tenants.access import tenant_member_required
from .models import Notification, PushSubscription, WebPushConfig

KINDS={'online_booking':'Онлайн-записи','integration_lead':'Внешние заявки',
 'appointment_cancelled':'Отмена записей','low_stock':'Низкие остатки материалов','stock':'Состояние склада',
 'overdue_appointment':'Просроченные записи','cash_difference':'Расхождения кассы',
 'inactive_client':'Давно не приезжавшие клиенты','employee_time':'Превышение времени работы',
 'low_rating':'Низкие оценки','overdue_complaint':'Просроченные претензии','complaint':'Новые претензии'}


def user_notifications(request):
    return Notification.objects.filter(tenant=request.tenant).exclude(kind='extra_work').exclude(
        kind__in=request.user.disabled_notification_kinds or []).filter(Q(recipient=request.user)|Q(recipient__isnull=True))


class NotificationSettingsForm(forms.Form):
    notification_toasts=forms.BooleanField(label='Всплывающие уведомления в CRM',required=False)
    notification_push=forms.BooleanField(label='Системные push-уведомления',required=False)
    enabled_kinds=forms.MultipleChoiceField(label='Какие уведомления получать',choices=list(KINDS.items()),
        required=False,widget=forms.CheckboxSelectMultiple)


def webpush_config():
    config=WebPushConfig.objects.filter(pk=1).first()
    if config: return config
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
    key=ec.generate_private_key(ec.SECP256R1())
    encode=lambda value:base64.urlsafe_b64encode(value).decode().rstrip('=')
    config,_=WebPushConfig.objects.get_or_create(pk=1,defaults={
        'private_key':encode(key.private_numbers().private_value.to_bytes(32,'big')),
        'public_key':encode(key.public_key().public_bytes(Encoding.X962,PublicFormat.UncompressedPoint))})
    return config


@tenant_member_required
def notification_settings(request):
    user=request.user
    known=dict(KINDS)
    for kind in Notification.objects.filter(recipient=user).values_list('kind',flat=True).distinct():
        if kind!='extra_work': known.setdefault(kind,kind)
    form=NotificationSettingsForm(request.POST if request.method=='POST' else None,initial={
        'notification_toasts':user.notification_toasts,'notification_push':user.notification_push,
        'enabled_kinds':[kind for kind in known if kind not in (user.disabled_notification_kinds or [])]})
    form.fields['enabled_kinds'].choices=list(known.items())
    if request.method=='POST' and form.is_valid():
        user.notification_toasts=form.cleaned_data['notification_toasts'];user.notification_push=form.cleaned_data['notification_push']
        user.disabled_notification_kinds=[kind for kind in known if kind not in form.cleaned_data['enabled_kinds']]
        user.save(update_fields=['notification_toasts','notification_push','disabled_notification_kinds'])
        if not user.notification_push: PushSubscription.objects.filter(user=user).delete()
        from django.contrib import messages
        messages.success(request,'Настройки уведомлений сохранены.')
        return redirect('notification-settings')
    return render(request,'core/notification_settings.html',{'notification_form':form,
        'push_public_key':webpush_config().public_key})


@tenant_member_required
def notification_feed(request):
    now=timezone.now()
    try:
        after=parse_datetime(request.GET.get('after',''))
    except (ValueError, OverflowError):
        after=None
    if after and timezone.is_naive(after): after=timezone.make_aware(after)
    if not after or after>now: after=now
    rows=user_notifications(request)
    from uuid import UUID
    try: after_id=UUID(request.GET.get('after_id',''))
    except (ValueError,TypeError): after_id=None
    recent=rows.filter(Q(created_at__gt=after)|Q(created_at=after,pk__gt=after_id)) if after_id else rows.filter(created_at__gt=after)
    items=list(recent.filter(created_at__lte=now).order_by('created_at','pk')[:100])
    cursor=items[-1].created_at if len(items)==100 else now
    return JsonResponse({'unread':rows.filter(read_at__isnull=True).count(),'cursor':cursor.isoformat(),'cursor_id':str(items[-1].pk) if len(items)==100 else '',
        'toasts':request.user.notification_toasts,'push':request.user.notification_push,
        'items':[{'id':str(row.pk),'title':row.title,'message':row.message,'severity':row.severity,
            'url':row.url if row.url.startswith('/') and not row.url.startswith('//') else '/notifications/',
            'read_url':f'/notifications/{row.pk}/read/'} for row in items]})


def validate_subscription(data):
    endpoint=data.get('endpoint','');url=urlsplit(endpoint)
    allowed=['push.services.mozilla.com','fcm.googleapis.com','push.apple.com','web.push.apple.com','notify.windows.com','wns.windows.com']
    if len(endpoint)>1000 or url.scheme!='https' or url.username or url.password or url.fragment or url.port not in {None,443} or not any(url.hostname==host or (url.hostname or '').endswith('.'+host) for host in allowed):
        raise ValueError('Недопустимый адрес push-сервиса.')
    keys=data.get('keys',{})
    decode=lambda value:base64.urlsafe_b64decode(value+'='*((-len(value))%4))
    public=decode(keys.get('p256dh',''));auth=decode(keys.get('auth',''))
    if len(public)!=65 or len(auth)!=16: raise ValueError('Некорректные ключи устройства.')
    from cryptography.hazmat.primitives.asymmetric import ec
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),public)
    return endpoint,keys['p256dh'],keys['auth']


@require_POST
@tenant_member_required
@transaction.atomic
def push_subscribe(request):
    try:
        if len(request.body)>5000: raise ValueError
        data=json.loads(request.body)
        endpoint,p256dh,auth=validate_subscription(data)
    except (ValueError,TypeError,KeyError,AttributeError):
        return JsonResponse({'error':'Не удалось зарегистрировать устройство.'},status=400)
    if data.get('enable_push') is True and not request.user.notification_push:
        request.user.notification_push=True
        request.user.save(update_fields=['notification_push'])
    if not request.user.notification_push: return JsonResponse({'error':'Сначала включите push в настройках.'},status=403)
    PushSubscription.objects.update_or_create(endpoint=endpoint,defaults={'tenant':request.tenant,
        'user':request.user,'p256dh':p256dh,'auth':auth})
    return JsonResponse({'ok':True})


@require_POST
@tenant_member_required
def push_unsubscribe(request):
    try: endpoint=json.loads(request.body).get('endpoint','')
    except (ValueError,AttributeError): return JsonResponse({'error':'Некорректный запрос.'},status=400)
    PushSubscription.objects.filter(user=request.user,tenant=request.tenant,endpoint=endpoint).delete()
    return JsonResponse({'ok':True})


def service_worker(request):
    from pathlib import Path
    response=HttpResponse((Path(settings.BASE_DIR)/'static/js/crm-sw.js').read_text(),content_type='application/javascript')
    response['Cache-Control']='no-cache';response['Service-Worker-Allowed']='/'
    return response


def web_manifest(request):
    return JsonResponse({'name':getattr(request.tenant,'name','CRM Detailing'),'short_name':'CRM Detailing',
        'start_url':'/app/','scope':'/','display':'standalone','background_color':'#dcebfa',
        'theme_color':getattr(request.tenant,'primary_color','#087cff'),
        'icons':[{'src':f'/static/img/crm-icon-{size}-20261007.png','sizes':f'{size}x{size}','type':'image/png','purpose':'any'} for size in (192,512)]},
        content_type='application/manifest+json')
