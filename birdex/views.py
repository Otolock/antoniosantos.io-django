from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, render

from .models import Bird, Sighting, SightingPhoto


def home(request):
    sightings = Sighting.objects.published().with_primary_photo()
    birds = list(
        Bird.objects.order_by("common_name").prefetch_related(
            Prefetch("sightings", queryset=sightings, to_attr="card_sightings")
        )
    )

    for bird in birds:
        bird.featured_photo = next(
            (
                sighting.primary_photo
                for sighting in bird.card_sightings
                if sighting.primary_photo
            ),
            None,
        )

    return render(
        request,
        "birdex/index.html",
        {"birds": birds},
    )


def bird_detail(request, slug):
    bird = get_object_or_404(Bird, slug=slug)
    sightings = list(
        bird.sightings.published()
        .with_primary_photo()
        .order_by("-date", "-published_at")
    )
    bird.featured_photo = next(
        (sighting.primary_photo for sighting in sightings if sighting.primary_photo),
        None,
    )
    return render(
        request,
        "birdex/bird_detail.html",
        {"bird": bird, "sightings": sightings},
    )


def sighting_detail(request, bird_slug, pk):
    sighting = get_object_or_404(
        Sighting.objects.published().with_primary_photo(),
        pk=pk,
        bird__slug=bird_slug,
    )
    return render(
        request,
        "birdex/sighting_detail.html",
        {"sighting": sighting},
    )
