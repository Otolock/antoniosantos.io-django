from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone
from markdown import markdown


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
