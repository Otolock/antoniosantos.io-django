from pathlib import Path

from django.db import migrations, models
from django.utils.text import slugify


def populate_media_slugs(apps, schema_editor):
    PostMedia = apps.get_model("blog", "PostMedia")
    used_slugs = set()

    for media in PostMedia.objects.order_by("pk"):
        base = slugify(media.title) or slugify(Path(media.file.name).stem) or "media"
        base = base[:220]
        candidate = base
        suffix = 2

        while candidate in used_slugs:
            suffix_text = f"-{suffix}"
            candidate = f"{base[:220 - len(suffix_text)]}{suffix_text}"
            suffix += 1

        media.slug = candidate
        media.save(update_fields=["slug"])
        used_slugs.add(candidate)


class Migration(migrations.Migration):

    dependencies = [
        ("blog", "0003_postmedia"),
    ]

    operations = [
        migrations.AddField(
            model_name="postmedia",
            name="slug",
            field=models.SlugField(blank=True, max_length=220),
        ),
        migrations.RunPython(populate_media_slugs, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="postmedia",
            name="slug",
            field=models.SlugField(blank=True, max_length=220, unique=True),
        ),
    ]
