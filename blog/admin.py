from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Case, F, IntegerField, Value, When
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils import timezone

from .llm import DescriptionGenerationError, generate_post_description
from .models import Note, Post, PostMedia, Subscriber, Tag
from webmentions.models import Webmention
from webmentions.services import send_webmentions_for_post_async


def pending_moderation_count():
    return Webmention.objects.filter(status=Webmention.PENDING).count()


def moderation_queue_view(request):
    if request.method == "POST":
        _moderate_queue_item(request)
        return HttpResponseRedirect(reverse("admin:moderation_queue"))

    pending_webmentions = Webmention.objects.filter(status=Webmention.PENDING)
    queue_items = [
        _webmention_queue_item(webmention)
        for webmention in pending_webmentions.order_by("created_at", "pk")
    ]
    queue_items.sort(key=lambda item: (item["created_at"], item["type"], item["id"]))

    context = {
        **admin.site.each_context(request),
        "title": "Moderation queue",
        "subtitle": "Webmentions pending approval",
        "queue_items": queue_items,
        "pending_webmention_count": pending_webmentions.count(),
    }
    return render(request, "admin/moderation_queue.html", context)


def _moderate_queue_item(request):
    item_type = request.POST.get("item_type")
    item_id = request.POST.get("item_id")
    action = request.POST.get("action")

    if action not in {"approve", "reject"}:
        raise PermissionDenied

    if item_type == "webmention":
        if not request.user.has_perm("webmentions.change_webmention"):
            raise PermissionDenied
        webmention = get_object_or_404(Webmention, pk=item_id, status=Webmention.PENDING)
        webmention.status = (
            Webmention.APPROVED if action == "approve" else Webmention.REJECTED
        )
        webmention.save(update_fields=["status"])
        action_label = "Approved" if action == "approve" else "Rejected"
        messages.success(
            request,
            f"{action_label} webmention from {webmention.source_url}.",
        )
        return

    raise PermissionDenied


def _webmention_queue_item(webmention):
    return {
        "id": webmention.pk,
        "type": "webmention",
        "label": "Webmention",
        "created_at": webmention.created_at,
        "author": webmention.author_name or webmention.source_url,
        "title": webmention.title,
        "body": webmention.content,
        "source_url": webmention.source_url,
        "target_url": webmention.target_url,
        "change_url": reverse(
            "admin:webmentions_webmention_change",
            args=[webmention.pk],
        ),
    }


_original_admin_get_urls = admin.site.get_urls


def _admin_get_urls():
    custom_urls = [
        path(
            "moderation-queue/",
            admin.site.admin_view(moderation_queue_view),
            name="moderation_queue",
        ),
    ]
    return custom_urls + _original_admin_get_urls()


_original_admin_index = admin.site.index


def _admin_index(request, extra_context=None):
    extra_context = extra_context or {}
    extra_context["pending_moderation_count"] = pending_moderation_count()
    return _original_admin_index(request, extra_context)


admin.site.get_urls = _admin_get_urls
admin.site.index = _admin_index
admin.site.index_template = "admin/moderation_index.html"


@admin.register(Subscriber)
class SubscriberAdmin(admin.ModelAdmin):
    list_display = ["email", "created_at", "source_path"]
    search_fields = ["email", "source_path"]
    readonly_fields = ["created_at"]


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ["name", "slug"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    change_form_template = "admin/blog/post/change_form.html"
    list_display = ["title", "status", "published_at", "upvotes"]
    list_filter = ["status", "tags"]
    search_fields = ["title", "body", "tags__name"]
    prepopulated_fields = {"slug": ("title",)}
    filter_horizontal = ["tags"]
    actions = ["publish_posts", "unpublish_posts", "generate_descriptions"]

    def get_ordering(self, request):
        return [
            Case(
                When(status=Post.DRAFT, then=Value(0)),
                When(status=Post.PUBLISHED, then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            ),
            F("published_at").desc(nulls_last=True),
            "-pk",
        ]

    def render_change_form(self, request, context, *args, **kwargs):
        context["available_media"] = PostMedia.objects.order_by("-created_at", "-pk")[
            :25
        ]
        original = context.get("original")
        if original:
            context["post_preview_url"] = reverse(
                "admin:blog_post_preview",
                args=[original.pk],
            )
        else:
            context["post_preview_url"] = reverse("admin:blog_post_preview")
        return super().render_change_form(request, context, *args, **kwargs)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "preview/",
                self.admin_site.admin_view(self.preview_view),
                name="blog_post_preview",
            ),
            path(
                "<path:object_id>/preview/",
                self.admin_site.admin_view(self.preview_view),
                name="blog_post_preview",
            ),
            path(
                "<path:object_id>/generate-description/",
                self.admin_site.admin_view(self.generate_description_view),
                name="blog_post_generate_description",
            ),
        ]
        return custom_urls + urls

    def save_model(self, request, obj, form, change):
        if request and "_publish_now" in request.POST:
            obj.status = Post.PUBLISHED
            obj.published_at = timezone.now()

        super().save_model(request, obj, form, change)
        if obj.is_published:
            send_webmentions_for_post_async(obj)

    def preview_view(self, request, object_id=None):
        obj = self.get_object(request, object_id) if object_id else None
        if object_id and obj is None:
            self.message_user(request, "Post not found.", messages.ERROR)
            return HttpResponseRedirect(reverse("admin:blog_post_changelist"))

        if request.method == "POST":
            if obj is None and not self.has_add_permission(request):
                raise PermissionDenied
            if obj is not None and not self.has_change_permission(request, obj):
                raise PermissionDenied

            form_class = self.get_form(request, obj, change=obj is not None)
            form = form_class(request.POST, request.FILES, instance=obj)
            if not form.is_valid():
                self.message_user(
                    request,
                    "Fix the highlighted errors before previewing.",
                    messages.ERROR,
                )
                if obj is None:
                    return HttpResponseRedirect(reverse("admin:blog_post_add"))
                return HttpResponseRedirect(
                    reverse("admin:blog_post_change", args=[obj.pk])
                )

            post = form.save(commit=False)
            post_tags = form.cleaned_data.get("tags", [])
        else:
            if obj is None:
                self.message_user(
                    request,
                    "Save this post, or use the Preview button on the form.",
                    messages.WARNING,
                )
                return HttpResponseRedirect(reverse("admin:blog_post_add"))
            if not self.has_view_or_change_permission(request, obj):
                raise PermissionDenied

            post = obj
            post_tags = obj.tags.all()

        return self._render_preview(request, post, post_tags)

    def _render_preview(self, request, post, post_tags):
        if post.published_at is None:
            post.published_at = timezone.now()

        canonical_path = (
            post.get_absolute_url()
            if post.slug
            else reverse("admin:blog_post_preview")
        )
        return render(
            request,
            "blog/post_detail.html",
            {
                "post": post,
                "canonical_url": request.build_absolute_uri(canonical_path),
                "webmentions": [],
                "post_tags": post_tags,
                "is_preview": True,
            },
        )

    def generate_description_view(self, request, object_id):
        post = self.get_object(request, object_id)
        if post is None:
            self.message_user(
                request,
                "Post not found.",
                messages.ERROR,
            )
            return HttpResponseRedirect(reverse("admin:blog_post_changelist"))

        redirect_url = reverse("admin:blog_post_change", args=[post.pk])
        if request.method != "POST":
            self.message_user(
                request,
                "Use the Generate description button to update this post.",
                messages.WARNING,
            )
            return HttpResponseRedirect(redirect_url)

        self._generate_description(request, post)

        return HttpResponseRedirect(redirect_url)

    def response_add(self, request, obj, post_url_continue=None):
        if "_generate_description" in request.POST:
            self._generate_description(request, obj)
            return HttpResponseRedirect(reverse("admin:blog_post_change", args=[obj.pk]))

        if "_publish_now" in request.POST:
            self.message_user(request, "Published this post now.", messages.SUCCESS)
            return HttpResponseRedirect(reverse("admin:blog_post_change", args=[obj.pk]))

        return super().response_add(request, obj, post_url_continue)

    def response_change(self, request, obj):
        if "_generate_description" in request.POST:
            self._generate_description(request, obj)
            return HttpResponseRedirect(reverse("admin:blog_post_change", args=[obj.pk]))

        if "_publish_now" in request.POST:
            self.message_user(request, "Published this post now.", messages.SUCCESS)
            return HttpResponseRedirect(reverse("admin:blog_post_change", args=[obj.pk]))

        return super().response_change(request, obj)

    def _generate_description(self, request, post):
        try:
            post.description = generate_post_description(post)
        except DescriptionGenerationError as error:
            self.message_user(
                request,
                str(error),
                messages.ERROR,
            )
            return

        post.save(update_fields=["description"])
        self.message_user(
            request,
            "Saved changes and generated a new description.",
            messages.SUCCESS,
        )

    @admin.display(ordering="upvotes_count", description="Upvotes")
    def upvotes(self, obj):
        return obj.upvotes_count

    @admin.action(description="Publish selected posts")
    def publish_posts(self, request, queryset):
        publish_time = timezone.now()
        queryset.filter(published_at__isnull=True).update(
            status=Post.PUBLISHED,
            published_at=publish_time,
        )
        queryset.filter(published_at__isnull=False).update(status=Post.PUBLISHED)
        for post in queryset:
            post.refresh_from_db(fields=["status", "published_at"])
            send_webmentions_for_post_async(post)

    @admin.action(description="Unpublish selected posts")
    def unpublish_posts(self, request, queryset):
        queryset.update(status=Post.DRAFT, published_at=None)

    @admin.action(description="Generate descriptions with OpenRouter")
    def generate_descriptions(self, request, queryset):
        generated = 0
        for post in queryset:
            try:
                post.description = generate_post_description(post)
            except DescriptionGenerationError as error:
                self.message_user(
                    request,
                    f"{post.title}: {error}",
                    messages.ERROR,
                )
                continue

            post.save(update_fields=["description"])
            generated += 1

        if generated:
            self.message_user(
                request,
                f"Generated descriptions for {generated} post(s).",
                messages.SUCCESS,
            )


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    change_form_template = "admin/blog/note/change_form.html"
    fields = ["body", "tags", "status", "published_at"]
    list_display = ["display_title", "status", "published_at"]
    list_filter = ["status", "tags"]
    search_fields = ["body", "tags__name"]
    filter_horizontal = ["tags"]
    actions = ["publish_notes", "unpublish_notes"]

    @admin.display(description="Note", ordering="published_at")
    def display_title(self, obj):
        return obj.display_title

    def save_model(self, request, obj, form, change):
        if "_publish_now" in request.POST:
            obj.status = Note.PUBLISHED
            obj.published_at = timezone.now()
        super().save_model(request, obj, form, change)

    def response_add(self, request, obj, post_url_continue=None):
        if "_publish_now" in request.POST:
            self.message_user(request, "Published this note now.", messages.SUCCESS)
            return HttpResponseRedirect(reverse("admin:blog_note_change", args=[obj.pk]))
        return super().response_add(request, obj, post_url_continue)

    def response_change(self, request, obj):
        if "_publish_now" in request.POST:
            self.message_user(request, "Published this note now.", messages.SUCCESS)
            return HttpResponseRedirect(reverse("admin:blog_note_change", args=[obj.pk]))
        return super().response_change(request, obj)

    @admin.action(description="Publish selected notes")
    def publish_notes(self, request, queryset):
        for note in queryset:
            note.status = Note.PUBLISHED
            if note.published_at is None:
                note.published_at = timezone.now()
            note.save()

    @admin.action(description="Unpublish selected notes")
    def unpublish_notes(self, request, queryset):
        queryset.update(status=Note.DRAFT, published_at=None)


@admin.register(PostMedia)
class PostMediaAdmin(admin.ModelAdmin):
    list_display = ["title", "slug", "file", "created_at"]
    search_fields = ["title", "slug", "alt_text", "file"]
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ["created_at", "markdown_snippet_display"]
    fields = [
        "title",
        "slug",
        "alt_text",
        "file",
        "markdown_snippet_display",
        "created_at",
    ]

    @admin.display(description="Markdown snippet")
    def markdown_snippet_display(self, obj):
        if not obj.pk:
            return "Save this upload to generate a Markdown snippet."
        return format_html("<code>{}</code>", obj.markdown_snippet)
