from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .models import Post


def published_posts():
    return Post.objects.filter(
        status=Post.PUBLISHED,
        published_at__lte=timezone.now(),
    )


def home(request):
    posts = published_posts()[:5]
    return render(request, "blog/home.html", {"posts": posts})


def archive(request):
    posts = published_posts()
    return render(request, "blog/archive.html", {"posts": posts})


def now(request):
    return render(request, "blog/now.html")


def post_detail(request, slug):
    post = get_object_or_404(
        Post,
        slug=slug,
        status=Post.PUBLISHED,
        published_at__lte=timezone.now(),
    )
    return render(request, "blog/post_detail.html", {"post": post})
