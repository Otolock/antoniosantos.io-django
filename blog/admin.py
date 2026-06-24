from django.contrib import admin
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils import timezone

from .llm import DescriptionGenerationError, generate_post_description
from .models import Post, Subscriber


@admin.register(Subscriber)
class SubscriberAdmin(admin.ModelAdmin):
    list_display = ["email", "created_at", "source_path"]
    search_fields = ["email", "source_path"]
    readonly_fields = ["created_at"]


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    change_form_template = "admin/blog/post/change_form.html"
    list_display = ["title", "status", "published_at"]
    list_filter = ["status"]
    search_fields = ["title", "body"]
    prepopulated_fields = {"slug": ("title",)}
    actions = ["publish_posts", "unpublish_posts", "generate_descriptions"]

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:object_id>/generate-description/",
                self.admin_site.admin_view(self.generate_description_view),
                name="blog_post_generate_description",
            ),
        ]
        return custom_urls + urls

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

        try:
            post.description = generate_post_description(post)
        except DescriptionGenerationError as error:
            self.message_user(
                request,
                str(error),
                messages.ERROR,
            )
        else:
            post.save(update_fields=["description"])
            self.message_user(
                request,
                "Generated a new description.",
                messages.SUCCESS,
            )

        return HttpResponseRedirect(redirect_url)

    @admin.action(description="Publish selected posts")
    def publish_posts(self, request, queryset):
        queryset.update(status=Post.PUBLISHED, published_at=timezone.now())

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
