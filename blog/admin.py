from django.contrib import admin, messages
from django.utils import timezone

from webmentions.services import send_webmentions_for_post_async

from .llm import DescriptionGenerationError, generate_post_description
from .models import Note, Post, PostMedia, Subscriber, Tag


class PublishableContentAdmin(admin.ModelAdmin):
    """The shared, deliberate controls for content with a publish state."""

    list_filter = ("status", "tags")
    search_fields = ("body", "tags__name")
    autocomplete_fields = ("tags",)

    @admin.action(description="Publish selected content")
    def publish(self, request, queryset):
        for item in queryset:
            item.status = item.PUBLISHED
            if item.published_at is None:
                item.published_at = timezone.now()
            item.save(update_fields=["status", "published_at"])
            if isinstance(item, Post):
                send_webmentions_for_post_async(item)

        self.message_user(request, "Selected content was published.", messages.SUCCESS)

    @admin.action(description="Unpublish selected content")
    def unpublish(self, request, queryset):
        updated = queryset.update(status=self.model.DRAFT, published_at=None)
        self.message_user(
            request,
            f"Unpublished {updated} item{'s' if updated != 1 else ''}.",
            messages.SUCCESS,
        )

@admin.register(Subscriber)
class SubscriberAdmin(admin.ModelAdmin):
    list_display = ("email", "created_at", "source_path")
    search_fields = ("email", "source_path")
    readonly_fields = ("created_at",)


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Post)
class PostAdmin(PublishableContentAdmin):
    list_display = ("title", "status", "published_at", "upvotes_count")
    search_fields = ("title", "body", "tags__name")
    prepopulated_fields = {"slug": ("title",)}
    actions = ("publish", "unpublish", "generate_descriptions")
    fieldsets = (
        (None, {"fields": ("title", "body")}),
        ("Publishing", {"fields": ("status", "published_at")}),
        (
            "Details",
            {
                "fields": (
                    "slug",
                    "description",
                    "tags",
                    "reply_to_url",
                    "reply_to_title",
                    "upvotes_count",
                )
            },
        ),
    )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.is_published:
            send_webmentions_for_post_async(obj)

    @admin.action(description="Generate descriptions for selected posts")
    def generate_descriptions(self, request, queryset):
        generated = 0
        for post in queryset:
            try:
                post.description = generate_post_description(post)
            except DescriptionGenerationError as error:
                self.message_user(request, f"{post.title}: {error}", messages.ERROR)
                continue

            post.save(update_fields=("description",))
            generated += 1

        if generated:
            self.message_user(
                request,
                f"Generated descriptions for {generated} post{'s' if generated != 1 else ''}.",
                messages.SUCCESS,
            )


@admin.register(Note)
class NoteAdmin(PublishableContentAdmin):
    list_display = ("display_title", "status", "published_at")
    actions = ("publish", "unpublish")
    fieldsets = (
        (None, {"fields": ("body",)}),
        ("Publishing", {"fields": ("status", "published_at")}),
        ("Details", {"fields": ("tags",)}),
    )


@admin.register(PostMedia)
class PostMediaAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "file", "created_at")
    search_fields = ("title", "slug", "alt_text", "file")
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("created_at", "markdown_snippet")
    fields = ("title", "slug", "alt_text", "file", "markdown_snippet", "created_at")


admin.site.site_header = "Antonio's Studio"
admin.site.site_title = "Antonio's Studio"
admin.site.index_title = "Site administration"
