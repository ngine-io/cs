from configparser import NoSectionError

import pytest

from cs import read_config
from cs.client import read_config_from_ini

BASE_CONFIG = {
    "cert": None,
    "cert_key": None,
    "dangerous_no_tls_verify": False,
    "expiration": 600,
    "method": "get",
    "name": None,
    "poll_interval": 2.0,
    "retry": 0,
    "timeout": 10,
    "trace": None,
    "verify": None,
}


class TestEnvironmentConfig:
    def test_required_keys_only(self, monkeypatch):
        monkeypatch.setenv("CLOUDSTACK_KEY", "test key from env")
        monkeypatch.setenv("CLOUDSTACK_SECRET", "test secret from env")
        monkeypatch.setenv("CLOUDSTACK_ENDPOINT", "https://api.example.com/from-env")

        assert read_config() == dict(
            BASE_CONFIG,
            key="test key from env",
            secret="test secret from env",
            endpoint="https://api.example.com/from-env",
        )

    def test_optional_keys(self, monkeypatch):
        monkeypatch.setenv("CLOUDSTACK_KEY", "test key from env")
        monkeypatch.setenv("CLOUDSTACK_SECRET", "test secret from env")
        monkeypatch.setenv("CLOUDSTACK_ENDPOINT", "https://api.example.com/from-env")
        monkeypatch.setenv("CLOUDSTACK_METHOD", "post")
        monkeypatch.setenv("CLOUDSTACK_TIMEOUT", "99")
        monkeypatch.setenv("CLOUDSTACK_RETRY", "5")
        monkeypatch.setenv("CLOUDSTACK_VERIFY", "/path/to/ca.pem")
        monkeypatch.setenv("CLOUDSTACK_CERT", "/path/to/cert.pem")

        assert read_config() == dict(
            BASE_CONFIG,
            key="test key from env",
            secret="test secret from env",
            endpoint="https://api.example.com/from-env",
            method="post",
            timeout="99",
            retry="5",
            verify="/path/to/ca.pem",
            cert="/path/to/cert.pem",
        )

    def test_environment_does_not_read_the_ini_file(self, monkeypatch, write_ini):
        write_ini(
            """
            [cloudstack]
            endpoint = https://api.example.com/from-file
            key = test key from file
            secret = test secret from file
            """
        )
        monkeypatch.setenv("CLOUDSTACK_KEY", "test key from env")
        monkeypatch.setenv("CLOUDSTACK_SECRET", "test secret from env")
        monkeypatch.setenv("CLOUDSTACK_ENDPOINT", "https://api.example.com/from-env")

        assert read_config()["key"] == "test key from env"


class TestIniConfig:
    def test_current_dir_config(self, write_ini):
        write_ini(
            """
            [cloudstack]
            endpoint = https://api.example.com/from-file
            key = test key from file
            secret = test secret from file
            dangerous_no_tls_verify = true
            theme = monokai
            other = please ignore me
            header_x-custom-header1 = foo
            header_x-custom-header2 = bar
            timeout = 50
            """
        )

        assert read_config() == dict(
            BASE_CONFIG,
            endpoint="https://api.example.com/from-file",
            key="test key from file",
            secret="test secret from file",
            dangerous_no_tls_verify=True,
            theme="monokai",
            timeout="50",
            name="cloudstack",
            headers={"x-custom-header1": "foo", "x-custom-header2": "bar"},
        )

    def test_home_dir_config(self, write_ini):
        write_ini(
            """
            [cloudstack]
            endpoint = https://api.example.com/from-home
            key = test key from home
            secret = test secret from home
            """,
            name=".cloudstack.ini",
        )

        assert read_config()["endpoint"] == "https://api.example.com/from-home"

    def test_cloudstack_config_env_var(self, monkeypatch, write_ini):
        path = write_ini(
            """
            [cloudstack]
            endpoint = https://api.example.com/from-custom-path
            key = test key
            secret = test secret
            """,
            name="somewhere-else.ini",
        )
        monkeypatch.setenv("CLOUDSTACK_CONFIG", str(path))

        conf = read_config()
        assert conf["endpoint"] == "https://api.example.com/from-custom-path"

    def test_env_var_combined_with_dir_config(self, monkeypatch, write_ini):
        write_ini(
            """
            [hanibal]
            endpoint = https://api.example.com/from-file
            key = test key from file
            secret = secret from file
            theme = monokai
            other = please ignore me
            timeout = 50
            """
        )
        monkeypatch.setenv("CLOUDSTACK_ENDPOINT", "https://api.example.com/from-env")
        monkeypatch.setenv("CLOUDSTACK_KEY", "test key from env")
        monkeypatch.setenv("CLOUDSTACK_SECRET", "test secret from env")
        monkeypatch.setenv("CLOUDSTACK_REGION", "hanibal")
        monkeypatch.setenv("CLOUDSTACK_DANGEROUS_NO_TLS_VERIFY", "1")
        monkeypatch.setenv("CLOUDSTACK_OVERRIDES", "endpoint,secret")

        # only the overridden keys are taken from the environment
        assert read_config() == dict(
            BASE_CONFIG,
            endpoint="https://api.example.com/from-env",
            key="test key from file",
            secret="test secret from env",
            theme="monokai",
            timeout="50",
            name="hanibal",
            dangerous_no_tls_verify=True,
        )

    def test_unparsable_boolean_is_left_alone(self, write_ini):
        write_ini(
            """
            [cloudstack]
            endpoint = https://api.example.com/from-file
            key = test key
            secret = test secret
            dangerous_no_tls_verify = maybe
            """
        )

        assert read_config()["dangerous_no_tls_verify"] == "maybe"

    def test_incomplete_config(self, write_ini):
        write_ini(
            """
            [hanibal]
            endpoint = https://api.example.com/from-file
            secret = secret from file
            """
        )

        with pytest.raises(ValueError, match="missing the following keys"):
            read_config()

    def test_unknown_region(self, write_ini):
        write_ini(
            """
            [cloudstack]
            endpoint = https://api.example.com/from-file
            key = test key
            secret = test secret
            """
        )

        with pytest.raises(NoSectionError):
            read_config(ini_group="hanibal")

    def test_default_region_is_missing(self, write_ini):
        write_ini(
            """
            [hanibal]
            endpoint = https://api.example.com/from-file
            key = test key
            secret = test secret
            """
        )

        assert read_config_from_ini() == {"name": None}

    def test_no_config_file(self, config_dir):
        with pytest.raises(SystemExit, match="Config file not found"):
            read_config()
