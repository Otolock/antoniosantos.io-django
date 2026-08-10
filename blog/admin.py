import re
from pathlib import Path

from django.contrib import admin, messages
from django.http import HttpResponseNotAllowed, JsonResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils import timezone

from webmentions.services import send_webmentions_for_post_async

from .admin_forms import PostAdminForm
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
    form = PostAdminForm
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
    list_display = ("thumbnail", "title", "alt_text_status", "file_name", "created_at")
    list_display_links = ("thumbnail", "title")
    search_fields = ("title", "slug", "alt_text", "file")
    search_help_text = "Search titles, alt text, slugs, and filenames"
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("image_preview", "created_at", "markdown_snippet")
    fields = (
        "title",
        "slug",
        "alt_text",
        "file",
        "image_preview",
        "markdown_snippet",
        "created_at",
    )
    date_hierarchy = "created_at"
    save_on_top = True

    class Media:
        css = {"all": ("blog/admin/post_composer.css",)}

    def get_urls(self):
        custom_urls = [
            path(
                "composer-upload/",
                self.admin_site.admin_view(self.composer_upload),
                name="blog_postmedia_composer_upload",
            ),
            path(
                "<int:object_id>/composer-metadata/",
                self.admin_site.admin_view(self.composer_metadata),
                name="blog_postmedia_composer_metadata",
            ),
        ]
        return custom_urls + super().get_urls()

    @admin.display(description="Preview")
    def thumbnail(self, obj):
        if not obj.is_image:
            return "File"
        return format_html(
            '<img class="media-admin-preview" src="{}" alt="">',
            obj.file.url,
        )

    @admin.display(description="Preview")
    def image_preview(self, obj):
        if not obj or not obj.file or not obj.is_image:
            return "Preview appears here after an image is uploaded."
        return format_html(
            '<img class="media-admin-preview media-admin-preview--large" src="{}" alt="">',
            obj.file.url,
        )

    @admin.display(description="Alt text", ordering="alt_text")
    def alt_text_status(self, obj):
        if obj.alt_text:
            return obj.alt_text
        return format_html(
            '<strong style="color: var(--error-fg)">{}</strong>',
            "Needs alt text",
        )

    @admin.display(description="File", ordering="file")
    def file_name(self, obj):
        return Path(obj.file.name).name

    def _serialize_media(self, media):
        return {
            "id": media.pk,
            "title": media.title,
            "alt_text": media.alt_text,
            "filename": Path(media.file.name).name,
            "file_url": media.file.url,
            "is_image": media.is_image,
            "markdown": media.markdown_snippet,
            "update_url": reverse(
                "admin:blog_postmedia_composer_metadata", args=[media.pk]
            ),
        }

    def composer_upload(self, request):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if not self.has_add_permission(request):
            return JsonResponse({"error": "You cannot upload media."}, status=403)

        uploads = request.FILES.getlist("files")
        if not uploads:
            return JsonResponse({"error": "Choose at least one image."}, status=400)
        if len(uploads) > 20:
            return JsonResponse({"error": "Upload no more than 20 images at once."}, status=400)

        created = []
        errors = []
        for upload in uploads:
            extension = Path(upload.name).suffix.lower()
            if (
                not (upload.content_type or "").startswith("image/")
                or extension not in PostMedia.IMAGE_EXTENSIONS
            ):
                errors.append(
                    f"{upload.name} is not a supported image. "
                    "Use AVIF, GIF, JPEG, PNG, or WebP."
                )
                continue
            if upload.size > 40 * 1024 * 1024:
                errors.append(f"{upload.name} is larger than 40 MB.")
                continue

            stem = Path(upload.name).stem
            title = re.sub(r"[_-]+", " ", stem).strip() or "Untitled photo"
            media = PostMedia.objects.create(title=title[:200], file=upload)
            created.append(self._serialize_media(media))

        if not created:
            return JsonResponse(
                {"error": " ".join(errors) or "No images could be uploaded."},
                status=400,
            )
        return JsonResponse({"media": created, "errors": errors}, status=201)

    def composer_metadata(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        media = self.get_object(request, object_id)
        if media is None:
            return JsonResponse({"error": "Media not found."}, status=404)
        if not self.has_change_permission(request, media):
            return JsonResponse({"error": "You cannot edit this media."}, status=403)

        title = request.POST.get("title", "").strip()
        alt_text = request.POST.get("alt_text", "").strip()
        if not title:
            return JsonResponse({"error": "Title is required."}, status=400)
        if len(title) > 200 or len(alt_text) > 200:
            return JsonResponse(
                {"error": "Title and alt text must be 200 characters or fewer."},
                status=400,
            )

        media.title = title
        media.alt_text = alt_text
        media.save(update_fields=("title", "alt_text"))
        return JsonResponse({"media": self._serialize_media(media)})


admin.site.site_header = "Antonio's Studio"
admin.site.site_title = "Antonio's Studio"
admin.site.index_title = "Site administration"
