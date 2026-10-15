from django.urls import path
from apps.core import knowledge, views

urlpatterns = [
    path('', knowledge.index, name='support-index'),
    path('search/', knowledge.search, name='support-search'),
    path('section/<slug:slug>/', knowledge.category, name='support-category'),
    path('articles/<slug:slug>/', knowledge.article, name='support-article'),
    path('images/<int:article_id>/', knowledge.article_image, name='support-article-image'),
    path('sitemap.xml', knowledge.sitemap, name='support-sitemap'),
    path('robots.txt', knowledge.robots, name='support-robots'),
    path('health/', views.health, name='health'),
]
