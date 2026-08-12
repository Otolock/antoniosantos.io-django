import re
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Bird, Sighting, SightingPhoto, birdex_photo_upload_to


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


@override_settings(STORAGES=TEST_STORAGES)
class BirdexHomeTests(TestCase):
    def test_home_uses_birdex_template_and_lists_birds(self):
        bird = Bird.objects.create(
            common_name="Puerto Rican Tody",
            scientific_name="Todus mexicanus",
            slug="puerto-rican-tody",
            endemic=True,
        )

        response = self.client.get(reverse("birdex:home"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "birdex/index.html")
        self.assertContains(response, "Birdex")
        self.assertContains(response, bird.common_name)
        self.assertContains(response, bird.scientific_name)
        self.assertContains(response, f'href="{bird.get_absolute_url()}"')
        self.assertIn(bird, response.context["birds"])

    def test_home_shows_a_birds_featured_photo(self):
        bird = Bird.objects.create(
            common_name="Puerto Rican Tody",
            scientific_name="Todus mexicanus",
            slug="puerto-rican-tody",
        )
        sighting = Sighting.objects.create(bird=bird, date="2026-08-12")
        photo = SightingPhoto.objects.create(
            sighting=sighting,
            image="birdex/photos/tody.jpg",
            is_featured=True,
        )

        response = self.client.get(reverse("birdex:home"))

        self.assertContains(response, "birdex/photos/tody.jpg")
        self.assertContains(response, 'alt="Puerto Rican Tody"')
        self.assertContains(response, f'href="{bird.get_absolute_url()}"')
        self.assertNotContains(response, f'href="{photo.image.url}"')
        self.assertContains(response, PHOTO_LICENSE_URL)

    def test_home_uses_the_first_photo_when_none_is_featured(self):
        bird = Bird.objects.create(
            common_name="Bananaquit",
            scientific_name="Coereba flaveola",
            slug="bananaquit",
        )
        sighting = Sighting.objects.create(bird=bird, date="2026-08-12")
        SightingPhoto.objects.create(
            sighting=sighting,
            image="birdex/photos/bananaquit.jpg",
            is_featured=False,
        )

        response = self.client.get(reverse("birdex:home"))

        self.assertContains(response, "bananaquit.jpg")


@override_settings(STORAGES=TEST_STORAGES)
class BirdDetailTests(TestCase):
    def setUp(self):
        self.bird = Bird.objects.create(
            common_name="Puerto Rican Tody",
            scientific_name="Todus mexicanus",
            slug="puerto-rican-tody",
            description="A tiny, bright green bird.",
            endemic=True,
            ebird_url="https://ebird.org/species/purtod1",
        )

    def test_bird_has_a_stable_public_url(self):
        self.assertEqual(
            self.bird.get_absolute_url(),
            "/birdex/puerto-rican-tody/",
        )

    def test_detail_shows_species_information_and_published_sightings(self):
        published = Sighting.objects.create(
            bird=self.bird,
            date="2026-08-12",
            location="Cabo Rojo",
            confidence=Sighting.Confidence.HIGH,
        )
        photo = SightingPhoto.objects.create(
            sighting=published,
            image="birdex/photos/tody.jpg",
            caption="Tody on a branch",
            is_featured=True,
        )
        scheduled = Sighting.objects.create(
            bird=self.bird,
            date="2026-08-13",
            location="Hidden location",
            published_at=timezone.now() + timezone.timedelta(days=1),
        )

        response = self.client.get(self.bird.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "birdex/bird_detail.html")
        self.assertContains(response, self.bird.common_name)
        self.assertContains(response, self.bird.scientific_name)
        self.assertContains(response, self.bird.description)
        self.assertContains(response, "Endemic")
        self.assertContains(response, "Learn more on eBird")
        self.assertContains(response, f'href="{self.bird.ebird_url}"')
        self.assertContains(response, 'rel="external noopener"')
        self.assertContains(response, 'target="_blank"')
        self.assertContains(response, published.get_absolute_url())
        self.assertContains(response, photo.image.url)
        self.assertContains(response, "1 photo")
        self.assertContains(response, PHOTO_LICENSE_URL, count=2)
        self.assertNotContains(response, f'href="{photo.image.url}"')
        self.assertNotContains(response, scheduled.get_absolute_url())
        self.assertNotContains(response, "Hidden location")

    def test_detail_hides_ebird_reference_when_url_is_blank(self):
        self.bird.ebird_url = ""
        self.bird.save(update_fields=("ebird_url",))

        response = self.client.get(self.bird.get_absolute_url())

        self.assertNotContains(response, "Learn more on eBird")

    def test_detail_has_birdex_breadcrumb(self):
        response = self.client.get(self.bird.get_absolute_url())

        self.assertContains(response, 'aria-label="Breadcrumb"')
        self.assertContains(response, f'href="{reverse("birdex:home")}"')
        self.assertContains(response, 'aria-current="page"')


@override_settings(STORAGES=TEST_STORAGES)
class SightingTests(TestCase):
    def setUp(self):
        self.bird = Bird.objects.create(
            common_name="Puerto Rican Tody",
            scientific_name="Todus mexicanus",
            slug="puerto-rican-tody",
            endemic=True,
        )

    def test_sighting_has_a_stable_public_url(self):
        sighting = Sighting.objects.create(bird=self.bird, date="2026-08-12")

        self.assertEqual(
            sighting.get_absolute_url(),
            f"/birdex/puerto-rican-tody/sightings/{sighting.pk}/",
        )

    def test_detail_shows_sighting_metadata_and_photos(self):
        sighting = Sighting.objects.create(
            bird=self.bird,
            date="2026-08-12",
            location="Cabo Rojo",
            notes="Seen near the trail.",
            confidence=Sighting.Confidence.HIGH,
            verification=Sighting.Verification.COMMUNITY,
        )
        photo = SightingPhoto.objects.create(
            sighting=sighting,
            image="birdex/photos/tody.jpg",
            caption="Tody on a branch",
            is_featured=True,
        )

        response = self.client.get(sighting.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "birdex/sighting_detail.html")
        self.assertContains(response, self.bird.common_name)
        self.assertContains(response, "Cabo Rojo")
        self.assertContains(response, "High")
        self.assertContains(response, "Community confirmed")
        self.assertContains(response, photo.image.url)
        self.assertContains(response, f'href="{photo.image.url}"')
        self.assertContains(response, 'class="photo-enlarge-link"')
        self.assertContains(response, 'target="_blank"')
        self.assertContains(response, PHOTO_LICENSE_URL)
        self.assertContains(response, "<dt>Photos</dt>", html=True)
        self.assertContains(response, "<dd>1</dd>", html=True)
        self.assertContains(response, 'aria-label="Breadcrumb"')
        self.assertContains(
            response,
            f'href="{self.bird.get_absolute_url()}"',
        )
        self.assertContains(response, "August 12, 2026 sighting")

    def test_scheduled_sighting_is_not_public_yet(self):
        sighting = Sighting.objects.create(
            bird=self.bird,
            date="2026-08-12",
            published_at=timezone.now() + timezone.timedelta(days=1),
        )

        response = self.client.get(sighting.get_absolute_url())

        self.assertEqual(response.status_code, 404)

    def test_primary_photo_prefers_featured_then_falls_back_to_first(self):
        sighting = Sighting.objects.create(bird=self.bird, date="2026-08-12")
        fallback = SightingPhoto.objects.create(
            sighting=sighting,
            image="birdex/photos/fallback.jpg",
        )

        self.assertEqual(sighting.primary_photo, fallback)

        featured = SightingPhoto.objects.create(
            sighting=sighting,
            image="birdex/photos/featured.jpg",
            is_featured=True,
        )

        self.assertEqual(sighting.primary_photo, featured)
        self.assertEqual(sighting.photo_count, 2)


class BirdexPhotoUploadPathTests(TestCase):
    def test_replaces_original_filename_with_uuid(self):
        path = birdex_photo_upload_to(None, "485A0312.JPG")

        self.assertRegex(
            path,
            re.compile(r"^birdex/photos/[0-9a-f]{32}\.jpg$"),
        )
        self.assertNotIn("485A0312", path)

    def test_generates_a_unique_path_for_each_upload(self):
        first_path = birdex_photo_upload_to(None, "bird.jpg")
        second_path = birdex_photo_upload_to(None, "bird.jpg")

        self.assertNotEqual(first_path, second_path)


class BirdexMediaRobotsTests(TestCase):
    def test_command_publishes_ai_training_rules_to_media_storage(self):
        with tempfile.TemporaryDirectory() as media_root, self.settings(
            MEDIA_ROOT=media_root,
            STORAGES=TEST_STORAGES,
        ):
            output = StringIO()

            call_command("publish_birdex_robots", stdout=output)

            robots_path = Path(media_root) / "robots.txt"
            content = robots_path.read_text()
            self.assertIn("Content-Signal: search=yes, ai-train=no", content)
            self.assertIn("User-agent: GPTBot\nDisallow: /", content)
            self.assertIn("Published /media/robots.txt", output.getvalue())
