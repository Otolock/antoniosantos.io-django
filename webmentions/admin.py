from django.contrib import admin
from .models import SentWebmention, Webmention


@admin.register(Webmention)
class WebmentionAdmin(admin.ModelAdmin):
    list_display = ("source_url", "target_url", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("source_url", "target_url", "author_name", "content")


@admin.register(SentWebmention)
class SentWebmentionAdmin(admin.ModelAdmin):
    list_display = (
        "source_url",
        "target_url",
        "endpoint_url",
        "status",
        "response_code",
        "attempts",
        "last_sent_at",
    )
    list_filter = ("status", "last_sent_at", "created_at")
    search_fields = ("source_url", "target_url", "endpoint_url", "error")
    readonly_fields = ("created_at", "last_sent_at")
