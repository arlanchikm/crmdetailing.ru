from decimal import Decimal
from urllib.parse import parse_qs, urlsplit
from django.test import SimpleTestCase
from apps.tenants.models import Tenant
from .booking_location import location_context
from .forms import CompanySettingsForm


class BookingLocationTests(SimpleTestCase):
    def test_address_is_encoded_for_map_and_routes(self):
        tenant = Tenant(name='Мойка', address='Ульяновск, 2-й пер. Мира, 13А & вход №1')
        links = location_context(tenant)['booking_location']
        self.assertEqual(parse_qs(urlsplit(links['embed_url']).query)['text'], [tenant.address])
        self.assertEqual(parse_qs(urlsplit(links['yandex_url']).query)['rtext'], ['~' + tenant.address])
        self.assertEqual(parse_qs(urlsplit(links['google_url']).query)['destination'], [tenant.address])
        self.assertEqual(parse_qs(urlsplit(links['apple_url']).query)['daddr'], [tenant.address])
        self.assertEqual(parse_qs(urlsplit(links['navigator_url']).query)['text'], [tenant.address])
        self.assertTrue(links['navigator_search'])
        self.assertEqual(parse_qs(urlsplit(links['yandex_app_url']).query)['rtext'], ['~' + tenant.address])
        self.assertEqual(parse_qs(urlsplit(links['apple_app_url']).query)['daddr'], [tenant.address])
        from urllib.parse import unquote
        self.assertIn('S.browser_fallback_url=' + links['google_url'], unquote(links['android_url']))

    def test_exact_point_uses_correct_coordinate_order(self):
        tenant = Tenant(name='Мойка', address='Ульяновск, Мира, 13А', latitude=Decimal('54.322900'), longitude=Decimal('48.396900'))
        links = location_context(tenant)['booking_location']
        self.assertEqual(parse_qs(urlsplit(links['embed_url']).query)['pt'], ['48.396900,54.322900,pm2rdm'])
        self.assertEqual(parse_qs(urlsplit(links['navigator_url']).query), {'lat_to':['54.322900'], 'lon_to':['48.396900']})
        self.assertEqual(parse_qs(urlsplit(links['google_url']).query)['destination'], ['54.322900,48.396900'])

    def test_old_companies_without_address_keep_booking_available(self):
        self.assertEqual(location_context(Tenant(name='Мойка')), {})

    def test_coordinate_pair_and_bounds_are_validated(self):
        for values in ({'latitude':'54'}, {'latitude':'91', 'longitude':'48'}, {'latitude':'54', 'longitude':'181'}):
            form = CompanySettingsForm({'name':'Мойка','address':'Мира, 13А', **values})
            self.assertFalse(form.is_valid())
        form = CompanySettingsForm({'name':'Мойка','address':'Мира, 13А','latitude':'54.322900','longitude':'48.396900'})
        self.assertTrue(form.is_valid(), form.errors)
