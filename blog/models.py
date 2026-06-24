from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from markdown import markdown
from pathlib import Path


RESERVED_POST_SLUGS = {
    "about",
    "now",
    "archive",
    "rss",
    "feed",
    "admin",
    "login",
    "logout",
    "subscribe",
    "search",
    "sitemap.xml",
    "robots.txt",
    "micropub",
}


class Post(models.Model):
    DRAFT = "draft"
    PUBLISHED = "published"

    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (PUBLISHED, "Published"),
    ]

    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    body = models.TextField()
    description = models.CharField(max_length=300, blank=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=DRAFT,
    )

    published_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-published_at"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if self.status == self.PUBLISHED and self.published_at is None:
            self.published_at = timezone.now()
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = set(update_fields) | {"published_at"}

        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.slug and self.slug.lower() in RESERVED_POST_SLUGS:
            raise ValidationError(
                {"slug": "This slug is reserved for a site route."}
            )

    def get_absolute_url(self):
        return reverse("blog:post_detail", args=[self.slug])

    @property
    def body_html(self):
        return markdown(self.body)

    @property
    def is_published(self):
        return (
            self.status == self.PUBLISHED
            and self.published_at is not None
            and self.published_at <= timezone.now()
        )


class Subscriber(models.Model):
    email = models.EmailField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    source_path = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)


class PostMedia(models.Model):
    IMAGE_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".webp"}

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    alt_text = models.CharField(max_length=200, blank=True)
    file = models.FileField(upload_to="blog/media/%Y/%m/")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "post media"

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._generate_unique_slug()
        super().save(*args, **kwargs)

    def _generate_unique_slug(self):
        base = slugify(self.title) or slugify(Path(self.file.name).stem) or "media"
        base = base[:220]
        candidate = base
        suffix = 2
        queryset = type(self).objects.all()
        if self.pk:
            queryset = queryset.exclude(pk=self.pk)

        while queryset.filter(slug=candidate).exists():
            suffix_text = f"-{suffix}"
            candidate = f"{base[:220 - len(suffix_text)]}{suffix_text}"
            suffix += 1

        return candidate

    def get_absolute_url(self):
        return reverse("blog:media_detail", args=[self.slug])

    @property
    def is_image(self):
        return Path(self.file.name).suffix.lower() in self.IMAGE_EXTENSIONS

    @property
    def markdown_snippet(self):
        label = self.alt_text or self.title
        if self.is_image:
            return f"![{label}]({self.get_absolute_url()})"
        return f"[{label}]({self.get_absolute_url()})"
