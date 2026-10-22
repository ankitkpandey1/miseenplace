"""Zulip credentials and transport: odd rc files, plain http, redirects to another host."""

from pathlib import Path

import pytest

from miseenplace.model import MiseError
from miseenplace.sources.base import SourceError
from miseenplace.sources.zulip import (
    Credentials,
    ZulipSource,
    credentials_from_rc,
    load_credentials,
)
from tests.http_server import Seen, serve

SECRET = "pa%ss$word{x}"


def rc(tmp_path: Path, body: str, mode: int = 0o600) -> Path:
    path = tmp_path / "zuliprc"
    path.write_text(body)
    path.chmod(mode)
    return path


def test_a_key_with_a_percent_sign_loads_unchanged(tmp_path: Path) -> None:
    path = rc(tmp_path, f"[api]\nemail=me@example.com\nkey={SECRET}\nsite=https://z.example.com\n")
    assert credentials_from_rc(path).key == SECRET


def test_a_malformed_rc_error_never_contains_the_key(tmp_path: Path) -> None:
    path = rc(tmp_path, f"key={SECRET}\n[api]\nemail me\n")  # option before any section
    with pytest.raises(MiseError) as caught:
        credentials_from_rc(path)
    assert "valid zuliprc" in str(caught.value)
    assert SECRET not in str(caught.value) and "pa%ss" not in str(caught.value)


def test_an_rc_missing_a_field_names_the_fields_not_the_values(tmp_path: Path) -> None:
    path = rc(tmp_path, f"[api]\nemail=me@example.com\nkey={SECRET}\n")
    with pytest.raises(MiseError, match="email, key and site") as caught:
        credentials_from_rc(path)
    assert SECRET not in str(caught.value)


def test_a_world_readable_rc_warns_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = "[api]\nemail=me@example.com\nkey=k\nsite=https://z.example.com\n"
    credentials_from_rc(rc(tmp_path, body, 0o644))
    assert "chmod 600" in capsys.readouterr().err
    credentials_from_rc(rc(tmp_path, body, 0o600))
    assert capsys.readouterr().err == ""


def test_plain_http_sites_are_refused_everywhere(tmp_path: Path) -> None:
    path = rc(tmp_path, "[api]\nemail=a@b.c\nkey=k\nsite=http://z.example.com\n")
    with pytest.raises(MiseError, match="https://"):
        credentials_from_rc(path)
    env = {"ZULIP_EMAIL": "a@b.c", "ZULIP_API_KEY": "k", "ZULIP_SITE": "http://z.example.com"}
    with pytest.raises(MiseError, match="https://"):
        load_credentials({}, env)


def test_a_redirect_to_another_host_does_not_carry_the_key(tmp_path: Path) -> None:
    elsewhere, origin = Seen(), Seen(mode="redirect")
    with serve(elsewhere) as other, serve(origin) as base:
        origin.redirect_to = other + "/api/v1/users"
        src = ZulipSource(tmp_path, Credentials("me@example.com", base, "key"))
        # 127.0.0.1:<port> differs between the two servers, so this is a cross-host redirect
        with pytest.raises(SourceError):
            src.users("anyone")
    assert origin.authorization and origin.authorization[0] is not None  # sent to its own host
    assert elsewhere.authorization == []  # the other host never saw a request, let alone the key
