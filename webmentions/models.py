from django.db import models


class Webmention(models.Model):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SPAM = "spam"
    DELETED = "deleted"

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (APPROVED, "Approved"),
        (REJECTED, "Rejected"),
        (SPAM, "Spam"),
        (DELETED, "Deleted"),
    ]

    source_url = models.URLField(max_length=500)
    target_url = models.URLField(max_length=500)

    title = models.CharField(max_length=255, blank=True)
    author_name = models.CharField(max_length=255, blank=True)
    content = models.TextField(blank=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=PENDING,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("source_url", "target_url")

    def __str__(self):
        return f"{self.source_url} -> {self.target_url}"
