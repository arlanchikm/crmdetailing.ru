import csv
import io
from collections import Counter

from django.contrib import messages
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.tenants.access import tenant_owner_required
from .models import ImportBatch
from .imports import (KINDS, FIELDS, UploadImportForm, MappingForm, read_table,
    suggested_mapping, preview, apply_import, undo_import)


@tenant_owner_required
def import_index(request):
    form=UploadImportForm(request.POST or None,request.FILES or None)
    if request.method=='POST' and form.is_valid():
        try: columns,rows=read_table(form.cleaned_data['file'],form.cleaned_data['sheet'])
        except ValueError as exc: form.add_error('file',str(exc))
        else:
            batch=ImportBatch.objects.create(tenant=request.tenant,location=request.current_location,
                actor=request.user,kind=form.cleaned_data['kind'],filename=form.cleaned_data['file'].name[:160],
                columns=columns,rows=rows,mapping=suggested_mapping(form.cleaned_data['kind'],columns))
            return redirect('import-detail',batch_id=batch.pk)
    batches=Paginator(ImportBatch.objects.filter(tenant=request.tenant).select_related('actor'),30).get_page(request.GET.get('page'))
    return render(request,'core/import_index.html',{'upload_form':form,'import_kinds':KINDS.items(),'batches':batches})


@tenant_owner_required
def import_detail(request,batch_id):
    batch=get_object_or_404(ImportBatch,tenant=request.tenant,pk=batch_id)
    initial={'map_'+key:value for key,value in batch.mapping.items()};initial['duplicate_mode']=batch.duplicate_mode
    form=MappingForm(request.POST if request.POST.get('action')=='preview' else None,batch=batch,initial=initial)
    if request.method=='POST':
        action=request.POST.get('action')
        try:
            if action=='preview' and batch.status in {'uploaded','preview'} and form.is_valid():
                batch.mapping={key[4:]:value for key,value in form.cleaned_data.items() if key.startswith('map_')}
                batch.duplicate_mode=form.cleaned_data['duplicate_mode'];batch.results=preview(batch)
                batch.status='preview';batch.save(update_fields=['mapping','duplicate_mode','results','status'])
                return redirect('import-detail',batch_id=batch.pk)
            if action=='apply':
                applied=apply_import(batch,request.user,skip_errors=request.POST.get('skip_errors')=='on')
                messages.success(request,'Импорт завершён.') if applied else messages.warning(request,'Данные CRM изменились. Проверьте обновлённый результат и подтвердите ещё раз.')
                return redirect('import-detail',batch_id=batch.pk)
            if action=='undo':
                undo_import(batch,request.user);messages.success(request,'Импорт отменён.')
                return redirect('import-detail',batch_id=batch.pk)
        except (ValueError,ValidationError,IntegrityError) as exc:
            messages.error(request,str(exc) if not isinstance(exc,IntegrityError) else 'Данные изменились или появились дубли. Повторите проверку таблицы.')
    return render(request,'core/import_detail.html',{'batch':batch,'mapping_form':form,
        'kind_name':KINDS[batch.kind],'preview_rows':batch.results[:100],
        'sample_rows':batch.rows[:3],'counts':dict(Counter(row['action'] for row in batch.results)),
        'row_count':len(batch.rows)})


def safe_cell(value):
    value=str(value)
    return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value


@tenant_owner_required
def import_report(request,batch_id):
    batch=get_object_or_404(ImportBatch,tenant=request.tenant,pk=batch_id)
    output=io.StringIO();writer=csv.writer(output,delimiter=';')
    writer.writerow(['Строка','Запись','Результат','Пояснение'])
    names={'create':'Создать','update':'Обновить','skip':'Пропустить','error':'Ошибка'}
    for row in batch.results:
        writer.writerow([row['row'],safe_cell(row.get('label','')),names[row['action']],safe_cell(row.get('message',''))])
    response=HttpResponse('\ufeff'+output.getvalue(),content_type='text/csv; charset=utf-8')
    response['Content-Disposition']=f'attachment; filename="import-{batch.pk}-report.csv"'
    return response


EXAMPLES={
 'clients':['Иван','Петров','+7 (800) 555-35-35','ivan@example.com','Предпочитает утренние записи'],
 'cars':['+7 (800) 555-35-35','А123ВС77','WBA12345678901234','Седан','BMW','3','Белый',''],
 'employees':['Михаил','','Детейлер','да','service_percent','30','0'],
 'consumables':['Шампунь','Химия','л','900','10','10','2','да']}


@tenant_owner_required
def import_template(request,kind):
    if kind not in KINDS: raise Http404
    columns=[label for _,label,_ in FIELDS[kind]]
    if request.GET.get('format')=='xlsx':
        from openpyxl import Workbook
        workbook=Workbook();sheet=workbook.active;sheet.title='Импорт'
        sheet.append(columns);sheet.append(EXAMPLES[kind]);sheet.freeze_panes='A2'
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width=27
            for cell in column: cell.number_format='@'
        output=io.BytesIO();workbook.save(output)
        response=HttpResponse(output.getvalue(),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition']=f'attachment; filename="{kind}-template.xlsx"'
    else:
        output=io.StringIO();writer=csv.writer(output,delimiter=';');writer.writerow(columns);writer.writerow(EXAMPLES[kind])
        response=HttpResponse('\ufeff'+output.getvalue(),content_type='text/csv; charset=utf-8')
        response['Content-Disposition']=f'attachment; filename="{kind}-template.csv"'
    return response


@tenant_owner_required
def import_guide(request,kind):
    if kind not in KINDS: raise Http404
    return render(request,'core/import_guide.html',{'kind':kind,'kind_name':KINDS[kind],'fields':FIELDS[kind]})
