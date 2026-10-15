from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from apps.tenants.models import PlatformAccess
from .models import LegalDocument
from .legal import sanitize_content


@override_settings(DEBUG=True, ALLOWED_HOSTS=['testserver', 'localhost', '.localhost'], TENANT_DOMAIN_SUFFIXES=('.localhost', '.crmdetailing.ru'))
class LegalDocumentTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user('legal-admin@example.com', 'test-legal-password')
        PlatformAccess.objects.create(user=self.admin)
        self.host = {'HTTP_HOST': 'platform.localhost'}

    def test_non_platform_user_cannot_edit(self):
        self.client.force_login(get_user_model().objects.create_user('ordinary@example.com', 'test-password'))
        response = self.client.post('/legal/offer/', {'title':'Changed', 'content':'<p>Changed</p>'}, **self.host)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(LegalDocument.objects.exists())

    def test_defaults_remain_until_saved_and_revisions_are_retained(self):
        original = self.client.get('/legal/offer/', HTTP_HOST='localhost')
        self.assertEqual(original.status_code, 200)
        self.client.force_login(self.admin)
        editor = self.client.get('/legal/offer/', **self.host)
        self.assertContains(editor, 'data-legal-editor')
        self.assertFalse(LegalDocument.objects.exists())
        for text in ['First edition', 'Second edition']:
            response = self.client.post('/legal/offer/', {'title':'Offer', 'content':f'<h2>Section</h2><p>{text}</p>'}, **self.host)
            self.assertEqual(response.status_code, 302)
        document = LegalDocument.objects.get(slug='offer')
        self.assertEqual(document.revisions.count(), 3)
        self.assertIn('First edition', document.revisions.get(version__endswith='-r1').content)
        response = self.client.get('/legal/offer/', HTTP_HOST='localhost')
        self.assertContains(response, 'Second edition')
        self.assertContains(response, document.version)
        self.assertNotContains(response, 'First edition')

    def test_editor_removes_scripts_and_unsafe_links(self):
        self.client.force_login(self.admin)
        payload='<script>alert(1)</script><h2 onclick="alert(1)">Heading</h2><a href="javascript:alert(1)">Link</a><p>Text</p>'
        self.client.post('/legal/privacy/', {'title':'Policy', 'content':payload}, **self.host)
        content=LegalDocument.objects.get(slug='privacy').content
        self.assertNotIn('script', content)
        self.assertNotIn('onclick', content)
        self.assertNotIn('javascript', content)
        self.assertIn('<h2>Heading</h2>', content)
        self.assertIn('https://example.com', sanitize_content('<a href="https://example.com">Safe</a>'))

    def test_registration_displays_and_records_current_document_version(self):
        from apps.accounts.tests import CompanyRegistrationTests
        from apps.accounts.models import LegalAcceptance
        LegalDocument.objects.create(slug='offer', title='Offer', content='<p>Current offer</p>', version='05.10.2026-r1')
        self.assertContains(self.client.get('/register/', HTTP_HOST='localhost'), '05.10.2026-r1')
        response = self.client.post('/register/', CompanyRegistrationTests.registration_data(self), HTTP_HOST='localhost')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(LegalAcceptance.objects.get(document_type=LegalAcceptance.DocumentType.OFFER).document_version, '05.10.2026-r1')
