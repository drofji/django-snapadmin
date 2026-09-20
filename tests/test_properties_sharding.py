"""
tests/test_properties_sharding.py

Property-based tests for ``SNAPADMIN_SHARDING`` DSN parsing (#QA1d, part 3).

DSNs are hand-written configuration, and the part most likely to be written
wrong is the password: generated passwords routinely contain ``/``, ``#``,
``?``, ``@`` or ``:``, each of which is URL syntax unless percent-encoded. So
the laws here are about two things:

* **Round-trip** — a DSN whose parts are percent-encoded parses back to exactly
  those parts, for every scheme spelling, port and Unicode user/password/name.
* **No password in any output, however the DSN is written** — with the
  password spliced in *raw*, ``parse_dsn`` either reads it back correctly or
  raises ``ImproperlyConfigured``; neither the error it raises nor
  ``redact_dsn``'s output may carry any piece of it. These messages reach
  ``manage.py check``, deploy logs and tracebacks.

Found by this file: a raw ``/``, ``#`` or ``?`` in the password moved the rest
of the DSN out of the authority, so ``redact_dsn`` found no password to hide
and echoed the whole DSN, and the port error echoed the password's first half;
a digits-only password followed by ``/`` even parsed *successfully*, into the
wrong host with no password at all.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest
from django.core.exceptions import ImproperlyConfigured
from hypothesis import assume, given
from hypothesis import strategies as st

from snapadmin.sharding.registration import parse_dsn, redact_dsn

SCHEMES = st.sampled_from(
    ["postgres", "postgresql", "mysql", "POSTGRES", "PostgreSQL", "MySQL"]
)
ENGINES = {
    "postgres": "django.db.backends.postgresql",
    "postgresql": "django.db.backends.postgresql",
    "mysql": "django.db.backends.mysql",
}
HOSTS = st.from_regex(r"\A[a-z][a-z0-9-]{0,20}(\.[a-z][a-z0-9-]{0,20}){0,3}\Z")
PORTS = st.one_of(st.none(), st.integers(min_value=1, max_value=65535))
NAMES = st.text(min_size=1, max_size=40)
USERS = st.text(min_size=1, max_size=30)

#: Any printable ASCII, URL syntax characters included — what a password
#: generator emits. Except ``%``: in a DSN, ``%41`` *is* the escape for ``A``,
#: so a raw password containing one cannot be told apart from an encoded one —
#: that spelling is covered by the round-trip law above instead.
PASSWORD_BODY = st.text(
    alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7E, exclude_characters="%"),
    max_size=24,
)
#: Distinctive head and tail, so a leaked *fragment* is detectable too: if any
#: part of the password reaches an output, one of these two does.
HEAD, TAIL = "Q7xK", "K9zQ"


def _dsn(scheme: str, user: str, password: str, host: str, port: int | None, name: str) -> str:
    authority = f"{user}:{password}@{host}" + (f":{port}" if port else "")
    return f"{scheme}://{authority}/{name}"


class TestRoundTrip:
    @given(scheme=SCHEMES, user=USERS, password=st.text(max_size=30), host=HOSTS, port=PORTS, name=NAMES)
    def test_a_percent_encoded_dsn_parses_back_to_its_parts(
        self, scheme, user, password, host, port, name
    ):
        dsn = _dsn(
            scheme, quote(user, safe=""), quote(password, safe=""), host, port, quote(name, safe="")
        )

        parsed = parse_dsn(dsn)

        assert parsed == {
            "ENGINE": ENGINES[scheme.lower()],
            "NAME": name,
            "USER": user,
            "PASSWORD": password,
            "HOST": host,
            "PORT": str(port) if port else "",
        }

    @given(scheme=SCHEMES, user=USERS, password=st.text(min_size=1, max_size=30), host=HOSTS, port=PORTS, name=NAMES)
    def test_redaction_keeps_everything_but_the_password(
        self, scheme, user, password, host, port, name
    ):
        dsn = _dsn(
            scheme, quote(user, safe=""), quote(password, safe=""), host, port, quote(name, safe="")
        )

        redacted = redact_dsn(dsn)

        assert redacted == _dsn(
            scheme, quote(user, safe=""), "***", host, port, quote(name, safe="")
        )


class TestNoPasswordEscapes:
    @given(body=PASSWORD_BODY, host=HOSTS, port=PORTS)
    def test_a_raw_password_is_read_back_exactly_or_refused_without_a_trace(
        self, body, host, port
    ):
        password = HEAD + body + TAIL
        dsn = _dsn("postgres", "app", password, host, port, "shop")

        try:
            parsed = parse_dsn(dsn)
        except ImproperlyConfigured as exc:
            message = str(exc)
            assert HEAD not in message and TAIL not in message, message
            return
        # Accepted: then it must be the configuration that was written — the
        # password, host, port and database as given, not a silent re-reading.
        assert parsed["PASSWORD"] == password
        assert parsed["HOST"] == host
        assert parsed["PORT"] == (str(port) if port else "")
        assert parsed["NAME"] == "shop"

    @given(body=PASSWORD_BODY, host=HOSTS, port=PORTS)
    def test_redaction_hides_a_raw_password_whatever_it_contains(self, body, host, port):
        password = HEAD + body + TAIL
        dsn = _dsn("postgres", "app", password, host, port, "shop")

        redacted = redact_dsn(dsn)

        assert HEAD not in redacted and TAIL not in redacted, redacted
        assert redacted.startswith("postgres://app:***@")

    @given(digits=st.text(alphabet="0123456789", min_size=1, max_size=5), rest=st.text(alphabet="abcdef", min_size=1, max_size=8))
    def test_a_numeric_password_with_a_raw_slash_is_refused_not_misread(self, digits, rest):
        """``app:1234/abc@db/shop`` is *valid URL syntax* — host ``app``, port
        ``1234``, database ``abc@db/shop`` — and was accepted as exactly that,
        pointing the shard at the wrong server with no password at all."""
        dsn = f"postgres://app:{digits}/{rest}@db.internal/shop"

        with pytest.raises(ImproperlyConfigured, match="percent-encode") as caught:
            parse_dsn(dsn)

        assert f"{digits}/{rest}" not in str(caught.value)

    @given(text=st.text(max_size=80))
    def test_arbitrary_text_is_parsed_or_refused_with_improperly_configured(self, text):
        try:
            parsed = parse_dsn(text)
        except ImproperlyConfigured:
            return
        assert parsed["ENGINE"] in set(ENGINES.values())
        assert parsed["HOST"] and parsed["NAME"]

    @given(text=st.text(max_size=80))
    def test_redaction_never_raises(self, text):
        assert isinstance(redact_dsn(text), str)
