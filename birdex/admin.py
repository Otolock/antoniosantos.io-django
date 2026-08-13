from django.contrib import admin, messages
from django.utils import timezone

from .models import Bird, Sighting, SightingPhoto


@admin.register(Bird)
class BirdAdmin(admin.ModelAdmin):
    list_display = ("common_name", "scientific_name", "endemic")
    search_fields = ("common_name", "scientific_name")
    prepopulated_fields = {"slug": ("common_name",)}


class SightingPhotoInline(admin.TabularInline):
    model = SightingPhoto
    fields = ("image", "caption", "is_featured")
    extra = 1
    verbose_name = "Photo"
    verbose_name_plural = "Photos — upload these before publishing"


@admin.register(Sighting)
class SightingAdmin(admin.ModelAdmin):
    inlines = (SightingPhotoInline,)
    list_display = (
        "bird",
        "date",
        "location",
        "status",
        "confidence",
        "verification",
        "published_at",
    )
    list_filter = ("status", "confidence", "verification", "published_at")
    search_fields = ("bird__common_name", "bird__scientific_name", "location")
    autocomplete_fields = ("bird",)
    actions = ("publish", "unpublish")
    save_on_top = True
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "bird",
                    "date",
                    "location",
                    "notes",
                    "confidence",
                    "verification",
                )
            },
        ),
        (
            "Publishing",
            {
                "fields": ("status", "published_at"),
                "description": (
                    "Keep this sighting as a draft while adding photos below. "
                    "Publish it only when the entry is ready for the website and RSS."
                ),
            },
        ),
    )

    @admin.action(description="Publish selected sightings")
    def publish(self, request, queryset):
        published = 0
        for sighting in queryset:
            sighting.status = Sighting.Status.PUBLISHED
            if sighting.published_at is None:
                sighting.published_at = timezone.now()
            sighting.save(update_fields=("status", "published_at"))
            published += 1

        self.message_user(
            request,
            f"Published {published} sighting{'s' if published != 1 else ''}.",
            messages.SUCCESS,
        )

    @admin.action(description="Unpublish selected sightings")
    def unpublish(self, request, queryset):
        updated = queryset.update(
            status=Sighting.Status.DRAFT,
            published_at=None,
        )
        self.message_user(
            request,
            f"Unpublished {updated} sighting{'s' if updated != 1 else ''}.",
            messages.SUCCESS,
        )


@admin.register(SightingPhoto)
class SightingPhotoAdmin(admin.ModelAdmin):
    list_display = ("sighting", "caption", "is_featured")
    list_filter = ("is_featured",)
    search_fields = ("sighting__bird__common_name", "caption")
    autocomplete_fields = ("sighting",)
