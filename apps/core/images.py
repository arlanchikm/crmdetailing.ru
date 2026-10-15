"""Bounded upload-time optimization for frequently displayed identity images."""
from io import BytesIO
from pathlib import Path
import warnings

from django import forms
from django.core.files.uploadedfile import SimpleUploadedFile, UploadedFile
from PIL import Image, ImageOps, UnidentifiedImageError

LIMITS = {"knowledge": (1600, 1600), "logo": (1200, 400), "avatar": (256, 256), "damage": (2560, 2560), "vehicle": (1200, 600), "background": (2560, 2560)}
MAX_PIXELS = 40_000_000


def optimize_image(file, *, kind):
    # ModelForm also cleans the saved FieldFile when no replacement was uploaded.
    if not file or not isinstance(file, UploadedFile):
        return file
    try:
        file.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(file) as source:
                if source.width * source.height > MAX_PIXELS:
                    raise forms.ValidationError("Изображение слишком большое: максимум 40 мегапикселей.")
                if source.format not in {"JPEG", "PNG", "WEBP"}:
                    raise forms.ValidationError("Загрузите изображение JPG, PNG или WebP.")
                # Preserve animations rather than silently flattening them.
                if getattr(source, "is_animated", False):
                    source.verify()
                    file.seek(0)
                    return file
                image = ImageOps.exif_transpose(source)
                image.thumbnail(LIMITS[kind], Image.Resampling.LANCZOS)
                has_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
                image = image.convert("RGBA" if has_alpha else "RGB")
                output = BytesIO()
                image.save(output, format="WEBP", quality=90 if kind == "damage" else 88, method=4)
        return SimpleUploadedFile(Path(file.name).stem + ".webp", output.getvalue(), content_type="image/webp")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise forms.ValidationError("Не удалось прочитать изображение. Загрузите корректный JPG, PNG или WebP.") from exc
    finally:
        if not file.closed:
            file.seek(0)
