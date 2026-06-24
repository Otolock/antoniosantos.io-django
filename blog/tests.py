from django.contrib import admin
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .admin import PostAdmin
from .models import Post


class PostTests(TestCase):
    def make_post(self, **kwargs):
        defaults = {
            "title": "Test post",
            "slug": "test-post",
            "body": "Hello **world**.",
            "description": "A test post",
            "status": Post.PUBLISHED,
            "published_at": timezone.now(),
        }
        defaults.update(kwargs)
        return Post.objects.create(**defaults)

    def test_markdown_body_is_rendered_as_html(self):
        post = self.make_post(body="Hello **world**.")

        self.assertIn("<strong>world</strong>", post.body_html)

    def test_is_published_requires_published_status_and_publish_date(self):
        live_post = self.make_post(slug="live")
        draft_post = self.make_post(
            slug="draft",
            status=Post.DRAFT,
        )
        scheduled_post = self.make_post(
            slug="scheduled",
            published_at=timezone.now() + timezone.timedelta(days=1),
        )
        unpublished_post = self.make_post(
            slug="unpublished",
            published_at=None,
        )

        self.assertTrue(live_post.is_published)
        self.assertFalse(draft_post.is_published)
        self.assertFalse(scheduled_post.is_published)
        self.assertFalse(unpublished_post.is_published)


class PostViewTests(TestCase):
    def make_post(self, **kwargs):
        defaults = {
            "title": "Live post",
            "slug": "live-post",
            "body": "Hello **world**.",
            "description": "A live post",
            "status": Post.PUBLISHED,
            "published_at": timezone.now(),
        }
        defaults.update(kwargs)
        return Post.objects.create(**defaults)

    def test_post_list_only_shows_published_posts_with_current_publish_dates(self):
        self.make_post()
        self.make_post(
            title="Draft post",
            slug="draft-post",
            status=Post.DRAFT,
        )
        self.make_post(
            title="Scheduled post",
            slug="scheduled-post",
            published_at=timezone.now() + timezone.timedelta(days=1),
        )

        response = self.client.get(reverse("blog:post_list"))

        self.assertContains(response, "Live post")
        self.assertNotContains(response, "Draft post")
        self.assertNotContains(response, "Scheduled post")

    def test_post_list_shows_about_and_latest_five_posts(self):
        now = timezone.now()
        for index in range(6):
            self.make_post(
                title=f"Post {index + 1}",
                slug=f"post-{index + 1}",
                published_at=now - timezone.timedelta(days=index),
            )

        response = self.client.get(reverse("blog:post_list"))

        self.assertContains(response, "I'm Antonio")
        self.assertContains(response, reverse("blog:archive"))
        for index in range(5):
            self.assertContains(response, f"Post {index + 1}")
        self.assertNotContains(response, "Post 6")

    def test_archive_lists_all_published_posts_with_current_publish_dates(self):
        self.make_post()
        self.make_post(
            title="Older post",
            slug="older-post",
            published_at=timezone.now() - timezone.timedelta(days=30),
        )
        self.make_post(
            title="Draft post",
            slug="draft-post",
            status=Post.DRAFT,
        )
        self.make_post(
            title="Scheduled post",
            slug="scheduled-post",
            published_at=timezone.now() + timezone.timedelta(days=1),
        )

        response = self.client.get(reverse("blog:archive"))

        self.assertContains(response, "Live post")
        self.assertContains(response, "Older post")
        self.assertNotContains(response, "Draft post")
        self.assertNotContains(response, "Scheduled post")

    def test_post_detail_renders_live_post_markdown(self):
        self.make_post()

        response = self.client.get(
            reverse("blog:post_detail", args=["live-post"])
        )

        self.assertContains(response, "Live post")
        self.assertContains(response, "<strong>world</strong>")

    def test_post_detail_returns_404_for_drafts_and_scheduled_posts(self):
        self.make_post(
            title="Draft post",
            slug="draft-post",
            status=Post.DRAFT,
        )
        self.make_post(
            title="Scheduled post",
            slug="scheduled-post",
            published_at=timezone.now() + timezone.timedelta(days=1),
        )

        for slug in ["draft-post", "scheduled-post"]:
            with self.subTest(slug=slug):
                response = self.client.get(
                    reverse("blog:post_detail", args=[slug])
                )
                self.assertEqual(response.status_code, 404)


class LatestPostsFeedTests(TestCase):
    def make_post(self, **kwargs):
        defaults = {
            "title": "Live post",
            "slug": "live-post",
            "body": "Hello **world**.",
            "description": "A live post",
            "status": Post.PUBLISHED,
            "published_at": timezone.now(),
        }
        defaults.update(kwargs)
        return Post.objects.create(**defaults)

    def test_rss_only_includes_published_posts_with_current_publish_dates(self):
        self.make_post()
        self.make_post(
            title="Draft post",
            slug="draft-post",
            status=Post.DRAFT,
        )
        self.make_post(
            title="Scheduled post",
            slug="scheduled-post",
            published_at=timezone.now() + timezone.timedelta(days=1),
        )

        response = self.client.get(reverse("blog:rss"))

        self.assertContains(response, "Live post")
        self.assertContains(response, "A live post")
        self.assertNotContains(response, "Draft post")
        self.assertNotContains(response, "Scheduled post")


class PostAdminTests(TestCase):
    def test_publish_posts_action_publishes_selected_drafts_now(self):
        post = Post.objects.create(
            title="Draft post",
            slug="draft-post",
            body="Draft body",
            status=Post.DRAFT,
            published_at=None,
        )
        model_admin = PostAdmin(Post, admin.site)

        model_admin.publish_posts(None, Post.objects.filter(pk=post.pk))

        post.refresh_from_db()
        self.assertEqual(post.status, Post.PUBLISHED)
        self.assertIsNotNone(post.published_at)
        self.assertTrue(post.is_published)

    def test_unpublish_posts_action_switches_selected_posts_to_draft(self):
        post = Post.objects.create(
            title="Live post",
            slug="live-post",
            body="Live body",
            status=Post.PUBLISHED,
            published_at=timezone.now(),
        )
        model_admin = PostAdmin(Post, admin.site)

        model_admin.unpublish_posts(None, Post.objects.filter(pk=post.pk))

        post.refresh_from_db()
        self.assertEqual(post.status, Post.DRAFT)
        self.assertIsNone(post.published_at)
        self.assertFalse(post.is_published)
