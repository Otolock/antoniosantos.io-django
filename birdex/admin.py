from django.contrib import admin

from .models import Bird, Sighting, SightingPhoto


@admin.register(Bird)
class BirdAdmin(admin.ModelAdmin):
    list_display = ("common_name", "scientific_name", "endemic")
    search_fields = ("common_name", "scientific_name")
    prepopulated_fields = {"slug": ("common_name",)}


@admin.register(Sighting)
class SightingAdmin(admin.ModelAdmin):
    list_display = (
        "bird",
        "date",
        "location",
        "confidence",
        "verification",
        "published_at",
    )
    list_filter = ("confidence", "verification", "published_at")
    search_fields = ("bird__common_name", "bird__scientific_name", "location")
    autocomplete_fields = ("bird",)


@admin.register(SightingPhoto)
class SightingPhotoAdmin(admin.ModelAdmin):
    list_display = ("sighting", "caption", "is_featured")
    list_filter = ("is_featured",)
    search_fields = ("sighting__bird__common_name", "caption")
    autocomplete_fields = ("sighting",)
