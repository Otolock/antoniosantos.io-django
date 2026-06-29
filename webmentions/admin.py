from django.contrib import admin
from .models import Webmention

@admin.register(Webmention)
class WebmentionAdmin(admin.ModelAdmin):
    list_display = ('source_url', 'target_url', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('source_url', 'target_url', 'author_name', 'content')
