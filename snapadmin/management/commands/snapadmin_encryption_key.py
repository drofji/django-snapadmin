"""
Generate a key for SnapAdmin's field-level encryption (#CRYPT1a).

    python manage.py snapadmin_encryption_key                # a first key
    python manage.py snapadmin_encryption_key --id 2026-09   # choose the id
    python manage.py snapadmin_encryption_key --rotate       # a key to prepend

The key is printed once, as the environment line to paste into a secret store —
it is never written to a file, a settings module or a log by this command.

``--rotate`` prints the *new* key plus the ids already configured, so the
resulting order is obvious, and never the existing material: rotation is
prepending one line, and no existing secret needs to be re-read to do it.

See :mod:`snapadmin.encryption.keys` for the four places a keyset can be
configured, and ``SECURITY.md`` for the threat model.
"""

from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ImproperlyConfigured

from snapadmin.encryption import keys as encryption_keys


class Command(BaseCommand):
    help = "Generate an encryption key for SnapAdmin's encrypted model fields."

    # This command is the fix for `snapadmin.E018` ("encrypted fields declared but
    # no key configured"). Running the system checks first would make it refuse to
    # run in exactly the situation it exists to resolve.
    requires_system_checks = []

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--id",
            dest="key_id",
            default=f"{date.today():%Y-%m}",
            help="Key id recorded in every ciphertext written with it "
                 "(default: the current year-month).",
        )
        parser.add_argument(
            "--rotate",
            action="store_true",
            help="Generate a key to prepend to the existing keyset, and show the "
                 "resulting order.",
        )

    def handle(self, *args, **options) -> None:
        key_id: str = str(options["key_id"]).strip()
        rotate: bool = bool(options["rotate"])

        if not encryption_keys.KEY_ID_PATTERN.match(key_id):
            raise CommandError(
                f"{encryption_keys.displayable_id(key_id)} is not a usable key id — use "
                "1-64 characters from A-Z, a-z, "
                "0-9, '-' and '_'. The id is stored inside every ciphertext, so '.', ':' "
                "and whitespace are not available."
            )

        keyset = None
        if rotate:
            try:
                keyset = encryption_keys.get_keyset()
            except ImproperlyConfigured as exc:
                raise CommandError(f"The current keyset cannot be read: {exc}") from exc
            if keyset is None:
                raise CommandError(
                    "--rotate needs an existing keyset, and no keyset is configured — "
                    "run this command without --rotate to generate the first key."
                )
            if key_id in keyset.ids:
                raise CommandError(
                    f"Key id {encryption_keys.displayable_id(key_id)} is already in the "
                    "keyset. Every key needs its own "
                    "id (a ciphertext names the id it was written with) — pass a different "
                    "--id, for example --id "
                    f"{date.today():%Y-%m}-2."
                )

        generated = encryption_keys.generate_key()

        self.stdout.write("")
        self.stdout.write(f"{encryption_keys.ENV_KEYS}={key_id}:{generated}")
        self.stdout.write("")

        if keyset is not None:
            self.stdout.write(
                f"Existing keys, in order (material not shown): {', '.join(keyset.ids)}"
            )
            self.stdout.write(
                "Resulting order once the new key is prepended: "
                f"{', '.join((key_id, *keyset.ids))}"
            )
            self.stdout.write("")
            self.stdout.write(
                "Prepend the line above to the existing value — the first key encrypts, "
                "every key decrypts. Keep the old keys until "
                "`snapadmin_encrypt_fields --rotate` has re-encrypted every row; dropping "
                "one earlier makes the rows still written with it unreadable."
            )
        else:
            self.stdout.write(
                "Set the line above in your secret store. The same content works as a "
                "mounted secret file, referenced by SNAPADMIN_ENCRYPTION['KEY_FILE'] or "
                f"the {encryption_keys.ENV_KEY_FILE} environment variable; a KMS or Vault "
                "lookup goes in SNAPADMIN_ENCRYPTION['KEY_PROVIDER'] instead."
            )

        self.stdout.write("")
        self.stdout.write(
            "This key is shown once and is not stored anywhere by this command. It is not "
            "your SECRET_KEY and must never be set to it — SECRET_KEY is rotated for "
            "session and CSRF reasons, and every rotation would make every encrypted "
            "column permanently unreadable. Losing this key loses the data it protects."
        )
