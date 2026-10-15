import re
from html import escape
from django import forms
from django.conf import settings
from django.contrib import messages
from django.db.models import Q, Count, Prefetch
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.html import strip_tags
from django.views.decorators.http import require_safe
from .models import KnowledgeCategory, KnowledgeArticle
from .legal import sanitize_content
from .platform import platform_required
from .images import optimize_image
from .forms import validate_uploaded_image


def public_articles(*, with_content=False):
    articles = KnowledgeArticle.objects.filter(published=True).select_related('category')
    return articles if with_content else articles.defer('content')


def context(*, section_id=None):
    sections = KnowledgeCategory.objects.all()
    if section_id is not None:
        sections = sections.filter(pk=section_id)
    return {'sections':sections.annotate(article_count=Count('articles',filter=Q(articles__published=True))).filter(article_count__gt=0).order_by('sort_order','name').prefetch_related(Prefetch('articles',queryset=public_articles(),to_attr='published_articles')),
            'support_domain':settings.SUPPORT_DOMAIN,'support_email':settings.SUPPORT_EMAIL}


@require_safe
def index(request):
    return render(request,'support/index.html',{**context(),'featured':public_articles().filter(featured=True)[:6]})


@require_safe
def category(request,slug):
    section=get_object_or_404(KnowledgeCategory,slug=slug)
    articles=public_articles().filter(category=section)
    if not articles.exists():raise Http404
    return render(request,'support/category.html',{**context(),'section':section,'articles':articles})


@require_safe
def search(request):
    query=request.GET.get('q','').strip()[:160]
    articles=public_articles()
    words=re.findall(r'[\w-]+',query)[:10]
    if words:
        for word in words: articles=articles.filter(Q(title__icontains=word)|Q(summary__icontains=word)|Q(content__icontains=word)|Q(category__name__icontains=word))
    else:articles=articles.none()
    return render(request,'support/search.html',{**context(),'query':query,'results':articles[:50]})


def article_content(content):
    """Assign stable IDs to sanitized headings for the article table of contents."""
    headings=[]
    def heading(match):
        number=len(headings)+1
        headings.append({'id':f'section-{number}','title':strip_tags(match[2])})
        return f'<h{match[1]} id="section-{number}">{match[2]}</h{match[1]}>'
    safe=sanitize_content(content)
    return re.sub(r'<h([23])>(.*?)</h\1>',heading,safe,flags=re.S),headings


@require_safe
def article(request,slug):
    item=get_object_or_404(public_articles(with_content=True),slug=slug)
    body,toc=article_content(item.content)
    return render(request,'support/article.html',{**context(section_id=item.category_id),'article':item,'body':body,'toc':toc,
        'related':public_articles().filter(category=item.category).exclude(pk=item.pk)[:4]})


@require_safe
def article_image(request,article_id):
    item=get_object_or_404(public_articles(),pk=article_id)
    if not item.screenshot:raise Http404
    try:
        import mimetypes
        response=FileResponse(item.screenshot.open('rb'),content_type=mimetypes.guess_type(item.screenshot.name)[0] or 'image/webp')
    except OSError as exc:raise Http404 from exc
    response['Cache-Control']='public, max-age=3600';response['X-Content-Type-Options']='nosniff'
    return response


@require_safe
def sitemap(request):
    sections = KnowledgeCategory.objects.filter(articles__published=True).order_by('sort_order','name').values_list('slug',flat=True).distinct()
    paths=['/']+['/section/'+slug+'/' for slug in sections]+['/articles/'+slug+'/' for slug in public_articles().values_list('slug',flat=True)]
    entries=''.join('<url><loc>'+escape('https://'+settings.SUPPORT_DOMAIN+path)+'</loc></url>' for path in paths)
    return HttpResponse('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+entries+'</urlset>',content_type='application/xml')


@require_safe
def robots(request):
    return HttpResponse('User-agent: *\nAllow: /\nDisallow: /search/\nSitemap: https://'+settings.SUPPORT_DOMAIN+'/sitemap.xml\n',content_type='text/plain')


class ArticleForm(forms.ModelForm):
    screenshot=forms.FileField(label='Скриншот',required=False,widget=forms.FileInput(attrs={'accept':'image/jpeg,image/png,image/webp'}))
    remove_screenshot=forms.BooleanField(label='Убрать скриншот',required=False)
    class Meta:
        model=KnowledgeArticle
        fields=('category','title','slug','summary','content','screenshot','screenshot_caption','published','featured','sort_order')
        widgets={'content':forms.Textarea(attrs={'rows':16}), 'summary':forms.Textarea(attrs={'rows':2})}
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['slug'].help_text = 'Адрес статьи: латинские буквы, цифры и дефисы. Изменение адреса сделает прежнюю ссылку недоступной.'
    def clean_content(self):
        body=sanitize_content(self.cleaned_data['content'])
        if not strip_tags(body).strip():raise forms.ValidationError('Добавьте текст статьи.')
        return body
    def clean_screenshot(self):
        return optimize_image(validate_uploaded_image(self.cleaned_data.get('screenshot')),kind='knowledge')


@platform_required
def manage(request):
    return render(request,'platform/knowledge_list.html',{'articles':KnowledgeArticle.objects.select_related('category')})


@platform_required
def edit(request,article_id=None):
    item=get_object_or_404(KnowledgeArticle,pk=article_id) if article_id else KnowledgeArticle()
    form=ArticleForm(request.POST or None,request.FILES or None,instance=item)
    if request.method=='POST' and form.is_valid():
        saved=form.save(commit=False)
        if form.cleaned_data.get('remove_screenshot'):
            saved.screenshot='';saved.default_screenshot=''
        saved.save()
        from apps.tenants.models import PlatformAudit
        PlatformAudit.objects.create(actor=request.user,action='knowledge_update',details={'article_id':saved.pk,'title':saved.title,'published':saved.published})
        messages.success(request,'Статья сохранена.' if not saved.published else 'Статья опубликована в базе знаний.')
        return redirect('platform-knowledge')
    editor_content=sanitize_content(form['content'].value() or '')
    return render(request,'platform/knowledge_edit.html',{'form':form,'article':item,'editor_content':editor_content,'support_domain':settings.SUPPORT_DOMAIN})
