from urllib.parse import urlencode


def location_context(tenant):
    """Provider links use the address unless an exact entrance is configured."""
    if not tenant.address:
        return {}
    point = tenant.latitude is not None and tenant.longitude is not None
    lat, lon = str(tenant.latitude), str(tenant.longitude)
    destination = f'{lat},{lon}' if point else tenant.address
    widget = {'ll': f'{lon},{lat}', 'pt': f'{lon},{lat},pm2rdm', 'z': '17'} if point else {'mode': 'search', 'text': tenant.address, 'z': '17'}
    route_query = urlencode({'rtext': '~' + destination, 'rtt': 'auto'})
    apple_query = urlencode({'daddr': destination, 'dirflg': 'd'})
    google_url = 'https://www.google.com/maps/dir/?' + urlencode({'api': '1', 'destination': destination, 'travelmode': 'driving'})
    from urllib.parse import quote
    return {'booking_location': {
        'map_url': 'https://yandex.ru/maps/?' + urlencode({'text': tenant.address}),
        'embed_url': 'https://yandex.ru/map-widget/v1/?' + urlencode(widget),
        'yandex_url': 'https://yandex.ru/maps/?' + urlencode({'rtext': '~' + destination, 'rtt': 'auto'}),
        'yandex_app_url': 'yandexmaps://maps.yandex.ru/?' + route_query,
        'apple_app_url': 'maps://?' + apple_query,
        'android_url': 'intent://maps.yandex.ru/?' + route_query + '#Intent;scheme=yandexmaps;package=ru.yandex.yandexmaps;S.browser_fallback_url=' + quote(google_url, safe='') + ';end',
        'navigator_url': 'yandexnavi://build_route_on_map?' + urlencode({'lat_to': lat, 'lon_to': lon}) if point else 'yandexnavi://map_search?' + urlencode({'text': tenant.address}),
        'navigator_search': not point,
        'google_url': 'https://www.google.com/maps/dir/?' + urlencode({'api': '1', 'destination': destination, 'travelmode': 'driving'}),
        'apple_url': 'https://maps.apple.com/?' + urlencode({'daddr': destination, 'dirflg': 'd'}),
    }}
