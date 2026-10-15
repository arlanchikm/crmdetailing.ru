import mimetypes
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse, FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from apps.catalog.models import Service, WorkPost
from apps.customers.models import VehicleClass, ensure_default_vehicle_classes, normalize_phone
from apps.staff.models import Employee, EmployeeService, EmployeeShift
from apps.operations.models import Appointment, ClientRating
from apps.operations.services import create_appointment
from apps.tenants.models import Tenant, Subscription, Membership
from apps.tenants.plans import ensure_primary_location, sync_subscription
from .models import IntegrationChannel, IntegrationLead, notify, log_event
from .phones import PhoneField


def resolve_channel(token):
    return get_object_or_404(IntegrationChannel.objects.select_related('tenant'), public_token=token,
        kind='website', enabled=True, tenant__status='active')


def booking_catalog(channel):
    tenant = channel.tenant
    location = ensure_primary_location(tenant)
    ensure_default_vehicle_classes(tenant)
    classes = list(VehicleClass.objects.filter(tenant=tenant, active=True))
    employees = list(Employee.objects.filter(tenant=tenant, location=location, active=True).prefetch_related('service_settings'))
    services = list(Service.objects.filter(tenant=tenant, location=location, active=True).exclude(system_key='other').select_related('category').prefetch_related('vehicle_class_prices'))
    capability = {str(e.id): {str(row.service_id) for row in e.service_settings.all() if row.tenant_id == tenant.id} for e in employees}
    # Online bookings require explicit capabilities even if the legacy CRM has none.
    employees = [e for e in employees if capability[str(e.id)]]
    service_ids = set().union(*(capability[str(e.id)] for e in employees)) if employees else set()
    services = [s for s in services if str(s.id) in service_ids]
    data = {'categories': [], 'classes': [], 'services': [], 'employees': [], 'today': timezone.localtime(timezone.now(), ZoneInfo(tenant.timezone)).date().isoformat(),
        'closed_weekdays': list(tenant.working_hours.filter(is_closed=True).values_list('weekday', flat=True)),
        'availability_url': reverse('booking-availability', kwargs={'token':channel.public_token}), 'language':tenant.language_code}
    for c in classes:
        image_url = reverse('booking-class-image',kwargs={'token':channel.public_token,'class_id':c.id}) if c.image else static(f'img/vehicle-classes/{c.builtin_key}-480-20261007.webp') if c.builtin_key in {'sedan','crossover','suv'} else ''
        data['classes'].append({'id':str(c.id),'name':c.name,'image':image_url})
    categories = {str(s.category_id): s.category for s in services if s.category_id and s.category.tenant_id == tenant.id}
    data['categories'] = [{'id': key, 'name': category.name} for key, category in sorted(categories.items(), key=lambda row: (row[1].sort_order, row[1].name))]
    if any(not s.category_id or s.category.tenant_id != tenant.id for s in services):
        data['categories'].append({'id': 'uncategorized', 'name': 'Uncategorized' if tenant.language_code == 'en' else 'Без категории'})
    for s in services:
        data['services'].append({'id':str(s.id),'name':s.name,'category':str(s.category_id) if str(s.category_id) in categories else 'uncategorized','duration':s.duration_minutes,'durations':{str(c.id):s.duration_for_class(c) for c in classes},'prices':{str(c.id):str(s.price_for_class(c)) for c in classes}})
    # A shared visit belongs to every assignee, but counts only once per person.
    employee_ids = {e.pk for e in employees}
    ratings = defaultdict(dict)
    rating_rows = ClientRating.objects.filter(tenant=tenant, appointment__status='completed', submitted_by_client=True).filter(
        Q(appointment__employee_id__in=employee_ids) | Q(appointment__assignees__employee_id__in=employee_ids)
    ).order_by().values_list('pk', 'score', 'appointment__employee_id', 'appointment__assignees__employee_id')
    for rating_id, score, primary_id, assignee_id in rating_rows.iterator(chunk_size=1000):
        for employee_id in {primary_id, assignee_id} & employee_ids:
            ratings[employee_id][rating_id] = score
    for e in employees:
        scores = ratings[e.pk].values()
        data['employees'].append({'id':str(e.id),'name':e.name,'services':list(capability[str(e.id)]),'rating':round(sum(scores)/len(scores),1) if scores else None,'rating_count':len(scores)})
    return classes, services, employees, data


def availability_context(tenant, selected_date, employee_id='', *, now=None):
    """Read the schedule once; each quarter-hour uses the same consistent snapshot."""
    tz = ZoneInfo(tenant.timezone)
    location = ensure_primary_location(tenant)
    day_start = timezone.make_aware(datetime.combine(selected_date, datetime.min.time()), tz)
    day_end = timezone.make_aware(datetime.combine(selected_date + timedelta(days=1), datetime.min.time()), tz)
    employees = Employee.objects.filter(tenant=tenant, location=location, active=True).prefetch_related('service_settings')
    if employee_id:
        employees = employees.filter(pk=employee_id)
    jobs = list(Appointment.objects.filter(tenant=tenant, location=location,
        start_at__lt=day_end, end_at__gt=day_start).exclude(status='no_show').prefetch_related('assignees'))
    job_staff = {job.pk: {job.employee_id} | {row.employee_id for row in job.assignees.all()} for job in jobs}
    workload = {}
    for job in jobs:
        for employee in job_staff[job.pk] - {None}:
            workload[employee] = workload.get(employee, 0) + (job.end_at-job.start_at).total_seconds()
    return {'tz': tz, 'now': now or timezone.now(),
        'hours': tenant.working_hours.filter(weekday=selected_date.weekday()).first(),
        'employees': list(employees), 'jobs': jobs, 'job_staff': job_staff, 'workload': workload,
        'shifts': {row.employee_id: row for row in EmployeeShift.objects.filter(tenant=tenant, location=location, date=selected_date)},
        'posts': list(WorkPost.objects.filter(tenant=tenant, location=location, active=True))}


def available_assignments(tenant, services, selected_date, start_time, employee_id='', *, now=None, context=None, vehicle_class=None):
    context = context or availability_context(tenant, selected_date, employee_id, now=now)
    start = timezone.make_aware(datetime.combine(selected_date, start_time), context['tz'])
    end = start + timedelta(minutes=sum(s.duration_for_class(vehicle_class) for s in services))
    if not services or start <= context['now'] or selected_date > timezone.localtime(context['now'], context['tz']).date()+timedelta(days=90) or start_time.minute % 15 or start_time.second:
        return [], start, end
    hours = context['hours']
    if not hours or hours.is_closed or end.date()!=start.date() or start.time()<hours.opens_at or end.time()>hours.closes_at:
        return [], start, end
    ids = {s.id for s in services}
    overlapping = [job for job in context['jobs'] if job.start_at < end and job.end_at > start]
    busy = set().union(*(context['job_staff'][job.pk] for job in overlapping)) if overlapping else set()
    busy_posts = {job.post_id for job in overlapping}
    free_posts = [post for post in context['posts'] if post.id not in busy_posts]
    if context['posts'] and not free_posts:
        return [], start, end
    candidates = []
    for employee in context['employees']:
        allowed = {row.service_id for row in employee.service_settings.all() if row.tenant_id==tenant.id}
        shift = context['shifts'].get(employee.id)
        if not ids.issubset(allowed) or not shift or start.time()<shift.start_time or end.time()>shift.end_time or employee.id in busy:
            continue
        candidates.append((context['workload'].get(employee.id, 0), employee, free_posts[0] if free_posts else None))
    candidates.sort(key=lambda row: (row[0], row[1].sort_order, row[1].name, str(row[1].id)))
    return candidates, start, end


def day_slots(tenant,services,selected_date,employee_id='',*,context=None,vehicle_class=None):
    context=context or availability_context(tenant,selected_date,employee_id)
    hours=context['hours'];slots=[]
    if hours and not hours.is_closed:
        cursor=datetime.combine(selected_date,hours.opens_at).replace(second=0,microsecond=0)
        cursor+=timedelta(minutes=(-cursor.minute)%15)
        while cursor.time()<hours.closes_at:
            candidates,_,_=available_assignments(tenant,services,selected_date,cursor.time(),employee_id,context=context,vehicle_class=vehicle_class)
            if candidates: slots.append(cursor.strftime('%H:%M'))
            cursor+=timedelta(minutes=15)
    return slots


def nearest_availability(tenant,services,selected_date,employee_id='',*,any_service=False,vehicle_class=None):
    today=timezone.localtime(timezone.now(),ZoneInfo(tenant.timezone)).date()
    shifts=EmployeeShift.objects.filter(tenant=tenant,location=ensure_primary_location(tenant),
        employee__active=True,date__gte=max(today,selected_date),date__lte=today+timedelta(days=90))
    if employee_id: shifts=shifts.filter(employee_id=employee_id)
    closed=set(tenant.working_hours.filter(is_closed=True).values_list('weekday',flat=True))
    for day in shifts.order_by('date').values_list('date',flat=True).distinct():
        if day.weekday() in closed: continue
        context=availability_context(tenant,day,employee_id)
        groups=([service] for service in sorted(services,key=lambda s:s.duration_for_class(vehicle_class))) if any_service else [services]
        for group in groups:
            slots=day_slots(tenant,group,day,employee_id,context=context,vehicle_class=vehicle_class)
            if slots: return day,slots
    return None,[]


def availability_view(request, token):
    channel=resolve_channel(token)
    if sync_subscription(channel.tenant).status in {'suspended','cancelled'}:
        return JsonResponse({'slots':[],'error':'Онлайн-запись временно недоступна.'},status=403)
    try:
        selected_date=date.fromisoformat(request.GET.get('date',''))
        ids=request.GET.getlist('services')
        if not ids or len(ids)>50 or len(ids)!=len(set(ids)):
            raise ValueError
        location=ensure_primary_location(channel.tenant)
        services=list(Service.objects.filter(tenant=channel.tenant,location=location,active=True,pk__in=ids).exclude(system_key='other').prefetch_related('vehicle_class_prices'))
        if len(services)!=len(ids):
            raise ValueError
        vehicle_class = None
        if request.GET.get('vehicle_class'):
            vehicle_class = VehicleClass.objects.filter(tenant=channel.tenant, active=True, pk=request.GET['vehicle_class']).first()
            if not vehicle_class:
                raise ValueError
        employee_id=request.GET.get('employee','')
        if employee_id and not Employee.objects.filter(tenant=channel.tenant,location=location,active=True,pk=employee_id).exists():
            raise ValueError
        if request.GET.get('nearest')=='1':
            found,slots=nearest_availability(channel.tenant,services,selected_date,employee_id,vehicle_class=vehicle_class)
            selected_date=found or selected_date
        else: slots=day_slots(channel.tenant,services,selected_date,employee_id,vehicle_class=vehicle_class)
        return JsonResponse({'slots':slots,'date':selected_date.isoformat(),'duration':sum(s.duration_for_class(vehicle_class) for s in services)})
    except (ValueError,ValidationError,TypeError,OverflowError):
        return JsonResponse({'slots':[],'error':'Проверьте услуги, дату и исполнителя.'},status=400)


class OnlineBookingForm(forms.Form):
    vehicle_class=forms.ModelChoiceField(label='Класс автомобиля',queryset=VehicleClass.objects.none())
    services=forms.ModelMultipleChoiceField(label='Услуги',queryset=Service.objects.none())
    employee=forms.ModelChoiceField(label='Исполнитель',queryset=Employee.objects.none(),required=False)
    date=forms.DateField(label='Дата',widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}),input_formats=['%Y-%m-%d'])
    time=forms.TimeField(label='Время',input_formats=['%H:%M'],widget=forms.Select())
    client_name=forms.CharField(label='Имя',max_length=160)
    last_name=forms.CharField(label='Фамилия',max_length=160,required=False)
    phone=PhoneField(label='Телефон')
    make=forms.CharField(label='Марка',max_length=80,required=False)
    model=forms.CharField(label='Модель',max_length=80,required=False)
    plate=forms.CharField(label='Госномер',max_length=24,required=False)
    comment=forms.CharField(label='Комментарий',max_length=2000,required=False,widget=forms.Textarea(attrs={'rows':3}))
    submission_token=forms.CharField(widget=forms.HiddenInput)
    def __init__(self,*args,channel,**kwargs):
        self.channel=channel
        super().__init__(*args,**kwargs)
        classes,services,employees,_=booking_catalog(channel)
        self.fields['vehicle_class'].queryset=VehicleClass.objects.filter(id__in=[c.id for c in classes])
        self.fields['services'].queryset=Service.objects.filter(id__in=[s.id for s in services]).prefetch_related('vehicle_class_prices')
        self.fields['employee'].queryset=Employee.objects.filter(id__in=[e.id for e in employees])
        self.fields['time'].widget.choices=[(f'{h:02}:{m:02}',f'{h:02}:{m:02}') for h in range(24) for m in range(0,60,15)]
        if channel.tenant.language_code=='en':
            labels={'vehicle_class':'Vehicle class','services':'Services','employee':'Specialist','date':'Date','time':'Time','client_name':'First name','last_name':'Last name','phone':'Phone','make':'Make','model':'Model','plate':'License plate','comment':'Comment'}
            for key,label in labels.items():
                self.fields[key].label=label
    def clean_phone(self):
        return self.cleaned_data['phone']
    def clean_submission_token(self):
        try:
            value=signing.loads(self.cleaned_data['submission_token'],salt='online-booking',max_age=7200)
            if value['channel']!=str(self.channel.pk):
                raise ValueError
            return value['nonce']
        except (signing.BadSignature,KeyError,ValueError):
            raise forms.ValidationError('Обновите форму и попробуйте снова.')
    def clean(self):
        data=super().clean()
        existing = IntegrationLead.objects.filter(tenant=self.channel.tenant, source='website', external_id=data.get('submission_token','')).exists() if data.get('submission_token') else False
        if not existing and all(data.get(k) for k in ['services','date','time']):
            candidates,_,_=available_assignments(self.channel.tenant,list(data['services']),data['date'],data['time'],str(data['employee'].id) if data.get('employee') else '',vehicle_class=data.get('vehicle_class'))
            if not candidates:
                raise forms.ValidationError('Время недоступно для выбранных услуг. Выберите другой слот.')
        return data


@transaction.atomic
def submit_booking(channel, data):
    tenant=Tenant.objects.select_for_update().get(pk=channel.tenant_id)
    existing=IntegrationLead.objects.filter(tenant=tenant,source='website',external_id=data['submission_token']).first()
    if existing:
        return Appointment.objects.get(tenant=tenant,pk=existing.payload['appointment_id'])
    if sync_subscription(tenant).status in {'suspended','cancelled'}:
        raise ValidationError('Онлайн-запись временно недоступна.')
    services=list(data['services'])
    candidates,start,end=available_assignments(tenant,services,data['date'],data['time'],str(data['employee'].id) if data.get('employee') else '',vehicle_class=data['vehicle_class'])
    if not candidates:
        raise ValidationError('Это время уже заняли. Выберите другое.')
    _,employee,post=candidates[0]
    appointment=create_appointment(tenant=tenant,location=ensure_primary_location(tenant),cleaned_data={
        'client_first_name':data['client_name'],'client_last_name':data['last_name'],'client_phone':data['phone'],
        'car_make':data['make'],'car_model':data['model'],'car_plate':data['plate'],
        'car_vehicle_class':data['vehicle_class'],'service_classes':{str(s.id):data['vehicle_class'] for s in services},
        'services':services,'employee':employee,'post':post,'start_at':start,'end_at':end,
        'comment':data['comment'],'source':'website'})
    lead=IntegrationLead.objects.create(tenant=tenant,channel=channel,source='website',status='processed',external_id=data['submission_token'],client_name=data['client_name'],phone=data['phone'],car=appointment.car_display if appointment.car_id else '',service_name=', '.join(s.name for s in services)[:180],preferred_at=start,comment=data['comment'],payload={'schema_version':2,'appointment_id':str(appointment.pk),'employee_id':str(employee.pk),'services':[str(s.pk) for s in services]})
    recipients={row.user for row in tenant.memberships.filter(role='owner',status='active').select_related('user')}
    if employee.user:
        recipients.add(employee.user)
    for user in recipients:
        notify(tenant=tenant,recipient=user,kind='online_booking',title='Новая онлайн-запись',message=f'{appointment.client_display_name} · {start:%d.%m %H:%M}',url=reverse('appointment-detail',kwargs={'appointment_id':appointment.pk},urlconf='config.urls'),dedupe_key=f'online-booking:{appointment.pk}')
    log_event(tenant=tenant,actor=None,action='online_booking',entity=appointment,description='Автоматически создана онлайн-запись')
    return appointment


def class_image(request,token,class_id):
    channel=resolve_channel(token)
    cls=get_object_or_404(VehicleClass,tenant=channel.tenant,pk=class_id,active=True)
    if not cls.image:
        raise Http404
    try:
        response=FileResponse(cls.image.open('rb'),content_type=mimetypes.guess_type(cls.image.name)[0] or 'application/octet-stream')
        response['X-Content-Type-Options']='nosniff'
        response['Cache-Control']='private, max-age=300'
        return response
    except OSError:
        raise Http404
