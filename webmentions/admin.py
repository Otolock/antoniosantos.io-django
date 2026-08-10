from django.contrib import admin, messages

from .models import SentWebmention, Webmention


@admin.register(Webmention)
class WebmentionAdmin(admin.ModelAdmin):
    list_display = ("source_url", "target_url", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("source_url", "target_url", "author_name", "title", "content")
    readonly_fields = ("created_at",)
    actions = ("approve", "reject", "mark_as_spam", "return_to_inbox")

    def get_readonly_fields(self, request, obj=None):
        if obj is not None:
            return (*self.readonly_fields, "source_url", "target_url")
        return self.readonly_fields

    def set_status(self, request, queryset, status, label):
        updated = queryset.update(status=status)
        self.message_user(
            request,
            f"{label} {updated} webmention{'s' if updated != 1 else ''}.",
            messages.SUCCESS,
        )

    @admin.action(description="Approve selected webmentions")
    def approve(self, request, queryset):
        self.set_status(request, queryset, Webmention.APPROVED, "Approved")

    @admin.action(description="Reject selected webmentions")
    def reject(self, request, queryset):
        self.set_status(request, queryset, Webmention.REJECTED, "Rejected")

    @admin.action(description="Mark selected webmentions as spam")
    def mark_as_spam(self, request, queryset):
        self.set_status(request, queryset, Webmention.SPAM, "Marked as spam")

    @admin.action(description="Return selected webmentions to inbox")
    def return_to_inbox(self, request, queryset):
        self.set_status(request, queryset, Webmention.PENDING, "Returned")


@admin.register(SentWebmention)
class SentWebmentionAdmin(admin.ModelAdmin):
    list_display = (
        "source_url",
        "target_url",
        "status",
        "response_code",
        "attempts",
        "last_sent_at",
    )
    list_filter = ("status",)
    search_fields = ("source_url", "target_url", "endpoint_url", "error")
    readonly_fields = (
        "source_url",
        "target_url",
        "endpoint_url",
        "status",
        "response_code",
        "error",
        "attempts",
        "created_at",
        "last_sent_at",
    )
    fields = readonly_fields

    def has_add_permission(self, request):
        return False
