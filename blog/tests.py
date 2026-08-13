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
from xml.etree import ElementTree

from birdex.models import Bird, Sighting, SightingPhoto

from . import llm
from .feeds import LEGACY_RSS_GUID_SLUGS
from .models import Note, Post, PostMedia, Tag
from webmentions.models import Webmention


PHOTO_LICENSE_URL = "https://creativecommons.org/licenses/by-nc-nd/4.0/"


TEST_STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
    "birdex": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
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
        for slug in ("about", "notes"):
            with self.subTest(slug=slug):
                post = Post(
                    title=slug.title(),
                    slug=slug,
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

    def test_published_note_gets_a_timestamp_slug_without_a_title(self):
        published_at = timezone.datetime(
            2026, 7, 14, 9, 30, tzinfo=timezone.get_current_timezone()
        )
        note = Note.objects.create(
            body="A quick note.",
            status=Note.PUBLISHED,
            published_at=published_at,
        )

        self.assertEqual(note.slug, "note-2026-07-14-0930")
        self.assertEqual(note.display_title, "July 14, 2026 at 9:30 AM")

    def test_posts_can_have_tags(self):
        post = self.make_post()
        django = Tag.objects.create(name="Django", slug="django")
        python = Tag.objects.create(name="Python", slug="python")

        post.tags.add(django, python)

        self.assertCountEqual(post.tags.all(), [django, python])
        self.assertCountEqual(django.posts.all(), [post])


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

        self.assertContains(response, 'class="h-feed home-section"')
        self.assertContains(response, 'class="p-name section-heading"')
        self.assertContains(response, 'class="h-entry post-list-item"')
        self.assertContains(response, 'class="p-name u-url"')
        self.assertContains(response, 'class="dt-published"')
        self.assertContains(response, 'class="p-summary"')

    def test_home_renders_notes_as_timestamped_content(self):
        note = Note.objects.create(
            slug="note-2026-07-14-0930",
            body="A **quick** note.",
            status=Note.PUBLISHED,
            published_at=timezone.now(),
        )

        response = self.client.get(reverse("blog:home"))

        self.assertContains(response, "Recent notes")
        self.assertContains(response, 'class="h-entry home-note"')
        self.assertContains(response, "A quick note.")
        self.assertNotContains(response, "note-list-item")
        self.assertContains(response, note.get_absolute_url())

    def test_home_renders_recent_bird_sightings_with_a_photo(self):
        bird = Bird.objects.create(
            common_name="Puerto Rican Tody",
            scientific_name="Todus mexicanus",
            slug="puerto-rican-tody",
        )
        sighting = Sighting.objects.create(
            bird=bird,
            date="2026-08-12",
            location="Cabo Rojo",
            status=Sighting.Status.PUBLISHED,
        )
        photo = SightingPhoto.objects.create(
            sighting=sighting,
            image="birdex/photos/tody.jpg",
            is_featured=True,
        )

        response = self.client.get(reverse("blog:home"))

        self.assertContains(response, "Recent bird sightings")
        self.assertContains(response, bird.common_name)
        self.assertContains(response, "Cabo Rojo")
        self.assertContains(response, sighting.get_absolute_url())
        self.assertContains(response, photo.image.url)
        self.assertNotContains(response, f'href="{photo.image.url}"')
        self.assertContains(response, "1 photo")
        self.assertContains(response, PHOTO_LICENSE_URL)

    def test_home_excludes_scheduled_bird_sightings(self):
        bird = Bird.objects.create(
            common_name="Bananaquit",
            scientific_name="Coereba flaveola",
            slug="bananaquit",
        )
        Sighting.objects.create(
            bird=bird,
            date="2026-08-12",
            status=Sighting.Status.PUBLISHED,
            published_at=timezone.now() + timezone.timedelta(days=1),
        )

        response = self.client.get(reverse("blog:home"))

        self.assertNotContains(response, bird.common_name)

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
        Note.objects.create(
            body="A note that belongs in the notes index.",
            status=Note.PUBLISHED,
            published_at=timezone.now(),
        )

        response = self.client.get(reverse("blog:archive"))

        self.assertContains(response, "Live post")
        self.assertContains(response, "Older post")
        self.assertNotContains(response, "Draft post")
        self.assertNotContains(response, "Scheduled post")
        self.assertNotContains(response, "A note that belongs in the notes index.")

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

    def test_notes_index_lists_only_published_notes(self):
        live_note = Note.objects.create(
            body="A live note.",
            status=Note.PUBLISHED,
            published_at=timezone.now(),
        )
        Note.objects.create(
            body="A draft note.",
            status=Note.DRAFT,
        )
        Note.objects.create(
            body="A scheduled note.",
            status=Note.PUBLISHED,
            published_at=timezone.now() + timezone.timedelta(days=1),
        )

        response = self.client.get(reverse("blog:notes"))

        self.assertContains(response, "A live note.")
        self.assertContains(response, live_note.get_absolute_url())
        self.assertContains(response, 'class="h-entry home-note"')
        self.assertContains(response, 'class="dt-published home-note-date"')
        self.assertNotContains(response, "A draft note.")
        self.assertNotContains(response, "A scheduled note.")

    def test_post_detail_renders_live_post_markdown(self):
        self.make_post()

        response = self.client.get(
            reverse("blog:post_detail", args=["live-post"])
        )

        self.assertContains(response, "Live post")
        self.assertContains(response, "<strong>world</strong>")

    def test_note_detail_uses_timestamp_in_place_of_a_title(self):
        note = Note.objects.create(
            slug="note-2026-07-14-0930",
            body="A quick note.",
            status=Note.PUBLISHED,
            published_at=timezone.now().replace(month=7, day=14, hour=9, minute=30),
        )

        response = self.client.get(reverse("blog:note_detail", args=[note.slug]))

        self.assertContains(response, "note-entry")
        self.assertContains(response, "July")
        self.assertNotContains(response, 'class="p-name post-title"')

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
        self.assertNotContains(response, 'class="u-url text-blue-200 hover:text-white"')
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

    def test_post_detail_shows_anonymous_upvote_button(self):
        post = self.make_post(upvotes_count=2)

        response = self.client.get(reverse("blog:post_detail", args=[post.slug]))

        self.assertContains(response, 'id="feedback-actions"')
        self.assertContains(response, 'name="action" value="upvote"')
        self.assertContains(response, 'data-upvote-button')
        self.assertContains(response, 'data-upvote-count')
        self.assertContains(
            response,
            'href="https://letterbird.co/antoniosantos">Reply via email</a>',
        )
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

        self.assertRedirects(response, f"{post.get_absolute_url()}#feedback-actions")
        post.refresh_from_db()
        self.assertEqual(post.upvotes_count, 3)
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
        self.assertNotIn(f"post_{post.pk}_upvoted", response.cookies)


@override_settings(STORAGES=TEST_STORAGES)
class SubscribeViewTests(TestCase):
    def test_subscribe_page_shows_both_rss_options(self):
        response = self.client.get(reverse("blog:subscribe"))

        self.assertContains(response, "Posts and notes")
        self.assertContains(response, "Posts only")
        self.assertContains(response, reverse("blog:rss"))
        self.assertContains(response, reverse("blog:posts_rss"))
        self.assertNotContains(response, 'type="email"')

    def test_subscribe_page_does_not_accept_email_signups(self):
        response = self.client.post(
            reverse("blog:subscribe"),
            {"email": "reader@example.com"},
        )

        self.assertEqual(response.status_code, 405)


@override_settings(
    ALLOWED_HOSTS=["example.com"],
    SITE_URL="https://example.com",
    STORAGES=TEST_STORAGES,
)
class ContentRightsTests(TestCase):
    def test_robots_txt_disallows_known_ai_training_crawlers(self):
        response = self.client.get(reverse("robots_txt"), HTTP_HOST="example.com")
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
        self.assertIn(
            "Content-Signal: search=yes, ai-train=no, use=reference",
            content,
        )
        for crawler in (
            "Amazonbot",
            "Applebot-Extended",
            "Bytespider",
            "CCBot",
            "ClaudeBot",
            "Google-Extended",
            "GPTBot",
            "meta-externalagent",
        ):
            with self.subTest(crawler=crawler):
                self.assertIn(f"User-agent: {crawler}\nDisallow: /", content)
        self.assertIn("Sitemap: https://example.com/sitemap.xml", content)

    def test_pages_send_machine_readable_rights_reservations(self):
        response = self.client.get(reverse("blog:home"), HTTP_HOST="example.com")

        self.assertEqual(
            response["Content-Signal"],
            "search=yes, ai-train=no, use=reference",
        )
        self.assertEqual(response["TDM-Reservation"], "1")
        self.assertContains(
            response,
            '<meta name="tdm-reservation" content="1">',
            html=True,
        )


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
        self.assertContains(response, "Hello")
        self.assertNotContains(response, "A live post")
        self.assertNotContains(response, "Draft post")
        self.assertNotContains(response, "Scheduled post")

    def test_rss_feeds_include_full_post_content(self):
        self.make_post(
            body="First paragraph.\n\nSecond paragraph with **formatting**.",
            description="Short summary",
        )

        for feed_name in ("blog:rss", "blog:posts_rss"):
            with self.subTest(feed_name=feed_name):
                response = self.client.get(reverse(feed_name))
                feed = ElementTree.fromstring(response.content)
                description = feed.findtext("./channel/item/description")

                self.assertEqual(
                    description,
                    "<p>First paragraph.</p>\n<p>Second paragraph with "
                    "<strong>formatting</strong>.</p>",
                )
                self.assertNotContains(response, "Short summary")

    def test_posts_only_rss_includes_all_published_posts(self):
        for index in range(21):
            self.make_post(
                title=f"Post {index}",
                slug=f"post-{index}",
                published_at=timezone.now() - timezone.timedelta(minutes=index),
            )

        response = self.client.get(reverse("blog:posts_rss"))

        self.assertContains(response, "Post 20")

    @override_settings(ALLOWED_HOSTS=["example.com"], SITE_URL="https://example.com")
    def test_rss_includes_published_notes(self):
        note = Note.objects.create(
            body="A quick note.",
            status=Note.PUBLISHED,
            published_at=timezone.now(),
        )

        response = self.client.get(reverse("blog:rss"), HTTP_HOST="example.com")

        self.assertContains(response, "A quick note.")
        self.assertContains(response, f"https://example.com{note.get_absolute_url()}")

    @override_settings(
        ALLOWED_HOSTS=["example.com"],
        SITE_URL="https://example.com",
        STORAGES=TEST_STORAGES,
    )
    def test_combined_rss_includes_full_sighting_content_and_all_photos(self):
        bird = Bird.objects.create(
            common_name="Puerto Rican Tody",
            scientific_name="Todus mexicanus",
            slug="puerto-rican-tody",
        )
        sighting = Sighting.objects.create(
            bird=bird,
            date="2026-08-12",
            location="Cabo Rojo",
            notes="Seen near the trail.",
            status=Sighting.Status.PUBLISHED,
        )
        photo = SightingPhoto.objects.create(
            sighting=sighting,
            image="birdex/photos/tody.jpg",
            is_featured=True,
        )
        second_photo = SightingPhoto.objects.create(
            sighting=sighting,
            image="birdex/photos/tody-flight.jpg",
            caption="Tody in flight",
        )

        response = self.client.get(reverse("blog:rss"), HTTP_HOST="example.com")
        feed = ElementTree.fromstring(response.content)
        item = feed.find("./channel/item")

        self.assertEqual(item.findtext("title"), "Puerto Rican Tody sighting")
        self.assertEqual(
            item.findtext("link"),
            f"https://example.com{sighting.get_absolute_url()}",
        )
        description = item.findtext("description")
        self.assertIn(photo.image.url, description)
        self.assertIn(second_photo.image.url, description)
        self.assertIn(
            f'<a href="https://example.com{sighting.get_absolute_url()}">',
            description,
        )
        self.assertNotIn(f'<a href="{photo.image.url}">', description)
        self.assertIn("Cabo Rojo", description)
        self.assertIn("Seen near the trail.", description)
        self.assertIn("<strong>Photos:</strong> 2", description)
        self.assertIn(PHOTO_LICENSE_URL, description)
        self.assertIn("View the complete sighting in Birdex", description)

        full_content = item.findtext(
            "{http://purl.org/rss/1.0/modules/content/}encoded"
        )
        self.assertEqual(full_content, description)
        media = item.find("{http://search.yahoo.com/mrss/}content")
        self.assertEqual(media.attrib["url"], photo.image.url)
        self.assertEqual(media.attrib["medium"], "image")

    @override_settings(ALLOWED_HOSTS=["example.com"], SITE_URL="https://example.com")
    def test_combined_rss_excludes_draft_sightings(self):
        bird = Bird.objects.create(
            common_name="Puerto Rican Tody",
            scientific_name="Todus mexicanus",
            slug="puerto-rican-tody",
        )
        Sighting.objects.create(
            bird=bird,
            date="2026-08-12",
            location="Still adding photos",
        )

        response = self.client.get(reverse("blog:rss"), HTTP_HOST="example.com")

        self.assertNotContains(response, "Puerto Rican Tody sighting")
        self.assertNotContains(response, "Still adding photos")

    @override_settings(STORAGES=TEST_STORAGES)
    def test_posts_only_rss_excludes_sightings(self):
        self.make_post()
        bird = Bird.objects.create(
            common_name="Puerto Rican Tody",
            scientific_name="Todus mexicanus",
            slug="puerto-rican-tody",
        )
        Sighting.objects.create(bird=bird, date="2026-08-12")

        response = self.client.get(reverse("blog:posts_rss"))

        self.assertContains(response, "Live post")
        self.assertNotContains(response, "Puerto Rican Tody sighting")

    @override_settings(ALLOWED_HOSTS=["example.com"], SITE_URL="https://example.com")
    def test_posts_only_rss_excludes_notes(self):
        self.make_post()
        Note.objects.create(
            body="A quick note.",
            status=Note.PUBLISHED,
            published_at=timezone.now(),
        )

        response = self.client.get(reverse("blog:posts_rss"), HTTP_HOST="example.com")

        self.assertContains(response, "Live post")
        self.assertNotContains(response, "A quick note.")
        self.assertContains(response, "https://example.com/posts.rss.xml")

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
        self.assertIn("https://example.com/notes/", content)
        self.assertIn("https://example.com/now/", content)
        self.assertIn("https://example.com/subscribe.html", content)
        self.assertIn("https://example.com/live-post/", content)

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_sitemap_includes_published_notes(self):
        note = Note.objects.create(
            body="A quick note.",
            status=Note.PUBLISHED,
            published_at=timezone.now(),
        )

        response = self.client.get(
            reverse("blog:sitemap"), secure=True, HTTP_HOST="example.com"
        )

        self.assertContains(response, note.get_absolute_url())

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
    @override_settings(STORAGES=TEST_STORAGES)
    def test_uploaded_media_uses_generated_filename(self):
        with tempfile.TemporaryDirectory() as media_root, self.settings(
            MEDIA_ROOT=media_root
        ):
            media = PostMedia.objects.create(
                title="Puerto Rican tody",
                file=SimpleUploadedFile(
                    "private-location_DSC-0042.JPG",
                    b"image contents",
                    content_type="image/jpeg",
                ),
            )

        self.assertRegex(
            media.file.name,
            r"^blog/media/\d{4}/\d{2}/[0-9a-f]{32}\.jpg$",
        )
        self.assertNotIn("private-location", media.file.name)
        self.assertNotIn("DSC-0042", media.file.name)

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
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(self.user)

    def test_post_and_note_forms_use_standard_django_admin_fields(self):
        for url, fields in [
            (
                reverse("admin:blog_post_add"),
                ("title", "body", "status", "published_at_0", "published_at_1"),
            ),
            (reverse("admin:blog_note_add"), ("body", "status", "published_at_0", "published_at_1")),
        ]:
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assertEqual(response.status_code, 200)
                for field in fields:
                    self.assertContains(response, f'name="{field}"')

    def test_post_form_includes_searchable_media_library(self):
        media = PostMedia.objects.create(
            title="Puerto Rican tody",
            alt_text="A green tody perched on a thin branch",
            file="blog/media/2026/08/tody.jpg",
        )

        response = self.client.get(reverse("admin:blog_post_add"))

        self.assertContains(response, "Photo library")
        self.assertContains(response, "Upload photos")
        self.assertContains(response, media.title)
        self.assertContains(response, media.alt_text)
        self.assertContains(response, "data-media-workbench")
        self.assertContains(response, '<details class="post-media"')
        self.assertContains(response, '<span data-media-count>1</span>')

    def test_composer_can_upload_multiple_photos(self):
        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            response = self.client.post(
                reverse("admin:blog_postmedia_composer_upload"),
                {
                    "files": [
                        SimpleUploadedFile(
                            "Puerto_Rican_Tody.jpg",
                            b"first image",
                            content_type="image/jpeg",
                        ),
                        SimpleUploadedFile(
                            "bananaquit.png",
                            b"second image",
                            content_type="image/png",
                        ),
                    ]
                },
            )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(len(payload["media"]), 2)
        self.assertEqual(payload["media"][0]["title"], "Puerto Rican Tody")
        self.assertTrue(payload["media"][0]["is_image"])
        self.assertRegex(
            payload["media"][0]["filename"],
            r"^[0-9a-f]{32}\.jpg$",
        )
        self.assertEqual(PostMedia.objects.count(), 2)

    def test_composer_rejects_unsupported_image_formats(self):
        response = self.client.post(
            reverse("admin:blog_postmedia_composer_upload"),
            {
                "files": SimpleUploadedFile(
                    "unsafe.svg",
                    b"<svg></svg>",
                    content_type="image/svg+xml",
                )
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("not a supported image", response.json()["error"])
        self.assertFalse(PostMedia.objects.exists())

    def test_composer_can_update_photo_alt_text(self):
        media = PostMedia.objects.create(
            title="Tody",
            file="blog/media/2026/08/tody.jpg",
        )

        response = self.client.post(
            reverse("admin:blog_postmedia_composer_metadata", args=[media.pk]),
            {
                "title": "Puerto Rican tody",
                "alt_text": "A small green tody facing left on a mossy branch",
            },
        )

        self.assertEqual(response.status_code, 200)
        media.refresh_from_db()
        self.assertEqual(media.title, "Puerto Rican tody")
        self.assertEqual(
            media.alt_text,
            "A small green tody facing left on a mossy branch",
        )
        self.assertIn(media.alt_text, response.json()["media"]["markdown"])

    def test_media_list_highlights_photos_missing_alt_text(self):
        PostMedia.objects.create(
            title="Tody",
            file="blog/media/2026/08/tody.jpg",
        )
        PostMedia.objects.create(
            title="Bananaquit",
            alt_text="A bananaquit drinking nectar from a red flower",
            file="blog/media/2026/08/bananaquit.jpg",
        )

        response = self.client.get(reverse("admin:blog_postmedia_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Needs alt text")
        self.assertContains(response, "A bananaquit drinking nectar")
        self.assertContains(response, "media-admin-preview")

    @patch("blog.admin.send_webmentions_for_post_async")
    def test_publish_action_publishes_post_and_sends_webmentions(self, send):
        post = Post.objects.create(
            title="Draft post",
            slug="draft-post",
            body="Draft body",
            status=Post.DRAFT,
        )

        response = self.client.post(
            reverse("admin:blog_post_changelist"),
            {
                "action": "publish",
                "_selected_action": post.pk,
                "index": "0",
            },
        )

        self.assertRedirects(response, reverse("admin:blog_post_changelist"))
        post.refresh_from_db()
        self.assertEqual(post.status, Post.PUBLISHED)
        self.assertIsNotNone(post.published_at)
        send.assert_called_once_with(post)

    @override_settings(OPENROUTER_API_KEY="test-key")
    @patch("blog.admin.generate_post_description", return_value="Generated description")
    def test_generate_description_action_updates_selected_post(self, generate):
        post = Post.objects.create(
            title="Post",
            slug="post",
            body="Body",
            status=Post.DRAFT,
        )

        response = self.client.post(
            reverse("admin:blog_post_changelist"),
            {
                "action": "generate_descriptions",
                "_selected_action": post.pk,
                "index": "0",
            },
        )

        self.assertRedirects(response, reverse("admin:blog_post_changelist"))
        post.refresh_from_db()
        self.assertEqual(post.description, "Generated description")
        generate.assert_called_once_with(post)

    def test_webmentions_are_moderated_from_the_standard_admin_list(self):
        mention = Webmention.objects.create(
            source_url="https://source.example/reply/",
            target_url="https://example.com/live-post/",
        )

        response = self.client.get(reverse("admin:webmentions_webmention_changelist"))

        self.assertContains(response, mention.source_url)
        self.assertContains(response, "Approve selected webmentions")


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
