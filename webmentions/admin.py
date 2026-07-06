from django.contrib import admin
from .models import SentWebmention, Webmention


@admin.register(Webmention)
class WebmentionAdmin(admin.ModelAdmin):
    list_display = ("source_url", "target_url", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("source_url", "target_url", "author_name", "content")
    actions = ("approve_webmentions", "reject_webmentions")

    @admin.action(description="Approve selected webmentions")
    def approve_webmentions(self, request, queryset):
        updated = queryset.update(status=Webmention.APPROVED)
        if updated:
            self.message_user(request, f"Approved {updated} webmention(s).")

    @admin.action(description="Reject selected webmentions")
    def reject_webmentions(self, request, queryset):
        updated = queryset.update(status=Webmention.REJECTED)
        if updated:
            self.message_user(request, f"Rejected {updated} webmention(s).")


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
