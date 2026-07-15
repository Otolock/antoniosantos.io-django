from urllib.parse import urlparse

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db.models import Case, IntegerField, Value, When
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import Truncator

from .models import SentWebmention, Webmention


def _host_label(url):
    parsed = urlparse(url)
    return parsed.netloc.removeprefix("www.") or url


def _path_label(url):
    parsed = urlparse(url)
    return parsed.path.rstrip("/") or "/"


@admin.register(Webmention)
class WebmentionAdmin(admin.ModelAdmin):
    change_list_template = "admin/webmentions/webmention/change_list.html"
    list_display = (
        "mention_summary",
        "destination_summary",
        "status_badge",
        "received_at",
        "quick_actions",
    )
    list_display_links = None
    list_filter = ("status",)
    search_fields = ("source_url", "target_url", "author_name", "title", "content")
    actions = (
        "approve_webmentions",
        "reject_webmentions",
        "mark_as_spam",
        "return_to_inbox",
    )
    readonly_fields = ("source_url", "target_url", "created_at")
    fieldsets = (
        (
            "Mention",
            {
                "fields": ("author_name", "title", "content"),
                "description": "The author and content extracted from the source page.",
            },
        ),
        (
            "Moderation",
            {
                "fields": ("status",),
                "description": "Approved mentions are visible on the destination post.",
            },
        ),
        (
            "Delivery details",
            {
                "fields": ("source_url", "target_url", "created_at"),
                "classes": ("collapse",),
            },
        ),
    )
    list_per_page = 30

    def has_add_permission(self, request):
        return False

    def get_ordering(self, request):
        return (
            Case(
                When(status=Webmention.PENDING, then=Value(0)),
                When(status=Webmention.APPROVED, then=Value(1)),
                When(status=Webmention.REJECTED, then=Value(2)),
                When(status=Webmention.SPAM, then=Value(3)),
                default=Value(4),
                output_field=IntegerField(),
            ),
            "-created_at",
            "-pk",
        )

    def changelist_view(self, request, extra_context=None):
        counts = {
            status: Webmention.objects.filter(status=status).count()
            for status, _label in Webmention.STATUS_CHOICES
        }
        extra_context = {
            **(extra_context or {}),
            "total_webmentions": sum(counts.values()),
            "pending_count": counts[Webmention.PENDING],
            "approved_count": counts[Webmention.APPROVED],
            "rejected_count": counts[Webmention.REJECTED],
            "spam_count": counts[Webmention.SPAM],
        }
        return super().changelist_view(request, extra_context=extra_context)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:object_id>/moderate/<str:action>/",
                self.admin_site.admin_view(self.moderate_view),
                name="webmentions_webmention_moderate",
            )
        ]
        return custom_urls + urls

    def moderate_view(self, request, object_id, action):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if not self.has_change_permission(request):
            raise PermissionDenied

        statuses = {
            "approve": (Webmention.APPROVED, "Approved"),
            "reject": (Webmention.REJECTED, "Rejected"),
            "spam": (Webmention.SPAM, "Marked as spam"),
            "pending": (Webmention.PENDING, "Returned to the inbox"),
        }
        if action not in statuses:
            raise PermissionDenied

        mention = self.get_object(request, object_id)
        if mention is None:
            raise PermissionDenied
        mention.status, label = statuses[action]
        mention.save(update_fields=["status"])
        self.message_user(
            request,
            f"{label}: {mention.author_name or _host_label(mention.source_url)}.",
        )

        fallback = reverse("admin:webmentions_webmention_changelist")
        return_to = request.headers.get("Referer", "")
        if not url_has_allowed_host_and_scheme(
            return_to,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return_to = fallback
        return HttpResponseRedirect(return_to)

    @admin.display(description="Mention", ordering="author_name")
    def mention_summary(self, obj):
        author = obj.author_name or _host_label(obj.source_url)
        headline = obj.title or "Untitled mention"
        excerpt = (
            Truncator(" ".join(obj.content.split())).chars(165)
            if obj.content
            else "No text was provided."
        )
        return format_html(
            '<div class="webmention-summary">'
            '<strong>{}</strong><span class="webmention-author">{}</span>'
            '<p>{}</p><a href="{}" target="_blank" rel="noopener">Open source ↗</a>'
            "</div>",
            headline,
            author,
            excerpt,
            obj.source_url,
        )

    @admin.display(description="Destination", ordering="target_url")
    def destination_summary(self, obj):
        return format_html(
            '<a class="webmention-destination" href="{}" target="_blank" rel="noopener">'
            '<strong>{}</strong><span>{}</span></a>',
            obj.target_url,
            _path_label(obj.target_url),
            _host_label(obj.target_url),
        )

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return format_html(
            '<span class="studio-status studio-status--{}">{}</span>',
            obj.status,
            obj.get_status_display(),
        )

    @admin.display(description="Received", ordering="created_at")
    def received_at(self, obj):
        received = timezone.localtime(obj.created_at)
        return format_html(
            '<time class="webmention-time" datetime="{}">{}<span>{}</span></time>',
            received.isoformat(),
            received.strftime("%b %-d"),
            received.strftime("%Y · %-I:%M %p"),
        )

    @admin.display(description="Actions")
    def quick_actions(self, obj):
        actions = []
        if obj.status != Webmention.APPROVED:
            actions.append(("approve", "Approve", "webmention-action--approve"))
        if obj.status != Webmention.REJECTED:
            actions.append(("reject", "Reject", ""))
        if obj.status != Webmention.SPAM:
            actions.append(("spam", "Spam", "webmention-action--danger"))
        if obj.status != Webmention.PENDING:
            actions.append(("pending", "Inbox", ""))

        buttons = format_html_join(
            "",
            '<button type="submit" formmethod="post" formaction="{}" class="{}">{}</button>',
            (
                (
                    reverse("admin:webmentions_webmention_moderate", args=[obj.pk, action]),
                    css_class,
                    label,
                )
                for action, label, css_class in actions[:3]
            ),
        )
        return format_html(
            '<span class="webmention-actions">{}<a href="{}">Details</a></span>',
            buttons,
            reverse("admin:webmentions_webmention_change", args=[obj.pk]),
        )

    def _set_status(self, request, queryset, status, label):
        updated = queryset.update(status=status)
        if updated:
            self.message_user(
                request,
                f"{label} {updated} webmention{'' if updated == 1 else 's'}.",
                messages.SUCCESS,
            )

    @admin.action(description="Approve selected webmentions")
    def approve_webmentions(self, request, queryset):
        self._set_status(request, queryset, Webmention.APPROVED, "Approved")

    @admin.action(description="Reject selected webmentions")
    def reject_webmentions(self, request, queryset):
        self._set_status(request, queryset, Webmention.REJECTED, "Rejected")

    @admin.action(description="Mark selected webmentions as spam")
    def mark_as_spam(self, request, queryset):
        self._set_status(request, queryset, Webmention.SPAM, "Marked as spam")

    @admin.action(description="Return selected webmentions to inbox")
    def return_to_inbox(self, request, queryset):
        self._set_status(request, queryset, Webmention.PENDING, "Returned")


@admin.register(SentWebmention)
class SentWebmentionAdmin(admin.ModelAdmin):
    change_list_template = "admin/webmentions/sentwebmention/change_list.html"
    list_display = (
        "delivery_summary",
        "status_badge",
        "response_summary",
        "attempts",
        "last_attempt",
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
    fieldsets = (
        ("Route", {"fields": ("source_url", "target_url", "endpoint_url")}),
        ("Delivery result", {"fields": ("status", "response_code", "error", "attempts")}),
        ("Timing", {"fields": ("created_at", "last_sent_at"), "classes": ("collapse",)}),
    )
    list_per_page = 30

    def has_add_permission(self, request):
        return False

    def changelist_view(self, request, extra_context=None):
        counts = {
            status: SentWebmention.objects.filter(status=status).count()
            for status, _label in SentWebmention.STATUS_CHOICES
        }
        extra_context = {
            **(extra_context or {}),
            "total_deliveries": sum(counts.values()),
            "sent_count": counts[SentWebmention.SENT],
            "failed_count": counts[SentWebmention.FAILED],
            "pending_count": counts[SentWebmention.PENDING],
            "no_endpoint_count": counts[SentWebmention.NO_ENDPOINT],
        }
        return super().changelist_view(request, extra_context=extra_context)

    @admin.display(description="Delivery", ordering="target_url")
    def delivery_summary(self, obj):
        return format_html(
            '<div class="webmention-delivery">'
            '<a href="{}" target="_blank" rel="noopener"><span>From</span><strong>{}</strong></a>'
            '<span class="webmention-delivery-arrow">→</span>'
            '<a href="{}" target="_blank" rel="noopener"><span>To</span><strong>{}</strong></a>'
            "</div>",
            obj.source_url,
            _path_label(obj.source_url),
            obj.target_url,
            _host_label(obj.target_url),
        )

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return format_html(
            '<span class="studio-status studio-status--{}">{}</span>',
            obj.status,
            obj.get_status_display(),
        )

    @admin.display(description="Response", ordering="response_code")
    def response_summary(self, obj):
        if obj.response_code:
            return format_html(
                '<strong class="webmention-response-code">HTTP {}</strong>',
                obj.response_code,
            )
        if obj.error:
            return format_html(
                '<span class="webmention-error" title="{}">{}</span>',
                obj.error,
                Truncator(obj.error).chars(70),
            )
        return "—"

    @admin.display(description="Last attempt", ordering="last_sent_at")
    def last_attempt(self, obj):
        if not obj.last_sent_at:
            return "Not attempted"
        attempted = timezone.localtime(obj.last_sent_at)
        return format_html(
            '<time class="webmention-time" datetime="{}">{}<span>{}</span></time>',
            attempted.isoformat(),
            attempted.strftime("%b %-d"),
            attempted.strftime("%Y · %-I:%M %p"),
        )
