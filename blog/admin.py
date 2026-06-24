from django.contrib import admin
from django.utils import timezone

from .models import Post


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ["title", "status", "published_at"]
    list_filter = ["status"]
    search_fields = ["title", "body"]
    prepopulated_fields = {"slug": ("title",)}
    actions = ["publish_posts", "unpublish_posts"]

    @admin.action(description="Publish selected posts")
    def publish_posts(self, request, queryset):
        queryset.update(status=Post.PUBLISHED, published_at=timezone.now())

    @admin.action(description="Unpublish selected posts")
    def unpublish_posts(self, request, queryset):
        queryset.update(status=Post.DRAFT, published_at=None)
