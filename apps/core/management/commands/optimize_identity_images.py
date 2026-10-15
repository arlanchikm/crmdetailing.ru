from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand
from django import forms
from PIL import Image
from apps.core.images import LIMITS, optimize_image
from apps.tenants.models import Tenant


class Command(BaseCommand):
    help = "Optimize existing logos/avatars. Dry run by default; original files are retained."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        before = after = count = skipped = 0
        for model, field, kind in ((Tenant, 'logo', 'logo'), (get_user_model(), 'avatar', 'avatar')):
            for row in model.objects.exclude(**{field: ''}).only('pk', field).iterator(chunk_size=100):
                image = getattr(row, field)
                original = image.name
                try:
                    size = image.size
                    if size > 5 * 1024 * 1024:
                        skipped += 1
                        continue
                    with image.open('rb') as source:
                        uploaded = SimpleUploadedFile(original, source.read())
                    with Image.open(uploaded) as decoded:
                        width, height = LIMITS[kind]
                        if decoded.format == 'WEBP' and decoded.width <= width and decoded.height <= height:
                            skipped += 1
                            continue
                    optimized = optimize_image(uploaded, kind=kind)
                    if optimized is uploaded or optimized.size >= size:
                        skipped += 1
                        continue
                    if options['apply']:
                        image.save(optimized.name, optimized, save=False)
                        # Never overwrite a concurrent user upload; keep the original file for rollback.
                        if not model.objects.filter(pk=row.pk, **{field: original}).update(**{field: image.name}):
                            image.storage.delete(image.name)
                            skipped += 1
                            continue
                    before += size
                    after += optimized.size
                    count += 1
                except (OSError, forms.ValidationError):
                    skipped += 1
        self.stdout.write(f"mode={'apply' if options['apply'] else 'dry-run'} optimized={count} skipped={skipped} before_bytes={before} after_bytes={after} saved_bytes={before-after}")
