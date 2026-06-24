from django.db import models
from django.utils import timezone
from markdown import markdown


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
