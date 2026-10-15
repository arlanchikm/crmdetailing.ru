from django.core.management.base import BaseCommand
from django.db import transaction
from apps.core.models import KnowledgeCategory, KnowledgeArticle
from apps.core.knowledge_seed import CATEGORIES, ARTICLES, article_body


class Command(BaseCommand):
    help = 'Add missing knowledge-base articles without replacing editor changes.'

    @transaction.atomic
    def handle(self, *args, **options):
        categories = {}
        for order, (slug, name, description) in enumerate(CATEGORIES):
            categories[slug], _ = KnowledgeCategory.objects.get_or_create(
                slug=slug, defaults={'name': name, 'description': description, 'sort_order': order})
        added = 0
        featured = {'first-steps', 'create-appointment', 'service-quantity', 'inventory-balance', 'online-booking', 'analytics'}
        for order, (category, slug, title, summary, screenshot, preparation, steps, explanation) in enumerate(ARTICLES):
            _, created = KnowledgeArticle.objects.get_or_create(slug=slug, defaults={
                'category': categories[category], 'title': title, 'summary': summary,
                'content': article_body(preparation, steps, explanation), 'published': True,
                'featured': slug in featured, 'sort_order': order,
                'default_screenshot': f'img/knowledge/{screenshot}-20261007.webp' if screenshot else '',
                'screenshot_caption': title + ' — интерфейс тестовой компании' if screenshot else '',
            })
            added += created
        self.stdout.write(self.style.SUCCESS(f'Added {added} articles; existing articles preserved.'))
