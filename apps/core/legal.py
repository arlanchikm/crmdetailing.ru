LEGAL_VERSION = "30.09.2026"

LEGAL_DOCUMENTS = {
    "privacy": {
        "title": "Политика в отношении обработки персональных данных",
        "template": "legal/privacy_content.html",
    },
    "personal-data-consent": {
        "title": "Согласие на обработку персональных данных",
        "template": "legal/personal_data_consent_content.html",
    },
    "offer": {
        "title": "Публичная оферта",
        "template": "legal/offer_content.html",
    },
    "marketing-consent": {
        "title": "Согласие на получение рекламных и маркетинговых сообщений",
        "template": "legal/marketing_consent_content.html",
    },
}


def get_document(slug):
    from .models import LegalDocument
    definition = LEGAL_DOCUMENTS.get(slug)
    if definition is None:
        return None
    document = dict(definition, version=LEGAL_VERSION, content=None)
    saved = LegalDocument.objects.filter(slug=slug).first()
    if saved:
        document.update(title=saved.title, version=saved.version, content=saved.content)
    return document


def sanitize_content(content):
    """Keep document formatting and safe links; discard executable HTML."""
    from html import escape
    from html.parser import HTMLParser
    from urllib.parse import urlsplit
    class Sanitizer(HTMLParser):
        allowed = {'p', 'div', 'br', 'h2', 'h3', 'h4', 'ul', 'ol', 'li', 'strong', 'b', 'em', 'i', 'u', 'a', 'blockquote'}
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.result = []
            self.blocked = 0
        def handle_starttag(self, tag, attrs):
            if tag in {'script', 'style', 'iframe', 'object'}:
                self.blocked += 1
            if self.blocked or tag not in self.allowed:
                return
            attributes = ''
            if tag == 'a':
                href = dict(attrs).get('href', '')
                if href and urlsplit(href).scheme.lower() in {'https', 'http', 'mailto'}:
                    attributes = ' href="' + escape(href, quote=True) + '"'
            self.result.append('<' + tag + attributes + '>')
        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)
            if tag != 'br': self.handle_endtag(tag)
        def handle_endtag(self, tag):
            if tag in {'script', 'style', 'iframe', 'object'}:
                self.blocked = max(0, self.blocked - 1)
                return
            if not self.blocked and tag in self.allowed and tag != 'br':
                self.result.append('</' + tag + '>')
        def handle_data(self, text):
            if not self.blocked: self.result.append(escape(text))
    parser = Sanitizer()
    parser.feed(content)
    return ''.join(parser.result)
