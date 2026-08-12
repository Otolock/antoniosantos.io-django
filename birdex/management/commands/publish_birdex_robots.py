from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.management.base import BaseCommand, CommandError

from config.ai_policy import build_robots_txt


class Command(BaseCommand):
    help = "Publish AI-training crawler rules to the Birdex R2 media hostname."

    def handle(self, *args, **options):
        storage = storages["birdex"]
        name = storage.save(
            "robots.txt",
            ContentFile(build_robots_txt().encode("utf-8")),
        )
        if name != "robots.txt":
            raise CommandError(
                f"Storage saved the policy as {name!r} instead of 'robots.txt'."
            )

        self.stdout.write(self.style.SUCCESS(f"Published {storage.url(name)}"))
