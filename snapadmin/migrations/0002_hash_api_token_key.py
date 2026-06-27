"""Hash API token keys at rest.

Replaces the plaintext ``token_key`` column with a non-secret ``token_prefix``
(for identification) and a unique ``token_digest`` (SHA-256 of the raw key).
Existing rows are backfilled from their plaintext key before it is dropped.
"""

import hashlib

from django.db import migrations, models


def backfill_digests(apps, schema_editor):
    APIToken = apps.get_model("snapadmin", "APIToken")
    for token in APIToken.objects.all().iterator():
        raw_key = token.token_key
        token.token_prefix = raw_key[:8]
        token.token_digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        token.save(update_fields=["token_prefix", "token_digest"])


class Migration(migrations.Migration):

    dependencies = [
        ("snapadmin", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="apitoken",
            name="token_prefix",
            field=models.CharField(blank=True, editable=False, help_text="First 8 characters of the key, for identification. Not secret.", max_length=8, verbose_name="Token Prefix"),
        ),
        migrations.AddField(
            model_name="apitoken",
            name="token_digest",
            field=models.CharField(blank=True, editable=False, help_text="SHA-256 hash of the secret key. The raw key is never stored — it is shown only once, at creation.", max_length=64, verbose_name="Token Digest"),
        ),
        migrations.RunPython(backfill_digests, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="apitoken",
            name="token_digest",
            field=models.CharField(blank=True, editable=False, help_text="SHA-256 hash of the secret key. The raw key is never stored — it is shown only once, at creation.", max_length=64, unique=True, verbose_name="Token Digest"),
        ),
        migrations.RemoveField(
            model_name="apitoken",
            name="token_key",
        ),
    ]
