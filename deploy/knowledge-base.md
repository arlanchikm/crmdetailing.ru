# Knowledge base

Public host: `support.crmdetailing.ru`. The host has its own URL configuration and never resolves to a tenant. DNS points to the existing application server; Caddy's on-demand TLS allow endpoint recognises the support host.

After applying migrations, run `python manage.py seed_knowledge_base`. The command creates only missing categories and articles. It does not overwrite titles, text, publication state or screenshots edited on the platform.

The platform's **База знаний** screen lets an authorised platform administrator create and edit articles, upload optimised JPG/PNG/WebP screenshots, and publish or unpublish articles. Public routes include published articles only. Uploaded screenshots are delivered through the published article endpoint, never through an unrestricted media directory. Seeded screenshots are public static WebP assets.

Initial content: 28 original Russian articles in eight categories. Ten screenshots were captured in the authenticated interface of `lk-test.crmdetailing.ru` on 7 October 2026. Client contacts and transaction details were excluded. Asset names include a date because production static assets have immutable caching.

Validation: `python manage.py test apps.core.test_knowledge apps.core.test_legal_documents apps.core.test_booking_location apps.core.test_platform_management --noinput`; `python manage.py makemigrations --check --dry-run`. Browser checks cover desktop (1440 px), mobile (390 px), search, article rendering, loaded images and horizontal overflow.
