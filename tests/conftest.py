import json
import os
import textwrap
from unittest import mock

import pytest
from requests.structures import CaseInsensitiveDict

from cs import CloudStack

ENDPOINT = "https://localhost"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """Keep the developer's own CLOUDSTACK_* variables out of the tests."""
    for key in list(os.environ):
        if key.startswith("CLOUDSTACK_"):
            monkeypatch.delenv(key)


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """Make ``tmp_path`` both the home and the current directory.

    ``read_config_from_ini()`` looks for ``~/.cloudstack.ini`` and
    ``$PWD/cloudstack.ini``, both of which end up in ``tmp_path``.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        os.path, "expanduser", lambda path: path.replace("~", str(tmp_path), 1)
    )
    return tmp_path


@pytest.fixture
def write_ini(config_dir):
    """Write an ini file into the (temporary) config directory."""

    def write(content, name="cloudstack.ini"):
        path = config_dir / name
        path.write_text(textwrap.dedent(content).lstrip())
        return path

    return write


def make_response(
    payload=None,
    *,
    status_code=200,
    reason="OK",
    text=None,
    content_type="application/json",
):
    """Build a fake ``requests.Response``.

    Without a ``payload``, ``response.json()`` fails the way requests does
    on a malformed document.
    """
    response = mock.Mock()
    response.status_code = status_code
    response.reason = reason
    response.headers = CaseInsensitiveDict({"Content-Type": content_type})
    if payload is None:
        response.text = "" if text is None else text
        response.json.side_effect = ValueError("Expecting value")
    else:
        response.text = json.dumps(payload) if text is None else text
        response.json.return_value = payload
    return response


@pytest.fixture
def response():
    return make_response


@pytest.fixture
def send(monkeypatch):
    """Patch out the network layer of the requests based client."""
    sender = mock.MagicMock()
    monkeypatch.setattr("requests.Session.send", sender)
    return sender


@pytest.fixture
def make_client():
    """Build a client with sane defaults for tests.

    ``expiration=-1`` disables the signature version 3 parameters, which
    keeps the generated signatures stable.
    """

    def factory(**kwargs):
        kwargs.setdefault("endpoint", ENDPOINT)
        kwargs.setdefault("key", "foo")
        kwargs.setdefault("secret", "bar")
        kwargs.setdefault("expiration", -1)
        return CloudStack(**kwargs)

    return factory


@pytest.fixture
def client(make_client):
    return make_client()
