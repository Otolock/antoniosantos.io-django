from django.contrib import admin
from django.contrib import messages
from django import forms
from django.core.exceptions import PermissionDenied
from django.db.models import Case, F, IntegerField, TextField, Value, When
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils import timezone
from markdown import markdown
from pathlib import Path
from urllib.parse import urlencode

from .html import sanitize_html
from .forms import QuickNoteForm
from .llm import DescriptionGenerationError, generate_post_description
from .models import ContentRevision, Note, Post, PostMedia, Subscriber, Tag
from .revisions import create_revision, restore_revision, revision_diff
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
admin.site.site_header = "Antonio's Studio"
admin.site.site_title = "Antonio's Studio"
admin.site.index_title = "Publishing desk"


class MarkdownEditorAdminMixin:
    """Shared, writing-focused behavior for posts and notes."""

    change_list_template = "admin/blog/content_change_list.html"
    list_per_page = 25
    formfield_overrides = {
        TextField: {
            "widget": forms.Textarea(
                attrs={
                    "class": "markdown-editor",
                    "data-markdown-editor": "true",
                    "spellcheck": "true",
                    "autocapitalize": "sentences",
                    "placeholder": "Paste Markdown from iA Writer, or start writing here…",
                }
            )
        }
    }

    def render_change_form(self, request, context, *args, **kwargs):
        context["available_media"] = PostMedia.objects.order_by("-created_at", "-pk")[
            :25
        ]
        original = context.get("original")
        if original:
            context["revision_history_url"] = reverse(
                f"admin:{self.opts.app_label}_{self.opts.model_name}_revisions",
                args=[original.pk],
            )
        context["markdown_render_url"] = reverse(
            f"admin:{self.opts.app_label}_{self.opts.model_name}_render_markdown"
        )
        context["inline_media_upload_url"] = reverse(
            "admin:blog_postmedia_editor_upload"
        )
        return super().render_change_form(request, context, *args, **kwargs)

    def get_urls(self):
        urls = super().get_urls()
        app_label = self.opts.app_label
        model_name = self.opts.model_name
        custom_urls = [
            path(
                "render-markdown/",
                self.admin_site.admin_view(self.render_markdown_view),
                name=f"{app_label}_{model_name}_render_markdown",
            ),
            path(
                "<path:object_id>/revisions/",
                self.admin_site.admin_view(self.revision_history_view),
                name=f"{app_label}_{model_name}_revisions",
            ),
            path(
                "<path:object_id>/toggle-publish/",
                self.admin_site.admin_view(self.toggle_publish_view),
                name=f"{app_label}_{model_name}_toggle_publish",
            ),
            path(
                "<path:object_id>/revisions/<int:revision_id>/restore/",
                self.admin_site.admin_view(self.restore_revision_view),
                name=f"{app_label}_{model_name}_revision_restore",
            ),
        ]
        return custom_urls + urls

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        duplicate_id = request.GET.get("duplicate")
        if not duplicate_id:
            return initial
        original = self.get_object(request, duplicate_id)
        if original is None or not self.has_view_or_change_permission(request, original):
            return initial

        duplicate = {
            "body": original.body,
            "tags": list(original.tags.values_list("pk", flat=True)),
            "status": original.DRAFT,
            "published_at": None,
        }
        if isinstance(original, Post):
            base_slug = f"{original.slug}-copy"[: original._meta.get_field("slug").max_length]
            slug = base_slug
            suffix = 2
            while Post.objects.filter(slug=slug).exists():
                suffix_text = f"-{suffix}"
                slug = f"{base_slug[: original._meta.get_field('slug').max_length - len(suffix_text)]}{suffix_text}"
                suffix += 1
            duplicate.update(
                {
                    "title": f"Copy of {original.title}"[:200],
                    "slug": slug,
                    "description": original.description,
                    "reply_to_url": original.reply_to_url,
                    "reply_to_title": original.reply_to_title,
                    "upvotes_count": 0,
                }
            )
        initial.update(duplicate)
        return initial

    def render_markdown_view(self, request):
        if request.method != "POST":
            return JsonResponse({"error": "POST required."}, status=405)
        if not self.has_view_or_change_permission(request):
            raise PermissionDenied
        body = request.POST.get("body", "")
        return JsonResponse({"html": sanitize_html(markdown(body))})

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        create_revision(form.instance, request.user, reason="Saved in editor")

    def revision_history_view(self, request, object_id):
        obj = self.get_object(request, object_id)
        if obj is None:
            self.message_user(request, "Content not found.", messages.ERROR)
            return HttpResponseRedirect(
                reverse(f"admin:{self.opts.app_label}_{self.opts.model_name}_changelist")
            )
        if not self.has_view_or_change_permission(request, obj):
            raise PermissionDenied

        revisions = list(
            ContentRevision.objects.filter(
                content_type=self.opts.model_name,
                object_id=obj.pk,
            ).select_related("created_by")
        )
        revision_rows = []
        for index, revision in enumerate(revisions):
            older = revisions[index + 1] if index + 1 < len(revisions) else None
            revision_rows.append(
                {
                    "revision": revision,
                    "diff": revision_diff(older, revision),
                    "restore_url": reverse(
                        f"admin:{self.opts.app_label}_{self.opts.model_name}_revision_restore",
                        args=[obj.pk, revision.pk],
                    ),
                }
            )

        context = {
            **self.admin_site.each_context(request),
            "title": f"Revisions: {obj}",
            "opts": self.opts,
            "original": obj,
            "revision_rows": revision_rows,
            "change_url": reverse(
                f"admin:{self.opts.app_label}_{self.opts.model_name}_change",
                args=[obj.pk],
            ),
        }
        return render(request, "admin/blog/content_revisions.html", context)

    def restore_revision_view(self, request, object_id, revision_id):
        obj = self.get_object(request, object_id)
        if obj is None:
            self.message_user(request, "Content not found.", messages.ERROR)
            return HttpResponseRedirect(
                reverse(f"admin:{self.opts.app_label}_{self.opts.model_name}_changelist")
            )
        if not self.has_change_permission(request, obj):
            raise PermissionDenied

        change_url = reverse(
            f"admin:{self.opts.app_label}_{self.opts.model_name}_change",
            args=[obj.pk],
        )
        if request.method != "POST":
            self.message_user(request, "Use the Restore button on the revisions page.", messages.WARNING)
            return HttpResponseRedirect(change_url)

        revision = get_object_or_404(
            ContentRevision,
            pk=revision_id,
            content_type=self.opts.model_name,
            object_id=obj.pk,
        )
        restore_revision(obj, revision)
        create_revision(
            obj,
            request.user,
            reason=f"Restored revision from {revision.created_at:%Y-%m-%d %H:%M}",
        )
        self.message_user(request, "Restored the selected revision.", messages.SUCCESS)
        return HttpResponseRedirect(change_url)

    def toggle_publish_view(self, request, object_id):
        obj = self.get_object(request, object_id)
        if obj is None:
            self.message_user(request, "Content not found.", messages.ERROR)
            return HttpResponseRedirect(
                reverse(f"admin:{self.opts.app_label}_{self.opts.model_name}_changelist")
            )
        if not self.has_change_permission(request, obj):
            raise PermissionDenied
        if request.method != "POST":
            self.message_user(request, "Use the list action to change publication status.", messages.WARNING)
            return HttpResponseRedirect(
                reverse(f"admin:{self.opts.app_label}_{self.opts.model_name}_changelist")
            )

        if obj.status == obj.PUBLISHED:
            obj.status = obj.DRAFT
            obj.published_at = None
            action = "Unpublished"
        else:
            obj.status = obj.PUBLISHED
            obj.published_at = timezone.now()
            action = "Published"
        obj.save()
        create_revision(obj, request.user, reason=f"{action} from content list")
        if isinstance(obj, Post) and obj.is_published:
            send_webmentions_for_post_async(obj)
        self.message_user(request, f"{action} {obj}.", messages.SUCCESS)
        return HttpResponseRedirect(
            reverse(f"admin:{self.opts.app_label}_{self.opts.model_name}_changelist")
        )

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context.update(
            {
                "content_type_label": self.model._meta.verbose_name,
                "content_type_label_plural": self.model._meta.verbose_name_plural,
                "content_list_description": self.content_list_description,
            }
        )
        return super().changelist_view(request, extra_context=extra_context)

    @admin.display(ordering="status", description="Status")
    def status_badge(self, obj):
        status = obj.status
        label = obj.get_status_display()
        if (
            status == obj.PUBLISHED
            and obj.published_at
            and obj.published_at > timezone.now()
        ):
            status = "scheduled"
            label = "Scheduled"
        return format_html(
            '<span class="content-status content-status--{}">{}</span>',
            status,
            label,
        )

    @admin.display(description="Quick actions")
    def quick_actions(self, obj):
        app_label = self.opts.app_label
        model_name = self.opts.model_name
        preview_url = reverse(f"admin:{app_label}_{model_name}_preview", args=[obj.pk])
        add_url = reverse(f"admin:{app_label}_{model_name}_add")
        duplicate_url = f"{add_url}?{urlencode({'duplicate': obj.pk})}"
        toggle_url = reverse(
            f"admin:{app_label}_{model_name}_toggle_publish",
            args=[obj.pk],
        )
        toggle_label = "Unpublish" if obj.status == obj.PUBLISHED else "Publish"
        view_link = ""
        if obj.is_published:
            view_link = format_html(
                '<a href="{}" target="_blank" rel="noopener">View</a>',
                obj.get_absolute_url(),
            )
        return format_html(
            '<details class="content-quick-menu"><summary>Actions</summary>'
            '<span class="content-quick-actions">'
            '<a href="{}" target="_blank" rel="noopener">Preview</a>'
            '{}'
            '<a href="{}">Duplicate</a>'
            '<button type="submit" formmethod="post" formaction="{}">{}</button>'
            "</span></details>",
            preview_url,
            view_link,
            duplicate_url,
            toggle_url,
            toggle_label,
        )


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
class PostAdmin(MarkdownEditorAdminMixin, admin.ModelAdmin):
    content_list_description = "Draft, schedule, and publish long-form writing."
    change_form_template = "admin/blog/post/change_form.html"
    fieldsets = [
        (None, {"fields": ["title", "body"], "classes": ["writing-section"]}),
        (
            "Publishing",
            {
                "fields": ["status", "published_at"],
                "classes": ["publishing-section"],
                "description": "Save as a draft, schedule it, or publish immediately.",
            },
        ),
        (
            "Post details",
            {
                "fields": [
                    "slug",
                    "description",
                    "tags",
                    "reply_to_url",
                    "reply_to_title",
                    "upvotes_count",
                ],
                "classes": ["details-section"],
                "description": "Optional URL, summary, tags, and reply context.",
            },
        ),
    ]
    list_display = ["title", "status_badge", "published_at", "upvotes", "quick_actions"]
    list_filter = ["status", "tags"]
    search_fields = ["title", "body", "tags__name"]
    prepopulated_fields = {"slug": ("title",)}
    autocomplete_fields = ["tags"]
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
        original = context.get("original")
        if original:
            context["post_preview_url"] = reverse(
                "admin:blog_post_preview",
                args=[original.pk],
            )
        else:
            context["post_preview_url"] = reverse("admin:blog_post_preview")
        context["content_type_label"] = "post"
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
        create_revision(post, request.user, reason="Generated description")
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
            create_revision(
                post,
                getattr(request, "user", None),
                reason="Published from bulk action",
            )
            send_webmentions_for_post_async(post)

    @admin.action(description="Unpublish selected posts")
    def unpublish_posts(self, request, queryset):
        for post in queryset:
            post.status = Post.DRAFT
            post.published_at = None
            post.save(update_fields=["status", "published_at"])
            create_revision(
                post,
                getattr(request, "user", None),
                reason="Unpublished from bulk action",
            )

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
            create_revision(
                post,
                getattr(request, "user", None),
                reason="Generated description from bulk action",
            )
            generated += 1

        if generated:
            self.message_user(
                request,
                f"Generated descriptions for {generated} post(s).",
                messages.SUCCESS,
            )


@admin.register(Note)
class NoteAdmin(MarkdownEditorAdminMixin, admin.ModelAdmin):
    content_list_description = "Short-form updates, newest drafts first."
    change_form_template = "admin/blog/note/change_form.html"
    fieldsets = [
        (None, {"fields": ["body"], "classes": ["writing-section"]}),
        (
            "Publishing",
            {
                "fields": ["status", "published_at"],
                "classes": ["publishing-section"],
                "description": "Save as a draft, schedule it, or publish immediately.",
            },
        ),
        (
            "Note details",
            {
                "fields": ["tags"],
                "classes": ["details-section"],
                "description": "Optional tags for organizing this note.",
            },
        ),
    ]
    list_display = ["note_summary", "status_badge", "published_at", "quick_actions"]
    list_filter = ["status", "tags"]
    search_fields = ["body", "tags__name"]
    autocomplete_fields = ["tags"]
    actions = ["publish_notes", "unpublish_notes"]

    def render_change_form(self, request, context, *args, **kwargs):
        original = context.get("original")
        if original:
            context["content_preview_url"] = reverse(
                "admin:blog_note_preview",
                args=[original.pk],
            )
        else:
            context["content_preview_url"] = reverse("admin:blog_note_preview")
        context["content_type_label"] = "note"
        return super().render_change_form(request, context, *args, **kwargs)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "quick/",
                self.admin_site.admin_view(self.quick_note_view),
                name="blog_note_quick",
            ),
            path(
                "preview/",
                self.admin_site.admin_view(self.preview_view),
                name="blog_note_preview",
            ),
            path(
                "<path:object_id>/preview/",
                self.admin_site.admin_view(self.preview_view),
                name="blog_note_preview",
            ),
        ]
        return custom_urls + urls

    def quick_note_view(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied

        if request.method == "POST":
            form = QuickNoteForm(request.POST)
            if form.is_valid():
                note = form.save(commit=False)
                note.status = Note.PUBLISHED
                note.published_at = timezone.now()
                note.save()
                form.save_m2m()
                create_revision(note, request.user, reason="Published from quick note")
                self.message_user(request, "Published the note.", messages.SUCCESS)
                return HttpResponseRedirect(
                    reverse("admin:blog_note_change", args=[note.pk])
                )
        else:
            form = QuickNoteForm()

        context = {
            **self.admin_site.each_context(request),
            "title": "Quick note",
            "opts": self.opts,
            "form": form,
            "markdown_render_url": reverse("admin:blog_note_render_markdown"),
            "inline_media_upload_url": reverse("admin:blog_postmedia_editor_upload"),
            "note_list_url": reverse("admin:blog_note_changelist"),
        }
        return render(request, "admin/blog/note/quick_note.html", context)

    def preview_view(self, request, object_id=None):
        obj = self.get_object(request, object_id) if object_id else None
        if object_id and obj is None:
            self.message_user(request, "Note not found.", messages.ERROR)
            return HttpResponseRedirect(reverse("admin:blog_note_changelist"))

        if request.method != "POST":
            if obj is not None and self.has_view_or_change_permission(request, obj):
                if obj.published_at is None:
                    obj.published_at = timezone.now()
                return render(
                    request,
                    "blog/note_detail.html",
                    {
                        "note": obj,
                        "canonical_url": request.build_absolute_uri(obj.get_absolute_url()),
                        "note_tags": obj.tags.all(),
                        "is_preview": True,
                    },
                )
            self.message_user(
                request,
                "Use the Preview button on the note form.",
                messages.WARNING,
            )
            target = "admin:blog_note_change" if obj else "admin:blog_note_add"
            args = [obj.pk] if obj else None
            return HttpResponseRedirect(reverse(target, args=args))

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
            target = "admin:blog_note_change" if obj else "admin:blog_note_add"
            args = [obj.pk] if obj else None
            return HttpResponseRedirect(reverse(target, args=args))

        note = form.save(commit=False)
        if note.published_at is None:
            note.published_at = timezone.now()
        note_tags = form.cleaned_data.get("tags", [])
        canonical_path = (
            note.get_absolute_url()
            if note.slug
            else reverse("admin:blog_note_preview")
        )
        return render(
            request,
            "blog/note_detail.html",
            {
                "note": note,
                "canonical_url": request.build_absolute_uri(canonical_path),
                "note_tags": note_tags,
                "is_preview": True,
            },
        )

    @admin.display(description="Note", ordering="published_at")
    def display_title(self, obj):
        return obj.display_title

    @admin.display(description="Note", ordering="published_at")
    def note_summary(self, obj):
        summary = " ".join(obj.body.split())
        if len(summary) > 90:
            summary = f"{summary[:87].rstrip()}…"
        return format_html(
            '<span class="note-summary"><strong>{}</strong><small>{}</small></span>',
            summary or "Empty note",
            obj.display_title,
        )

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
            create_revision(
                note,
                getattr(request, "user", None),
                reason="Published from bulk action",
            )

    @admin.action(description="Unpublish selected notes")
    def unpublish_notes(self, request, queryset):
        for note in queryset:
            note.status = Note.DRAFT
            note.published_at = None
            note.save(update_fields=["status", "published_at"])
            create_revision(
                note,
                getattr(request, "user", None),
                reason="Unpublished from bulk action",
            )


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

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "editor-upload/",
                self.admin_site.admin_view(self.editor_upload_view),
                name="blog_postmedia_editor_upload",
            ),
        ]
        return custom_urls + urls

    def editor_upload_view(self, request):
        if request.method != "POST":
            return JsonResponse({"error": "POST required."}, status=405)
        if not self.has_add_permission(request):
            raise PermissionDenied

        upload = request.FILES.get("file")
        if upload is None:
            return JsonResponse({"error": "Choose an image to upload."}, status=400)
        if not (upload.content_type or "").startswith("image/"):
            return JsonResponse({"error": "Only image uploads are supported here."}, status=400)

        media = PostMedia.objects.create(
            title=request.POST.get("title", "").strip() or Path(upload.name).stem,
            alt_text=request.POST.get("alt_text", "").strip(),
            file=upload,
        )
        return JsonResponse(
            {
                "id": media.pk,
                "title": media.title,
                "snippet": media.markdown_snippet,
                "url": media.file.url,
            }
        )

    @admin.display(description="Markdown snippet")
    def markdown_snippet_display(self, obj):
        if not obj.pk:
            return "Save this upload to generate a Markdown snippet."
        return format_html("<code>{}</code>", obj.markdown_snippet)
