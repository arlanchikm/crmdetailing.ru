from io import BytesIO
from tempfile import TemporaryDirectory
from PIL import Image
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from apps.tenants.models import PlatformAccess, PlatformAudit
from .knowledge import ArticleForm
from .models import KnowledgeArticle, KnowledgeCategory


@override_settings(ALLOWED_HOSTS=['support.crmdetailing.ru','platform.crmdetailing.ru','crmdetailing.ru'], PRIMARY_DOMAIN='crmdetailing.ru', SUPPORT_DOMAIN='support.crmdetailing.ru', PLATFORM_DOMAIN='platform.crmdetailing.ru')
class KnowledgeTests(TestCase):
    def setUp(self):
        self.category=KnowledgeCategory.objects.create(slug='appointments',name='Записи')
        self.article=KnowledgeArticle.objects.create(category=self.category,slug='create-appointment',title='Создание записи',summary='Услуги и клиент',content='<h2>Порядок действий</h2><p>Выберите клиента</p>',published=True,featured=True)
        self.draft=KnowledgeArticle.objects.create(category=self.category,slug='private-draft',title='Скрытая инструкция',content='<p>Секретный черновик</p>')
        self.media=TemporaryDirectory();self.addCleanup(self.media.cleanup)
        self.override=override_settings(MEDIA_ROOT=self.media.name);self.override.enable();self.addCleanup(self.override.disable)

    def get(self,path,**kwargs):
        return self.client.get(path,HTTP_HOST='support.crmdetailing.ru',**kwargs)

    def test_public_pages_and_draft_isolation(self):
        for path in ['/', '/section/appointments/', '/articles/create-appointment/']:
            r=self.get(path);self.assertEqual(r.status_code,200);self.assertContains(r,'Создание записи');self.assertNotContains(r,'Скрытая инструкция')
        self.assertEqual(self.get('/articles/private-draft/').status_code,404)
        self.assertEqual(self.get('/section/missing/').status_code,404)
        self.assertEqual(self.get('/knowledge/').status_code,404)
        self.assertEqual(self.client.get('/articles/create-appointment/',HTTP_HOST='crmdetailing.ru').status_code,404)

    def test_search_and_escaping(self):
        self.assertContains(self.get('/search/',data={'q':'Создание клиент'}),'Создание записи')
        r=self.get('/search/',data={'q':'несуществующее'});self.assertNotContains(r,'kb-article-link')
        r=self.get('/search/',data={'q':'<script>alert(1)</script>'});self.assertNotContains(r,'<script>alert(1)</script>')
        self.assertNotContains(self.get('/search/',data={'q':'Скрытая'}),'Скрытая инструкция')

    def test_sanitization_and_heading_links(self):
        self.article.content='<h2 onclick="bad()">Шаги</h2><script>alert(1)</script><p>Текст</p><a href="javascript:alert(1)">Ссылка</a>'
        self.article.save()
        r=self.get('/articles/create-appointment/')
        self.assertContains(r,'id="section-1"');self.assertContains(r,'href="#section-1"')
        self.assertNotContains(r,'onclick');self.assertNotContains(r,'javascript:');self.assertNotContains(r,'alert(1)')

    def test_sitemap_and_robots(self):
        r=self.get('/sitemap.xml');self.assertContains(r,'https://support.crmdetailing.ru/articles/create-appointment/');self.assertNotContains(r,'private-draft')
        self.assertContains(self.get('/robots.txt'),'Disallow: /search/')
        self.assertEqual(self.get('/health/').status_code,200)

    def test_editor_requires_platform_access(self):
        self.assertEqual(self.client.get('/knowledge/',HTTP_HOST='platform.crmdetailing.ru').status_code,302)
        user=get_user_model().objects.create_user('reader@example.com','test-password');self.client.force_login(user)
        self.assertEqual(self.client.get('/knowledge/',HTTP_HOST='platform.crmdetailing.ru').status_code,302)
        PlatformAccess.objects.create(user=user,email=user.email,email_2fa=True)
        self.assertEqual(self.client.get('/knowledge/',HTTP_HOST='platform.crmdetailing.ru').status_code,302)
        s=self.client.session;s['platform_2fa_user']=str(user.pk);s.save()
        self.assertEqual(self.client.get('/knowledge/',HTTP_HOST='platform.crmdetailing.ru').status_code,200)
        r=self.client.post('/knowledge/new/',{'category':self.category.pk,'title':'Новая инструкция','slug':'new-guide','summary':'Описание','content':'<h2>Начало</h2><script>alert(1)</script><p>Текст</p>','sort_order':0,'published':'on'},HTTP_HOST='platform.crmdetailing.ru')
        self.assertEqual(r.status_code,302)
        item=KnowledgeArticle.objects.get(slug='new-guide');self.assertTrue(item.published);self.assertNotIn('script',item.content)
        self.assertTrue(PlatformAudit.objects.filter(action='knowledge_update').exists())
        self.assertEqual(self.client.get(f'/knowledge/{item.pk}/',HTTP_HOST='platform.crmdetailing.ru').status_code,200)

    def test_optimized_image_published_only(self):
        image=BytesIO();Image.new('RGB',(1900,1000),'white').save(image,'PNG')
        form=ArticleForm(data={'category':self.category.pk,'title':'Фото','slug':'photo','summary':'Фото','content':'<p>Текст</p>','sort_order':0,'published':'on'},files={'screenshot':SimpleUploadedFile('example.png',image.getvalue(),content_type='image/png')})
        self.assertTrue(form.is_valid(),form.errors);item=form.save()
        self.assertTrue(item.screenshot.name.endswith('.webp'))
        with Image.open(item.screenshot.path) as optimized:self.assertEqual(optimized.width,1600)
        r=self.get(f'/images/{item.pk}/');self.assertEqual(r.status_code,200);self.assertEqual(r['Content-Type'],'image/webp');self.assertTrue(b''.join(r.streaming_content))
        item.published=False;item.save();self.assertEqual(self.get(f'/images/{item.pk}/').status_code,404)
        form=ArticleForm(data={'category':self.category.pk,'title':'Неверный файл','slug':'bad','content':'<p>Текст</p>','sort_order':0},files={'screenshot':SimpleUploadedFile('bad.svg',b'<svg/>',content_type='image/svg+xml')})
        self.assertFalse(form.is_valid());self.assertIn('screenshot',form.errors)

    def test_seed_idempotent_preserves_editor_changes(self):
        call_command('seed_knowledge_base')
        self.article.title='Правка владельца';self.article.published=False;self.article.save()
        count=KnowledgeArticle.objects.count();call_command('seed_knowledge_base')
        self.article.refresh_from_db();self.assertEqual(self.article.title,'Правка владельца');self.assertFalse(self.article.published)
        self.assertEqual(KnowledgeArticle.objects.count(),count)
        self.assertEqual(KnowledgeCategory.objects.count(),8)
