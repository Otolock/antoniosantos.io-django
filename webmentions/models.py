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


class SentWebmention(models.Model):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    NO_ENDPOINT = "no_endpoint"

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (SENT, "Sent"),
        (FAILED, "Failed"),
        (NO_ENDPOINT, "No endpoint"),
    ]

    source_url = models.URLField(max_length=500)
    target_url = models.URLField(max_length=500)
    endpoint_url = models.URLField(max_length=500, blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=PENDING,
    )
    response_code = models.PositiveSmallIntegerField(null=True, blank=True)
    error = models.TextField(blank=True)
    attempts = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("source_url", "target_url")
        ordering = ["-last_sent_at", "-created_at"]

    def __str__(self):
        return f"{self.source_url} -> {self.target_url}"
