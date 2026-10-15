"""Independent appearance settings for the public online-booking form."""
import io
import re
import uuid
from django import forms
from django.core.files.base import ContentFile
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from django.utils import timezone
from django.views.decorators.http import require_safe
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.utils.translation import override
from datetime import date
from PIL import Image, ImageOps
from apps.tenants.access import tenant_owner_required
from .models import IntegrationChannel, log_event

COLOR = re.compile(r'^#[0-9a-fA-F]{6}$')
EDITABLE_FIELDS = ('accent_color', 'page_color', 'text_color')
SAVED_COLOR_FIELDS = (*EDITABLE_FIELDS, 'block_color')


def appearance(channel):
    defaults = {'accent_color':channel.tenant.primary_color,'page_color':channel.tenant.background_color,
                'block_color':'#FFFFFF','text_color':'#102C51','overlay':15}
    saved=(channel.settings or {}).get('appearance',{})
    for name in SAVED_COLOR_FIELDS:
        if COLOR.fullmatch(str(saved.get(name,''))):defaults[name]=saved[name]
    try: defaults['overlay']=max(0,min(100,int(saved.get('overlay',15))))
    except (ValueError,TypeError):pass
    return defaults


class BookingAppearanceForm(forms.Form):
    accent_color=forms.RegexField(COLOR,label='Кнопки и акцент',widget=forms.TextInput(attrs={'type':'color'}))
    page_color=forms.RegexField(COLOR,label='Фон страницы',widget=forms.TextInput(attrs={'type':'color'}))
    text_color=forms.RegexField(COLOR,label='Текст',widget=forms.TextInput(attrs={'type':'color'}))
    overlay=forms.IntegerField(label='Затемнение фона',min_value=0,max_value=100,widget=forms.NumberInput(attrs={'type':'range','min':0,'max':100,'step':5}))
    background=forms.ImageField(label='Изображение фона',required=False,widget=forms.FileInput(attrs={'accept':'image/jpeg,image/png,image/webp'}))
    remove_background=forms.BooleanField(label='Убрать свой фон',required=False)

    def clean_background(self):
        image=self.cleaned_data.get('background')
        if not image:return None
        if image.size>10*1024*1024:raise forms.ValidationError('Изображение должно быть не больше 10 МБ.')
        try:
            image.seek(0)
            with Image.open(image) as source:
                if source.format not in {'JPEG','PNG','WEBP'}:raise ValueError()
                if source.width*source.height>40_000_000:raise ValueError()
                source=ImageOps.exif_transpose(source)
                source.thumbnail((2560,2560))
                output=io.BytesIO()
                source.convert('RGBA' if 'A' in source.getbands() else 'RGB').save(output,format='WEBP',quality=90)
            return ContentFile(output.getvalue(),name=str(uuid.uuid4())+'.webp')
        except (OSError,ValueError,Image.DecompressionBombError):
            raise forms.ValidationError('Загрузите корректное изображение JPG, PNG или WebP до 40 мегапикселей.')


def appearance_context(channel):
    from django.urls import reverse
    config=appearance(channel)
    from django.templatetags.static import static
    config['default_background_url']=static('img/interface-background.webp')
    config['background_url']=reverse('booking-background',kwargs={'token':channel.public_token})+'?v='+channel.updated_at.strftime('%Y%m%d%H%M%S%f') if channel.background_image else config['default_background_url']
    return {'booking_appearance':config}


@tenant_owner_required
def appearance_editor(request):
    channel,_=IntegrationChannel.objects.get_or_create(tenant=request.tenant,kind='website')
    form=BookingAppearanceForm(request.POST or None,request.FILES or None,initial=appearance(channel))
    if request.method=='POST':
        if request.POST.get('action')=='reset':
            settings=dict(channel.settings or {});settings.pop('appearance',None)
            channel.settings=settings;channel.background_image='';channel.save(update_fields=('settings','background_image','updated_at'))
            log_event(tenant=request.tenant,actor=request.user,action='booking_appearance_reset',entity=channel,description='Оформление формы записи сброшено')
            messages.success(request,'Оформление формы сброшено.')
            return redirect('booking-appearance')
        if form.is_valid():
            saved_appearance=dict((channel.settings or {}).get('appearance') or {})
            saved_appearance.update({name:form.cleaned_data[name] for name in (*EDITABLE_FIELDS,'overlay')})
            channel.settings={**(channel.settings or {}),'appearance':saved_appearance}
            if form.cleaned_data['remove_background']:channel.background_image=''
            if form.cleaned_data['background']:channel.background_image=form.cleaned_data['background']
            channel.save(update_fields=('settings','background_image','updated_at'))
            log_event(tenant=request.tenant,actor=request.user,action='booking_appearance_update',entity=channel,description='Оформление формы записи изменено')
            messages.success(request,'Оформление формы сохранено.')
            return redirect('booking-appearance')
    return render(request,'core/booking_appearance.html',{'appearance_form':form,'website':channel,**appearance_context(channel)})


@require_safe
@xframe_options_sameorigin
@tenant_owner_required
def appearance_preview(request):
    from django.core import signing
    from .booking import booking_catalog, OnlineBookingForm, nearest_availability
    from .booking_location import location_context
    channel,_=IntegrationChannel.objects.get_or_create(tenant=request.tenant,kind='website')
    with override(channel.tenant.language_code),timezone.override(channel.tenant.timezone):
        _,services,_,catalog=booking_catalog(channel)
        today=date.fromisoformat(catalog['today'])
        day,_=nearest_availability(channel.tenant,services,today,any_service=True) if services else (None,[])
        catalog['initial_date']=(day or today).isoformat()
        token=signing.dumps({'channel':str(channel.pk),'nonce':str(uuid.uuid4())},salt='online-booking')
        form=OnlineBookingForm(channel=channel,initial={'submission_token':token,'date':day or today})
    return render(request,'core/booking_widget.html',{'tenant':channel.tenant,'form':form,'catalog':catalog,
        **location_context(channel.tenant), 'is_preview':True,'widget_title':(channel.settings or {}).get('widget_title','Онлайн-запись'),**appearance_context(channel)})


@require_safe
def background_image(request,token):
    channel=get_object_or_404(IntegrationChannel,public_token=token,kind='website')
    if not channel.enabled and not (getattr(request,'membership',None) and request.membership.role=='owner' and request.tenant==channel.tenant):
        raise Http404
    if not channel.background_image:raise Http404
    try:response=FileResponse(channel.background_image.open('rb'),content_type='image/webp')
    except OSError as exc:raise Http404 from exc
    response['X-Content-Type-Options']='nosniff';response['Cache-Control']='private, max-age=300'
    return response
