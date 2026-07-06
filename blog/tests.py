from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
import tempfile
from unittest.mock import patch

from . import llm
from .admin import PostAdmin
from .feeds import LEGACY_RSS_GUID_SLUGS
from .models import Comment, Post, PostMedia, Subscriber, Tag
from webmentions.models import Webmention


TEST_STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}


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

    def test_body_html_strips_scriptable_html(self):
        post = self.make_post(
            body='<script>alert(1)</script><a href="javascript:alert(1)" onclick="x()">x</a>'
        )

        self.assertNotIn("<script", post.body_html)
        self.assertNotIn("javascript:", post.body_html)
        self.assertNotIn("onclick", post.body_html)
        self.assertIn(">x</a>", post.body_html)

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
            status=Post.DRAFT,
        )
        Post.objects.filter(pk=unpublished_post.pk).update(
            status=Post.PUBLISHED,
            published_at=None,
        )
        unpublished_post.refresh_from_db()

        self.assertTrue(live_post.is_published)
        self.assertFalse(draft_post.is_published)
        self.assertFalse(scheduled_post.is_published)
        self.assertFalse(unpublished_post.is_published)

    def test_reserved_slugs_are_rejected_by_model_validation(self):
        post = Post(
            title="About",
            slug="about",
            body="This slug conflicts with a site route.",
        )

        with self.assertRaises(ValidationError) as context:
            post.full_clean()

        self.assertIn("slug", context.exception.error_dict)

    def test_save_sets_publish_date_when_published_without_date(self):
        post = self.make_post(
            status=Post.DRAFT,
            published_at=None,
        )

        before_save = timezone.now()
        post.status = Post.PUBLISHED
        post.save()
        after_save = timezone.now()

        post.refresh_from_db()
        self.assertGreaterEqual(post.published_at, before_save)
        self.assertLessEqual(post.published_at, after_save)

    def test_save_preserves_existing_publish_date_when_published(self):
        for published_at in [
            timezone.now() - timezone.timedelta(days=30),
            timezone.now() + timezone.timedelta(days=1),
        ]:
            with self.subTest(published_at=published_at):
                post = self.make_post(
                    slug=f"dated-post-{published_at:%s}",
                    status=Post.DRAFT,
                    published_at=published_at,
                )

                post.status = Post.PUBLISHED
                post.save()

                post.refresh_from_db()
                self.assertEqual(post.published_at, published_at)

    def test_posts_can_have_tags(self):
        post = self.make_post()
        django = Tag.objects.create(name="Django", slug="django")
        python = Tag.objects.create(name="Python", slug="python")

        post.tags.add(django, python)

        self.assertCountEqual(post.tags.all(), [django, python])
        self.assertCountEqual(django.posts.all(), [post])


class CommentTests(TestCase):
    def make_post(self):
        return Post.objects.create(
            title="Test post",
            slug="test-post",
            body="Hello.",
            status=Post.PUBLISHED,
            published_at=timezone.now(),
        )

    def test_save_sets_approved_at_when_approved(self):
        comment = Comment.objects.create(
            post=self.make_post(),
            author_name="Reader",
            author_email="reader@example.com",
            body="Nice post.",
        )

        comment.status = Comment.APPROVED
        comment.save(update_fields=["status"])

        comment.refresh_from_db()
        self.assertIsNotNone(comment.approved_at)

    def test_save_clears_approved_at_when_unapproved(self):
        comment = Comment.objects.create(
            post=self.make_post(),
            author_name="Reader",
            author_email="reader@example.com",
            body="Nice post.",
            status=Comment.APPROVED,
        )

        comment.status = Comment.REJECTED
        comment.save(update_fields=["status"])

        comment.refresh_from_db()
        self.assertIsNone(comment.approved_at)


@override_settings(STORAGES=TEST_STORAGES)
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

    def test_home_only_shows_published_posts_with_current_publish_dates(self):
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

        response = self.client.get(reverse("blog:home"))

        self.assertContains(response, "Live post")
        self.assertNotContains(response, "Draft post")
        self.assertNotContains(response, "Scheduled post")

    def test_home_shows_about_and_latest_five_posts(self):
        now = timezone.now()
        for index in range(6):
            self.make_post(
                title=f"Post {index + 1}",
                slug=f"post-{index + 1}",
                published_at=now - timezone.timedelta(days=index),
            )

        response = self.client.get(reverse("blog:home"))

        self.assertContains(response, "Hi, I'm")
        self.assertContains(response, "Antonio Santos")
        self.assertContains(response, reverse("blog:archive"))
        for index in range(5):
            self.assertContains(response, f"Post {index + 1}")
        self.assertNotContains(response, "Post 6")

    def test_home_marks_latest_posts_as_microformats_feed_entries(self):
        self.make_post()

        response = self.client.get(reverse("blog:home"))

        self.assertContains(response, 'class="h-feed post-list"')
        self.assertContains(response, 'class="p-name section-heading"')
        self.assertContains(response, 'class="h-entry post-list-item"')
        self.assertContains(response, 'class="p-name u-url"')
        self.assertContains(response, 'class="dt-published"')
        self.assertContains(response, 'class="p-summary"')

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

    def test_archive_marks_posts_as_microformats_feed_entries(self):
        self.make_post()

        response = self.client.get(reverse("blog:archive"))

        self.assertContains(response, 'class="h-feed post-list"')
        self.assertContains(
            response,
            'class="h-entry post-list-item with-divider"',
        )
        self.assertContains(response, 'class="dt-published"')
        self.assertContains(response, 'class="p-name u-url"')
        self.assertContains(response, 'class="p-summary"')

    def test_post_detail_renders_live_post_markdown(self):
        self.make_post()

        response = self.client.get(
            reverse("blog:post_detail", args=["live-post"])
        )

        self.assertContains(response, "Live post")
        self.assertContains(response, "<strong>world</strong>")

    def test_post_detail_shows_reply_context_when_post_replies_to_url(self):
        post = self.make_post(
            reply_to_url="https://example.net/a-note/",
            reply_to_title="Example note",
        )

        response = self.client.get(reverse("blog:post_detail", args=[post.slug]))

        self.assertContains(response, "↩")
        self.assertContains(response, "in reply to")
        self.assertContains(
            response,
            (
                '<a class="u-in-reply-to post-reply-link" '
                'href="https://example.net/a-note/" '
                'rel="in-reply-to">Example note</a>'
            ),
            html=True,
        )
        self.assertNotContains(response, "&gt; in reply to")

    def test_post_detail_shows_linked_tag_pills(self):
        post = self.make_post()
        django = Tag.objects.create(name="Django", slug="django")
        python = Tag.objects.create(name="Python", slug="python")
        post.tags.add(django, python)

        response = self.client.get(reverse("blog:post_detail", args=[post.slug]))

        self.assertContains(response, 'class="post-tags"')
        self.assertContains(
            response,
            f'<a class="tag-pill p-category" href="{reverse("blog:tag_detail", args=["django"])}">#Django</a>',
            html=True,
        )
        self.assertContains(
            response,
            f'<a class="tag-pill p-category" href="{reverse("blog:tag_detail", args=["python"])}">#Python</a>',
            html=True,
        )

    def test_tag_detail_lists_only_published_posts_for_tag(self):
        tag = Tag.objects.create(name="Django", slug="django")
        other_tag = Tag.objects.create(name="Python", slug="python")
        tagged_post = self.make_post(title="Tagged post", slug="tagged-post")
        older_tagged_post = self.make_post(
            title="Older tagged post",
            slug="older-tagged-post",
            published_at=timezone.now() - timezone.timedelta(days=30),
        )
        draft_post = self.make_post(
            title="Draft tagged post",
            slug="draft-tagged-post",
            status=Post.DRAFT,
        )
        scheduled_post = self.make_post(
            title="Scheduled tagged post",
            slug="scheduled-tagged-post",
            published_at=timezone.now() + timezone.timedelta(days=1),
        )
        other_post = self.make_post(title="Other tag post", slug="other-tag-post")
        tagged_post.tags.add(tag)
        older_tagged_post.tags.add(tag)
        draft_post.tags.add(tag)
        scheduled_post.tags.add(tag)
        other_post.tags.add(other_tag)

        response = self.client.get(reverse("blog:tag_detail", args=[tag.slug]))

        self.assertContains(response, "#Django")
        self.assertContains(response, "Tagged post")
        self.assertContains(response, "Older tagged post")
        self.assertNotContains(response, "Draft tagged post")
        self.assertNotContains(response, "Scheduled tagged post")
        self.assertNotContains(response, "Other tag post")

    def test_post_detail_marks_post_as_microformats_entry(self):
        self.make_post()

        response = self.client.get(
            reverse("blog:post_detail", args=["live-post"])
        )

        self.assertContains(response, 'class="h-entry post-entry"')
        self.assertContains(response, 'class="p-name post-title"')
        self.assertContains(response, 'class="p-author h-card"')
        self.assertNotContains(response, "Published by")
        self.assertNotContains(response, 'class="u-url text-cyan-200 hover:text-white"')
        self.assertContains(response, 'class="u-url" value="http://testserver/live-post/"')
        self.assertContains(response, 'class="dt-published"')
        self.assertContains(response, 'class="p-summary" value="A live post"')
        self.assertContains(response, 'class="e-content prose prose-invert post-prose"')

    def test_home_bio_includes_author_h_card(self):
        self.make_post()

        response = self.client.get(reverse("blog:home"))

        self.assertContains(response, 'class="h-card intro-section"')
        self.assertContains(response, 'class="p-name"')
        self.assertContains(response, 'class="u-url" value="/"')
        self.assertContains(response, "Antonio Santos")

    def test_now_page_marks_content_as_microformats_entry(self):
        response = self.client.get(reverse("blog:now"))

        self.assertContains(response, 'class="h-entry now-page"')
        self.assertContains(response, 'class="p-name page-heading"')
        self.assertContains(response, 'class="dt-updated"')
        self.assertContains(response, 'datetime="2026-06-24"')

    def test_post_detail_resolves_at_root_level_slug_url(self):
        self.make_post()

        response = self.client.get("/live-post/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Live post")

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_post_detail_uses_root_level_canonical_url(self):
        self.make_post()

        response = self.client.get(
            "/live-post/",
            secure=True,
            HTTP_HOST="example.com",
        )

        self.assertContains(
            response,
            '<link rel="canonical" href="https://example.com/live-post/">',
            html=True,
        )

    def test_post_detail_lists_approved_webmentions(self):
        self.make_post()
        Webmention.objects.create(
            source_url="https://source.example/reply/",
            target_url="http://testserver/live-post/",
            title="A reply elsewhere",
            status=Webmention.APPROVED,
        )

        response = self.client.get(
            reverse("blog:post_detail", args=["live-post"])
        )

        self.assertContains(response, "Webmentions")
        self.assertContains(response, "A reply elsewhere")
        self.assertContains(response, 'href="https://source.example/reply/"')
        self.assertContains(response, 'rel="nofollow ugc"')

    def test_post_detail_uses_source_url_for_untitled_webmentions(self):
        self.make_post()
        Webmention.objects.create(
            source_url="https://source.example/reply/",
            target_url="http://testserver/live-post/",
            status=Webmention.APPROVED,
        )

        response = self.client.get(
            reverse("blog:post_detail", args=["live-post"])
        )

        self.assertContains(response, "https://source.example/reply/")

    def test_post_detail_hides_unapproved_webmentions(self):
        self.make_post()
        for status in [Webmention.PENDING, Webmention.REJECTED, Webmention.SPAM]:
            Webmention.objects.create(
                source_url=f"https://source.example/{status}/",
                target_url="http://testserver/live-post/",
                title=f"{status} mention",
                status=status,
            )

        response = self.client.get(
            reverse("blog:post_detail", args=["live-post"])
        )

        self.assertNotContains(response, "Webmentions")
        self.assertNotContains(response, "pending mention")
        self.assertNotContains(response, "rejected mention")
        self.assertNotContains(response, "spam mention")

    def test_post_detail_hides_webmentions_for_other_targets(self):
        self.make_post()
        Webmention.objects.create(
            source_url="https://source.example/reply/",
            target_url="http://testserver/other-post/",
            title="Wrong target",
            status=Webmention.APPROVED,
        )

        response = self.client.get(
            reverse("blog:post_detail", args=["live-post"])
        )

        self.assertNotContains(response, "Webmentions")
        self.assertNotContains(response, "Wrong target")

    def test_legacy_posts_url_returns_permanent_redirect(self):
        self.make_post()

        response = self.client.get("/posts/live-post/")

        self.assertEqual(response.status_code, 301)

    def test_legacy_posts_url_redirect_target_is_root_level_slug(self):
        self.make_post()

        response = self.client.get("/posts/live-post/")

        self.assertEqual(response["Location"], "/live-post/")

    def test_legacy_singular_post_url_redirects_for_extra_compatibility(self):
        self.make_post()

        response = self.client.get("/post/live-post/")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/live-post/")

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

    def test_post_detail_lists_only_approved_comments(self):
        post = self.make_post()
        Comment.objects.create(
            post=post,
            author_name="Ada",
            author_email="ada@example.com",
            body="Approved and visible.",
            status=Comment.APPROVED,
        )
        Comment.objects.create(
            post=post,
            author_name="Pending Reader",
            author_email="pending@example.com",
            body="Still waiting.",
            status=Comment.PENDING,
        )

        response = self.client.get(reverse("blog:post_detail", args=[post.slug]))

        self.assertContains(response, "Approved and visible.")
        self.assertContains(response, "Ada")
        self.assertNotContains(response, "Still waiting.")
        self.assertNotContains(response, "Pending Reader")

    def test_comment_form_explains_moderation(self):
        self.make_post()

        response = self.client.get(reverse("blog:post_detail", args=["live-post"]))

        self.assertContains(
            response,
            "Comments are moderated, so yours may not appear right away.",
        )

    def test_comment_submission_creates_pending_comment_and_redirects(self):
        post = self.make_post()

        response = self.client.post(
            reverse("blog:post_detail", args=[post.slug]),
            {
                "author_name": " Reader ",
                "author_email": " READER@example.com ",
                "body": "This is a thoughtful note.",
                "website": "",
            },
            REMOTE_ADDR="127.0.0.1",
            HTTP_USER_AGENT="Test browser",
        )

        self.assertRedirects(response, post.get_absolute_url())
        comment = Comment.objects.get()
        self.assertEqual(comment.post, post)
        self.assertEqual(comment.author_name, "Reader")
        self.assertEqual(comment.author_email, "reader@example.com")
        self.assertEqual(comment.body, "This is a thoughtful note.")
        self.assertEqual(comment.status, Comment.PENDING)
        self.assertEqual(comment.ip_address, "127.0.0.1")
        self.assertEqual(comment.user_agent, "Test browser")

    def test_post_detail_shows_anonymous_upvote_button(self):
        post = self.make_post(upvotes_count=2)

        response = self.client.get(reverse("blog:post_detail", args=[post.slug]))

        self.assertContains(response, 'id="comment-actions"')
        self.assertContains(response, 'name="action" value="upvote"')
        self.assertContains(response, 'data-upvote-button')
        self.assertContains(response, 'data-upvote-count')
        self.assertContains(response, 'fetch(form.getAttribute("action")')
        self.assertContains(response, "&#8593;")
        self.assertNotContains(response, ">Upvote ")
        self.assertContains(response, ">2<")

    def test_upvote_increments_post_count_with_non_js_redirect(self):
        post = self.make_post(upvotes_count=2)

        response = self.client.post(
            reverse("blog:post_detail", args=[post.slug]),
            {"action": "upvote"},
        )

        self.assertRedirects(response, f"{post.get_absolute_url()}#comment-actions")
        post.refresh_from_db()
        self.assertEqual(post.upvotes_count, 3)
        self.assertEqual(Comment.objects.count(), 0)
        self.assertNotIn(f"post_{post.pk}_upvoted", response.cookies)

    def test_upvote_fetch_returns_updated_count_without_cookie(self):
        post = self.make_post(upvotes_count=2)

        response = self.client.post(
            reverse("blog:post_detail", args=[post.slug]),
            {"action": "upvote"},
            HTTP_X_REQUESTED_WITH="fetch",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"upvotes_count": 3})
        post.refresh_from_db()
        self.assertEqual(post.upvotes_count, 3)
        self.assertEqual(Comment.objects.count(), 0)
        self.assertNotIn(f"post_{post.pk}_upvoted", response.cookies)

    def test_comment_submission_allows_anonymous_author_without_email(self):
        post = self.make_post()

        response = self.client.post(
            reverse("blog:post_detail", args=[post.slug]),
            {
                "author_name": "",
                "author_email": "",
                "body": "This is a quiet anonymous note.",
                "website": "",
            },
        )

        self.assertRedirects(response, post.get_absolute_url())
        comment = Comment.objects.get()
        self.assertEqual(comment.author_name, "Anonymous")
        self.assertEqual(comment.author_email, "")
        self.assertEqual(comment.body, "This is a quiet anonymous note.")

    def test_comment_honeypot_discards_submission(self):
        post = self.make_post()

        response = self.client.post(
            reverse("blog:post_detail", args=[post.slug]),
            {
                "author_name": "Spammer",
                "author_email": "spam@example.com",
                "body": "Definitely normal.",
                "website": "https://spam.example.com",
            },
        )

        self.assertRedirects(response, post.get_absolute_url())
        self.assertEqual(Comment.objects.count(), 0)

    def test_invalid_comment_submission_does_not_create_comment(self):
        post = self.make_post()

        response = self.client.post(
            reverse("blog:post_detail", args=[post.slug]),
            {
                "author_name": "",
                "author_email": "not an email",
                "body": "",
                "website": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required.")
        self.assertEqual(Comment.objects.count(), 0)


@override_settings(STORAGES=TEST_STORAGES)
class SubscribeViewTests(TestCase):
    def test_subscribe_page_shows_email_form(self):
        response = self.client.get(reverse("blog:subscribe"))

        self.assertContains(response, "Subscribe")
        self.assertContains(response, 'type="email"')

    def test_subscribe_adds_normalized_email_and_redirects_back(self):
        response = self.client.post(
            reverse("blog:subscribe"),
            {
                "email": " Reader@Example.COM ",
                "next": reverse("blog:archive"),
            },
        )

        self.assertRedirects(response, reverse("blog:archive"))
        subscriber = Subscriber.objects.get()
        self.assertEqual(subscriber.email, "reader@example.com")
        self.assertEqual(subscriber.source_path, reverse("blog:archive"))

    def test_subscribe_without_next_redirects_to_subscribe_page(self):
        response = self.client.post(
            reverse("blog:subscribe"),
            {"email": "reader@example.com"},
        )

        self.assertRedirects(response, reverse("blog:subscribe"))
        self.assertEqual(Subscriber.objects.get().email, "reader@example.com")

    def test_subscribe_does_not_duplicate_existing_email(self):
        Subscriber.objects.create(email="reader@example.com")

        response = self.client.post(
            reverse("blog:subscribe"),
            {
                "email": "READER@example.com",
                "next": reverse("blog:home"),
            },
        )

        self.assertRedirects(response, reverse("blog:home"))
        self.assertEqual(Subscriber.objects.count(), 1)

    def test_subscribe_rejects_invalid_email(self):
        response = self.client.post(
            reverse("blog:subscribe"),
            {
                "email": "not an email",
                "next": reverse("blog:home"),
            },
        )

        self.assertRedirects(response, reverse("blog:home"))
        self.assertEqual(Subscriber.objects.count(), 0)


class LatestPostsFeedTests(TestCase):
    ATTACHED_FEED_GUID_SLUGS = [
        "i-don-t-need-it-and-neither-do-you",
        "a-letter-of-appreciation-to-fiction-writers",
        "i-turned-off-estimated-read-time",
        "getting-away-from-the-algorithm",
        "blog-design-refinements",
        "playing-around-as-a-sysadmin",
        "goodbye-hermes",
        "don-t-be-afraid-to-have-a-voice",
        "hands-on-with-fable-5",
        "im-done-picking-a-niche",
        "just-write",
        "agentic-ai-isnt-just-coding",
        "building-with-local-models",
        "learning-with-ai",
        "gemma-4-e4b-local-coding-assistant",
        "threads-spotlight-vol-1",
        "writing-at-the-speed-of-thought-27-days-with-wispr-flow",
        "my-ai-designed-site-was-perfect-and-that-was-the-problem",
        "design-breakdown-hero-accounting-firm",
    ]

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

    @override_settings(ALLOWED_HOSTS=["example.com"], SITE_URL="https://example.com")
    def test_rss_uses_canonical_post_urls(self):
        self.make_post()

        response = self.client.get(
            reverse("blog:rss"),
            secure=True,
            HTTP_HOST="example.com",
        )
        content = response.content.decode("utf-8")

        self.assertIn("https://example.com/live-post/", content)
        self.assertNotIn("https://example.com/post/live-post/", content)

    @override_settings(
        ALLOWED_HOSTS=["django.antoniosantos.io"],
        SITE_URL="https://antoniosantos.io",
    )
    def test_rss_preserves_legacy_posts_url_as_stable_guid(self):
        self.make_post(slug="just-write")

        response = self.client.get(
            reverse("blog:rss"),
            secure=True,
            HTTP_HOST="django.antoniosantos.io",
        )
        content = response.content.decode("utf-8")

        self.assertIn("<link>https://antoniosantos.io/just-write/</link>", content)
        self.assertIn(
            '<guid isPermaLink="false">'
            "https://antoniosantos.io/posts/just-write/"
            "</guid>",
            content,
        )
        self.assertNotIn(
            '<guid isPermaLink="true">'
            "https://antoniosantos.io/just-write/"
            "</guid>",
            content,
        )
        self.assertNotIn("https://django.antoniosantos.io", content)

    @override_settings(
        ALLOWED_HOSTS=["django.antoniosantos.io"],
        SITE_URL="https://antoniosantos.io",
    )
    def test_rss_guid_set_matches_pre_migration_feed(self):
        self.assertEqual(
            LEGACY_RSS_GUID_SLUGS,
            set(self.ATTACHED_FEED_GUID_SLUGS),
        )

        now = timezone.now()
        for index, slug in enumerate(self.ATTACHED_FEED_GUID_SLUGS):
            self.make_post(
                slug=slug,
                title=slug,
                published_at=now - timezone.timedelta(minutes=index),
            )

        response = self.client.get(
            reverse("blog:rss"),
            secure=True,
            HTTP_HOST="django.antoniosantos.io",
        )
        content = response.content.decode("utf-8")

        for slug in self.ATTACHED_FEED_GUID_SLUGS:
            self.assertIn(
                '<guid isPermaLink="false">'
                f"https://antoniosantos.io/posts/{slug}/"
                "</guid>",
                content,
            )
            self.assertIn(f"<link>https://antoniosantos.io/{slug}/</link>", content)
            self.assertNotIn(
                '<guid isPermaLink="true">'
                f"https://antoniosantos.io/{slug}/"
                "</guid>",
                content,
            )
        self.assertNotIn("https://django.antoniosantos.io", content)

    @override_settings(ALLOWED_HOSTS=["example.com"], SITE_URL="https://example.com")
    def test_rss_uses_canonical_guid_for_posts_not_in_legacy_feed(self):
        self.make_post(slug="new-django-post")

        response = self.client.get(
            reverse("blog:rss"),
            secure=True,
            HTTP_HOST="example.com",
        )
        content = response.content.decode("utf-8")

        self.assertIn("<link>https://example.com/new-django-post/</link>", content)
        self.assertIn(
            '<guid isPermaLink="true">'
            "https://example.com/new-django-post/"
            "</guid>",
            content,
        )
        self.assertNotIn("https://example.com/posts/new-django-post/", content)

    @override_settings(ALLOWED_HOSTS=["example.com"], SITE_URL="https://example.com")
    def test_rss_includes_post_publish_date(self):
        published_at = timezone.datetime(
            2026,
            6,
            23,
            18,
            46,
            18,
            tzinfo=timezone.UTC,
        )
        self.make_post(slug="just-write", published_at=published_at)

        response = self.client.get(
            reverse("blog:rss"),
            secure=True,
            HTTP_HOST="example.com",
        )
        content = response.content.decode("utf-8")

        self.assertIn("<pubDate>Tue, 23 Jun 2026 18:46:18 +0000</pubDate>", content)


class SitemapTests(TestCase):
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

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_sitemap_includes_public_pages_and_published_posts(self):
        self.make_post()

        response = self.client.get(
            reverse("blog:sitemap"),
            secure=True,
            HTTP_HOST="example.com",
        )
        content = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("https://example.com/", content)
        self.assertIn("https://example.com/archive/", content)
        self.assertIn("https://example.com/now/", content)
        self.assertIn("https://example.com/subscribe/", content)
        self.assertIn("https://example.com/live-post/", content)

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_sitemap_excludes_drafts_and_scheduled_posts(self):
        self.make_post(slug="live-post")
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

        response = self.client.get(
            reverse("blog:sitemap"),
            secure=True,
            HTTP_HOST="example.com",
        )
        content = response.content.decode("utf-8")

        self.assertIn("https://example.com/live-post/", content)
        self.assertNotIn("draft-post", content)
        self.assertNotIn("scheduled-post", content)


class PostMediaTests(TestCase):
    def test_media_slug_is_generated_from_title_when_blank(self):
        media = PostMedia.objects.create(
            title="Hero Photo",
            file="blog/media/2026/06/hero.png",
        )

        self.assertEqual(media.slug, "hero-photo")

    def test_media_slug_can_be_changed(self):
        media = PostMedia.objects.create(
            title="Hero Photo",
            slug="custom-hero",
            file="blog/media/2026/06/hero.png",
        )

        media.slug = "renamed-hero"
        media.save()

        self.assertEqual(
            media.markdown_snippet,
            f"![Hero Photo]({media.file.url})",
        )

    def test_image_media_has_markdown_image_snippet(self):
        media = PostMedia(
            title="Hero photo",
            slug="hero-photo",
            alt_text="A bright sky",
            file="blog/media/2026/06/hero.png",
        )

        self.assertTrue(media.is_image)
        self.assertEqual(
            media.markdown_snippet,
            f"![A bright sky]({media.file.url})",
        )

    def test_non_image_media_has_markdown_link_snippet(self):
        media = PostMedia(
            title="Launch notes",
            slug="launch-notes",
            file="blog/media/2026/06/notes.pdf",
        )

        self.assertFalse(media.is_image)
        self.assertEqual(
            media.markdown_snippet,
            f"[Launch notes]({media.file.url})",
        )

    def test_media_detail_redirects_to_uploaded_file(self):
        PostMedia.objects.create(
            title="Hero photo",
            slug="hero-photo",
            file="blog/media/2026/06/hero.png",
        )

        response = self.client.get(reverse("blog:media_detail", args=["hero-photo"]))

        self.assertRedirects(
            response,
            "/media/blog/media/2026/06/hero.png",
            fetch_redirect_response=False,
        )


@override_settings(STORAGES=TEST_STORAGES)
class PostAdminTests(TestCase):
    def test_reserved_slugs_are_rejected_by_admin_validation(self):
        user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("admin:blog_post_add"),
            {
                "title": "About",
                "slug": "about",
                "body": "This slug conflicts with a site route.",
                "description": "",
                "status": Post.DRAFT,
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["adminform"].form,
            "slug",
            "This slug is reserved for a site route.",
        )

    def test_post_editor_shows_recent_media_markdown_snippets(self):
        post = Post.objects.create(
            title="Draft post",
            slug="draft-post",
            body="Draft body",
            status=Post.DRAFT,
        )
        PostMedia.objects.create(
            title="Hero photo",
            slug="hero-photo",
            alt_text="A bright sky",
            file="blog/media/2026/06/hero.png",
        )
        user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin:blog_post_change", args=[post.pk]))

        self.assertContains(response, "Media")
        self.assertContains(response, "Hero photo")
        self.assertContains(
            response,
            "![A bright sky](/media/blog/media/2026/06/hero.png)",
        )

    def test_post_editor_shows_recent_media_newest_first(self):
        post = Post.objects.create(
            title="Draft post",
            slug="draft-post",
            body="Draft body",
            status=Post.DRAFT,
        )
        older_media = PostMedia.objects.create(
            title="Older photo",
            slug="older-photo",
            file="blog/media/2026/06/older.png",
        )
        newer_media = PostMedia.objects.create(
            title="Newer photo",
            slug="newer-photo",
            file="blog/media/2026/06/newer.png",
        )
        PostMedia.objects.filter(pk=older_media.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=1)
        )
        user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin:blog_post_change", args=[post.pk]))
        media = list(response.context["available_media"])

        self.assertEqual(media, [newer_media, older_media])

    def test_post_editor_shows_inline_description_generation_control(self):
        post = Post.objects.create(
            title="Draft post",
            slug="draft-post",
            body="Draft body",
            status=Post.DRAFT,
        )
        user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin:blog_post_change", args=[post.pk]))

        self.assertContains(response, 'name="_generate_description"')
        self.assertContains(response, 'aria-label="Generate description"')

    def test_post_editor_shows_preview_and_publish_now_controls(self):
        post = Post.objects.create(
            title="Draft post",
            slug="draft-post",
            body="Draft body",
            status=Post.DRAFT,
        )
        user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin:blog_post_change", args=[post.pk]))

        self.assertContains(response, 'name="_preview"')
        self.assertContains(
            response,
            f'formaction="{reverse("admin:blog_post_preview", args=[post.pk])}"',
        )
        self.assertContains(response, 'name="_publish_now"')

    def test_post_admin_orders_drafts_then_published_newest_first(self):
        older_published = Post.objects.create(
            title="Older live post",
            slug="older-live-post",
            body="Live body",
            status=Post.PUBLISHED,
            published_at=timezone.now() - timezone.timedelta(days=30),
        )
        newer_published = Post.objects.create(
            title="Newer live post",
            slug="newer-live-post",
            body="Live body",
            status=Post.PUBLISHED,
            published_at=timezone.now(),
        )
        draft = Post.objects.create(
            title="Draft post",
            slug="draft-post",
            body="Draft body",
            status=Post.DRAFT,
            published_at=None,
        )
        user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin:blog_post_changelist"))
        posts = list(response.context["cl"].result_list)

        self.assertEqual(posts, [draft, newer_published, older_published])

    def test_post_admin_exposes_sortable_upvote_count(self):
        post = Post.objects.create(
            title="Popular post",
            slug="popular-post",
            body="Post body",
            status=Post.DRAFT,
            upvotes_count=7,
        )
        model_admin = PostAdmin(Post, admin.site)

        self.assertIn("upvotes", model_admin.list_display)
        self.assertEqual(model_admin.upvotes(post), 7)
        self.assertEqual(model_admin.upvotes.admin_order_field, "upvotes_count")

    def test_admin_can_upload_post_media(self):
        user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(user)

        with tempfile.TemporaryDirectory() as media_root:
            with self.settings(MEDIA_ROOT=media_root):
                response = self.client.post(
                    reverse("admin:blog_postmedia_add"),
                    {
                        "title": "Hero photo",
                        "slug": "custom-hero",
                        "alt_text": "A bright sky",
                        "file": SimpleUploadedFile(
                            "hero.png",
                            b"fake image bytes",
                            content_type="image/png",
                        ),
                        "_save": "Save",
                    },
                )

        self.assertEqual(response.status_code, 302)
        media = PostMedia.objects.get()
        self.assertEqual(media.title, "Hero photo")
        self.assertEqual(media.slug, "custom-hero")
        self.assertTrue(media.file.name.startswith("blog/media/"))
        self.assertEqual(
            media.markdown_snippet,
            f"![A bright sky]({media.file.url})",
        )

    def test_admin_save_sets_publish_date_when_published_without_date(self):
        post = Post.objects.create(
            title="Draft post",
            slug="draft-post",
            body="Draft body",
            status=Post.DRAFT,
            published_at=None,
        )
        user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(user)

        before_save = timezone.now()
        response = self.client.post(
            reverse("admin:blog_post_change", args=[post.pk]),
            {
                "title": "Draft post",
                "slug": "draft-post",
                "body": "Draft body",
                "description": "",
                "upvotes_count": "0",
                "status": Post.PUBLISHED,
                "published_at_0": "",
                "published_at_1": "",
                "_save": "Save",
            },
        )
        after_save = timezone.now()

        self.assertEqual(response.status_code, 302)
        post.refresh_from_db()
        self.assertEqual(post.status, Post.PUBLISHED)
        self.assertGreaterEqual(post.published_at, before_save)
        self.assertLessEqual(post.published_at, after_save)

    @patch("blog.admin.send_webmentions_for_post_async")
    def test_admin_publish_now_saves_as_published_with_current_date(
        self,
        send_webmentions,
    ):
        post = Post.objects.create(
            title="Draft post",
            slug="draft-post",
            body="Draft body",
            status=Post.DRAFT,
            published_at=None,
        )
        user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(user)

        before_save = timezone.now()
        response = self.client.post(
            reverse("admin:blog_post_change", args=[post.pk]),
            {
                "title": "Draft post",
                "slug": "draft-post",
                "body": "Updated body",
                "description": "",
                "upvotes_count": "0",
                "status": Post.DRAFT,
                "published_at_0": "",
                "published_at_1": "",
                "_publish_now": "Publish Now",
            },
        )
        after_save = timezone.now()

        self.assertRedirects(
            response,
            reverse("admin:blog_post_change", args=[post.pk]),
        )
        post.refresh_from_db()
        self.assertEqual(post.body, "Updated body")
        self.assertEqual(post.status, Post.PUBLISHED)
        self.assertGreaterEqual(post.published_at, before_save)
        self.assertLessEqual(post.published_at, after_save)
        send_webmentions.assert_called_once_with(post)

    @patch("blog.admin.send_webmentions_for_post_async")
    def test_admin_preview_renders_form_values_without_saving_or_webmentions(
        self,
        send_webmentions,
    ):
        post = Post.objects.create(
            title="Draft post",
            slug="draft-post",
            body="Original body",
            status=Post.DRAFT,
            published_at=None,
        )
        user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("admin:blog_post_preview", args=[post.pk]),
            {
                "title": "Preview title",
                "slug": "preview-title",
                "body": "Updated **preview** body",
                "description": "",
                "upvotes_count": "0",
                "status": Post.PUBLISHED,
                "published_at_0": "",
                "published_at_1": "",
                "_preview": "Preview",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Preview title")
        self.assertContains(response, "<strong>preview</strong>")
        self.assertContains(response, "Preview only.")
        self.assertContains(response, 'content="noindex,nofollow"')
        self.assertNotContains(response, 'rel="webmention"')
        self.assertNotContains(response, "analytics.antoniosantos.io")
        self.assertNotContains(response, 'id="comment-actions"')
        post.refresh_from_db()
        self.assertEqual(post.title, "Draft post")
        self.assertEqual(post.body, "Original body")
        self.assertEqual(post.status, Post.DRAFT)
        self.assertIsNone(post.published_at)
        send_webmentions.assert_not_called()

    @patch("blog.admin.send_webmentions_for_post_async")
    def test_admin_save_schedules_webmentions_for_published_post(
        self,
        send_webmentions,
    ):
        post = Post(
            title="Live post",
            slug="live-post",
            body="[Elsewhere](https://example.com/post/)",
            status=Post.PUBLISHED,
            published_at=timezone.now(),
        )
        model_admin = PostAdmin(Post, admin.site)

        model_admin.save_model(None, post, None, False)

        send_webmentions.assert_called_once_with(post)

    @patch("blog.admin.send_webmentions_for_post_async")
    def test_admin_save_does_not_schedule_webmentions_for_draft(
        self,
        send_webmentions,
    ):
        post = Post(
            title="Draft post",
            slug="draft-post",
            body="[Elsewhere](https://example.com/post/)",
            status=Post.DRAFT,
        )
        model_admin = PostAdmin(Post, admin.site)

        model_admin.save_model(None, post, None, False)

        send_webmentions.assert_not_called()

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

    @patch("blog.admin.send_webmentions_for_post_async")
    def test_publish_posts_action_schedules_webmentions(self, send_webmentions):
        post = Post.objects.create(
            title="Draft post",
            slug="draft-post",
            body="[Elsewhere](https://example.com/post/)",
            status=Post.DRAFT,
            published_at=None,
        )
        model_admin = PostAdmin(Post, admin.site)

        model_admin.publish_posts(None, Post.objects.filter(pk=post.pk))

        post.refresh_from_db()
        send_webmentions.assert_called_once()
        self.assertEqual(send_webmentions.call_args.args[0], post)

    def test_publish_posts_action_preserves_existing_publish_dates(self):
        backdated_at = timezone.now() - timezone.timedelta(days=30)
        scheduled_at = timezone.now() + timezone.timedelta(days=1)
        backdated_post = Post.objects.create(
            title="Backdated post",
            slug="backdated-post",
            body="Backdated body",
            status=Post.DRAFT,
            published_at=backdated_at,
        )
        scheduled_post = Post.objects.create(
            title="Scheduled post",
            slug="scheduled-post",
            body="Scheduled body",
            status=Post.DRAFT,
            published_at=scheduled_at,
        )
        model_admin = PostAdmin(Post, admin.site)

        model_admin.publish_posts(
            None,
            Post.objects.filter(pk__in=[backdated_post.pk, scheduled_post.pk]),
        )

        backdated_post.refresh_from_db()
        scheduled_post.refresh_from_db()
        self.assertEqual(backdated_post.status, Post.PUBLISHED)
        self.assertEqual(backdated_post.published_at, backdated_at)
        self.assertEqual(scheduled_post.status, Post.PUBLISHED)
        self.assertEqual(scheduled_post.published_at, scheduled_at)

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

    @override_settings(OPENROUTER_API_KEY="test-key")
    @patch("blog.admin.generate_post_description", return_value="Generated description")
    def test_generate_descriptions_action_updates_selected_posts(self, mock_generate):
        post = Post.objects.create(
            title="Draft post",
            slug="draft-post",
            body="Draft body",
            status=Post.DRAFT,
            published_at=None,
        )
        model_admin = PostAdmin(Post, admin.site)

        with patch.object(model_admin, "message_user"):
            model_admin.generate_descriptions(None, Post.objects.filter(pk=post.pk))

        post.refresh_from_db()
        self.assertEqual(post.description, "Generated description")
        mock_generate.assert_called_once_with(post)

    @override_settings(OPENROUTER_API_KEY="test-key")
    @patch("blog.admin.generate_post_description", return_value="Generated description")
    def test_generate_description_admin_view_updates_post(self, mock_generate):
        post = Post.objects.create(
            title="Draft post",
            slug="draft-post",
            body="Draft body",
            status=Post.DRAFT,
            published_at=None,
        )
        user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("admin:blog_post_generate_description", args=[post.pk])
        )

        self.assertRedirects(
            response,
            reverse("admin:blog_post_change", args=[post.pk]),
        )
        post.refresh_from_db()
        self.assertEqual(post.description, "Generated description")
        mock_generate.assert_called_once_with(post)

    @override_settings(OPENROUTER_API_KEY="test-key")
    @patch("blog.admin.generate_post_description")
    def test_generate_description_submit_saves_draft_changes_first(self, mock_generate):
        post = Post.objects.create(
            title="Draft post",
            slug="draft-post",
            body="Old draft body",
            status=Post.DRAFT,
            published_at=None,
        )
        user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(user)

        def generate(post_to_describe):
            self.assertEqual(post_to_describe.body, "Updated draft body")
            return "Generated from updated body"

        mock_generate.side_effect = generate
        response = self.client.post(
            reverse("admin:blog_post_change", args=[post.pk]),
            {
                "title": "Draft post",
                "slug": "draft-post",
                "body": "Updated draft body",
                "description": "",
                "upvotes_count": "0",
                "status": Post.DRAFT,
                "published_at_0": "",
                "published_at_1": "",
                "_generate_description": "1",
            },
        )

        self.assertRedirects(
            response,
            reverse("admin:blog_post_change", args=[post.pk]),
        )
        post.refresh_from_db()
        self.assertEqual(post.body, "Updated draft body")
        self.assertEqual(post.description, "Generated from updated body")

    @override_settings(OPENROUTER_API_KEY="test-key")
    @patch("blog.admin.generate_post_description", return_value="Generated description")
    def test_generate_description_submit_can_create_draft(self, mock_generate):
        user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("admin:blog_post_add"),
            {
                "title": "Draft post",
                "slug": "draft-post",
                "body": "Draft body",
                "description": "",
                "upvotes_count": "0",
                "status": Post.DRAFT,
                "published_at_0": "",
                "published_at_1": "",
                "_generate_description": "1",
            },
        )

        post = Post.objects.get()
        self.assertRedirects(
            response,
            reverse("admin:blog_post_change", args=[post.pk]),
        )
        self.assertEqual(post.description, "Generated description")
        mock_generate.assert_called_once_with(post)

    def test_admin_index_links_to_moderation_queue_with_pending_count(self):
        post = Post.objects.create(
            title="Live post",
            slug="live-post",
            body="Live body",
            status=Post.PUBLISHED,
            published_at=timezone.now(),
        )
        Comment.objects.create(
            post=post,
            author_name="Reader",
            author_email="reader@example.com",
            body="Please approve this.",
        )
        Webmention.objects.create(
            source_url="https://source.example/reply/",
            target_url="https://example.com/live-post/",
        )
        user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin:index"))

        self.assertContains(response, "Approval queue")
        self.assertContains(response, reverse("admin:moderation_queue"))
        self.assertContains(response, "2 pending")

    def test_moderation_queue_lists_pending_comments_and_webmentions(self):
        post = Post.objects.create(
            title="Live post",
            slug="live-post",
            body="Live body",
            status=Post.PUBLISHED,
            published_at=timezone.now(),
        )
        Comment.objects.create(
            post=post,
            author_name="Reader",
            author_email="reader@example.com",
            body="Pending comment",
        )
        Comment.objects.create(
            post=post,
            author_name="Approved reader",
            author_email="approved@example.com",
            body="Approved comment",
            status=Comment.APPROVED,
        )
        Webmention.objects.create(
            source_url="https://source.example/reply/",
            target_url="https://example.com/live-post/",
            author_name="Mentioner",
            content="Pending mention",
        )
        Webmention.objects.create(
            source_url="https://source.example/approved/",
            target_url="https://example.com/live-post/",
            content="Approved mention",
            status=Webmention.APPROVED,
        )
        user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin:moderation_queue"))

        self.assertContains(response, "Pending comment")
        self.assertContains(response, "Pending mention")
        self.assertContains(response, "1 comment and")
        self.assertContains(response, "1 webmention")
        self.assertNotContains(response, "Approved comment")
        self.assertNotContains(response, "Approved mention")

    def test_moderation_queue_can_approve_pending_comment(self):
        post = Post.objects.create(
            title="Live post",
            slug="live-post",
            body="Live body",
            status=Post.PUBLISHED,
            published_at=timezone.now(),
        )
        comment = Comment.objects.create(
            post=post,
            author_name="Reader",
            author_email="reader@example.com",
            body="Please approve this.",
        )
        user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("admin:moderation_queue"),
            {
                "item_type": "comment",
                "item_id": str(comment.pk),
                "action": "approve",
            },
        )

        self.assertRedirects(response, reverse("admin:moderation_queue"))
        comment.refresh_from_db()
        self.assertEqual(comment.status, Comment.APPROVED)
        self.assertIsNotNone(comment.approved_at)

    def test_moderation_queue_can_reject_pending_webmention(self):
        webmention = Webmention.objects.create(
            source_url="https://source.example/reply/",
            target_url="https://example.com/live-post/",
            content="Please reject this.",
        )
        user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("admin:moderation_queue"),
            {
                "item_type": "webmention",
                "item_id": str(webmention.pk),
                "action": "reject",
            },
        )

        self.assertRedirects(response, reverse("admin:moderation_queue"))
        webmention.refresh_from_db()
        self.assertEqual(webmention.status, Webmention.REJECTED)


class DescriptionGenerationTests(TestCase):
    def make_post(self, **kwargs):
        defaults = {
            "title": "A small note",
            "slug": "a-small-note",
            "body": "This is the body of the post.",
        }
        defaults.update(kwargs)
        return Post(**defaults)

    @override_settings(
        OPENROUTER_API_KEY="test-key",
        OPENROUTER_MODEL="test/model",
        POST_DESCRIPTION_TARGET_CHARS=155,
    )
    @patch("blog.llm.urllib.request.urlopen")
    def test_generate_post_description_calls_openrouter(self, mock_urlopen):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def read(self):
                return b'{"choices":[{"message":{"content":"A clear summary."}}]}'

        mock_urlopen.return_value = FakeResponse()

        description = llm.generate_post_description(self.make_post())

        self.assertEqual(description, "A clear summary.")
        request = mock_urlopen.call_args.args[0]
        payload = json_from_request(request)
        self.assertEqual(payload["model"], "test/model")
        self.assertIn("A small note", payload["messages"][1]["content"])
        self.assertIn("This is the body", payload["messages"][1]["content"])
        self.assertIn("Write in my voice", payload["messages"][0]["content"])
        self.assertIn("never in third person", payload["messages"][0]["content"])
        self.assertIn("Do not refer to me as the author", payload["messages"][1]["content"])
        self.assertIn("use first person", payload["messages"][1]["content"])

    @override_settings(OPENROUTER_API_KEY="")
    def test_generate_post_description_requires_api_key(self):
        with self.assertRaises(llm.DescriptionGenerationError):
            llm.generate_post_description(self.make_post())

    @override_settings(POST_DESCRIPTION_TARGET_CHARS=20)
    def test_truncate_description_keeps_result_under_limit(self):
        description = llm._truncate_description(
            "This description is intentionally too long.",
            20,
        )

        self.assertLessEqual(len(description), 20)


def json_from_request(request):
    import json

    return json.loads(request.data.decode("utf-8"))
