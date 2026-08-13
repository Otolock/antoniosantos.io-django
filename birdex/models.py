from pathlib import Path
from uuid import uuid4

from django.core.files.storage import storages
from django.db import models
from django.db.models import Prefetch
from django.urls import reverse
from django.utils import timezone


def birdex_storage():
    return storages["birdex"]


def birdex_photo_upload_to(instance, filename):
    """Give each uploaded photo an opaque, collision-resistant object key."""
    extension = Path(filename).suffix.lower()
    return f"birdex/photos/{uuid4().hex}{extension}"


class SightingQuerySet(models.QuerySet):
    def published(self):
        return self.filter(
            status=Sighting.Status.PUBLISHED,
            published_at__isnull=False,
            published_at__lte=timezone.now(),
        )

    def with_primary_photo(self):
        return self.select_related("bird").prefetch_related(
            Prefetch(
                "photos",
                queryset=SightingPhoto.objects.order_by("-is_featured", "pk"),
                to_attr="display_photos",
            )
        )


class Bird(models.Model):
    common_name = models.CharField(max_length=200)
    scientific_name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)

    description = models.TextField(
        blank=True,
        help_text=(
            "Keep this personal: what makes the bird memorable, where you encounter "
            "it, behavior you have noticed, and what makes it interesting to photograph."
        ),
    )
    endemic = models.BooleanField(default=False)
    ebird_url = models.URLField(
        "eBird species profile",
        blank=True,
        help_text="Paste the complete eBird species-profile URL.",
    )

    def __str__(self):
        return self.common_name

    def get_absolute_url(self):
        return reverse("birdex:bird_detail", kwargs={"slug": self.slug})


class Sighting(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"

    class Confidence(models.TextChoices):
        TENTATIVE = "tentative", "Tentative"
        PROBABLE = "probable", "Probable"
        HIGH = "high", "High"
        CERTAIN = "certain", "Certain"

    class Verification(models.TextChoices):
        UNVERIFIED = "unverified", "Unverified"
        COMMUNITY = "community", "Community confirmed"
        EXPERT = "expert", "Expert confirmed"

    bird = models.ForeignKey(
        Bird,
        on_delete=models.CASCADE,
        related_name="sightings",
    )

    date = models.DateField()
    location = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)

    confidence = models.CharField(
        max_length=20,
        choices=Confidence.choices,
        default=Confidence.TENTATIVE,
    )

    verification = models.CharField(
        max_length=20,
        choices=Verification.choices,
        default=Verification.UNVERIFIED,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        help_text="Draft sightings are only visible in the admin.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Leave blank to use the time the sighting is published.",
    )

    objects = SightingQuerySet.as_manager()

    class Meta:
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return f"{self.bird.common_name} — {self.date}"

    def save(self, *args, **kwargs):
        if self.status == self.Status.PUBLISHED and self.published_at is None:
            self.published_at = timezone.now()
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = set(update_fields) | {"published_at"}

        super().save(*args, **kwargs)

    @property
    def is_published(self):
        return (
            self.status == self.Status.PUBLISHED
            and self.published_at is not None
            and self.published_at <= timezone.now()
        )

    def get_absolute_url(self):
        return reverse(
            "birdex:sighting_detail",
            kwargs={"bird_slug": self.bird.slug, "pk": self.pk},
        )

    @property
    def primary_photo(self):
        if hasattr(self, "display_photos"):
            return self.display_photos[0] if self.display_photos else None
        return self.photos.order_by("-is_featured", "pk").first()

    @property
    def photo_count(self):
        if hasattr(self, "display_photos"):
            return len(self.display_photos)
        return self.photos.count()


class SightingPhoto(models.Model):
    sighting = models.ForeignKey(
        Sighting,
        on_delete=models.CASCADE,
        related_name="photos",
    )

    image = models.ImageField(
        upload_to=birdex_photo_upload_to,
        storage=birdex_storage,
    )

    caption = models.CharField(
        max_length=300,
        blank=True,
    )

    is_featured = models.BooleanField(
        default=False,
    )

    def __str__(self):
        return f"{self.sighting.bird.common_name} photo"
