# Generated manually — cleans up the removed micropub app's database artifacts.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("blog", "0009_alter_post_status"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                # Drop the MediaUpload table if it exists.
                "DROP TABLE IF EXISTS micropub_mediaupload;",
                # Remove micropub migration records so future migrate runs
                # don't complain about applied migrations for a missing app.
                "DELETE FROM django_migrations WHERE app = 'micropub';",
            ],
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]