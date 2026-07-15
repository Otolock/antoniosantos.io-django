from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from markdown import markdown
from pathlib import Path

from .html import sanitize_html


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


class Tag(models.Model):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Post(models.Model):
    DRAFT = "draft"
    PUBLISHED = "published"
    DELETED = "deleted"

    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (PUBLISHED, "Published"),
        (DELETED, "Deleted"),
    ]

    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    body = models.TextField()
    description = models.CharField(max_length=300, blank=True)
    reply_to_url = models.URLField("in reply to URL", max_length=500, blank=True)
    reply_to_title = models.CharField("in reply to title", max_length=200, blank=True)
    upvotes_count = models.PositiveIntegerField(default=0)
    tags = models.ManyToManyField(Tag, related_name="posts", blank=True)

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
        return sanitize_html(markdown(self.body))

    @property
    def is_published(self):
        return (
            self.status == self.PUBLISHED
            and self.published_at is not None
            and self.published_at <= timezone.now()
        )


class Note(models.Model):
    DRAFT = "draft"
    PUBLISHED = "published"
    DELETED = "deleted"

    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (PUBLISHED, "Published"),
        (DELETED, "Deleted"),
    ]

    slug = models.SlugField(unique=True, blank=True)
    body = models.TextField()
    tags = models.ManyToManyField(Tag, related_name="notes", blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=DRAFT)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-published_at"]

    def __str__(self):
        return self.display_title

    def save(self, *args, **kwargs):
        if self.status == self.PUBLISHED and self.published_at is None:
            self.published_at = timezone.now()

        if not self.slug or self.slug.startswith("note-draft-"):
            if self.status == self.PUBLISHED and self.published_at:
                self.slug = self._generate_slug(self.published_at)
            elif not self.slug:
                self.slug = f"note-draft-{timezone.now():%Y%m%d%H%M%S%f}"

        super().save(*args, **kwargs)

    def _generate_slug(self, published_at):
        base = timezone.localtime(published_at).strftime("note-%Y-%m-%d-%H%M")
        candidate = base
        suffix = 2
        while type(self).objects.exclude(pk=self.pk).filter(slug=candidate).exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def clean(self):
        super().clean()
        if self.slug and self.slug.lower() in RESERVED_POST_SLUGS:
            raise ValidationError({"slug": "This slug is reserved for a site route."})

    def get_absolute_url(self):
        return reverse("blog:note_detail", args=[self.slug])

    @property
    def display_title(self):
        if self.published_at:
            return timezone.localtime(self.published_at).strftime("%B %-d, %Y at %-I:%M %p")
        return "Untitled note"

    @property
    def body_html(self):
        return sanitize_html(markdown(self.body))

    @property
    def is_published(self):
        return (
            self.status == self.PUBLISHED
            and self.published_at is not None
            and self.published_at <= timezone.now()
        )

    @property
    def is_note(self):
        return True


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


class Comment(models.Model):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (APPROVED, "Approved"),
        (REJECTED, "Rejected"),
    ]

    post = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    author_name = models.CharField(max_length=80, blank=True)
    author_email = models.EmailField(blank=True)
    body = models.TextField(max_length=2000)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=PENDING,
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["post", "status", "created_at"]),
        ]

    def __str__(self):
        return f"{self.author_name} on {self.post}"

    def save(self, *args, **kwargs):
        self.author_name = self.author_name.strip() or "Anonymous"
        self.author_email = self.author_email.strip().lower()

        if self.status == self.APPROVED and self.approved_at is None:
            self.approved_at = timezone.now()
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = set(update_fields) | {"approved_at"}
        elif self.status != self.APPROVED:
            self.approved_at = None
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = set(update_fields) | {"approved_at"}

        super().save(*args, **kwargs)

    @property
    def is_approved(self):
        return self.status == self.APPROVED


class ContentRevision(models.Model):
    POST = "post"
    NOTE = "note"
    CONTENT_TYPE_CHOICES = [
        (POST, "Post"),
        (NOTE, "Note"),
    ]

    content_type = models.CharField(max_length=20, choices=CONTENT_TYPE_CHOICES)
    object_id = models.PositiveBigIntegerField()
    object_label = models.CharField(max_length=250)
    snapshot = models.JSONField()
    reason = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="content_revisions",
    )

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(fields=["content_type", "object_id", "created_at"]),
        ]

    def __str__(self):
        return f"{self.object_label} at {self.created_at:%Y-%m-%d %H:%M}"


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
            return f"![{label}]({self.file.url})"
        return f"[{label}]({self.file.url})"
