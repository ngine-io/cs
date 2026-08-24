import json
import runpy
import sys
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest

import cs
from cs import CloudStackException


@pytest.fixture(autouse=True)
def env_config(monkeypatch):
    """A complete configuration, so that no ini file is looked up."""
    monkeypatch.setenv("CLOUDSTACK_ENDPOINT", "https://localhost")
    monkeypatch.setenv("CLOUDSTACK_KEY", "foo")
    monkeypatch.setenv("CLOUDSTACK_SECRET", "bar")


def query(send):
    [request], _ = send.call_args
    return parse_qs(urlparse(request.url).query, True)


class TestSuccess:
    def test_output(self, send, response, capsys):
        send.return_value = response({"listzonesresponse": {"zone": [{"id": "z-1"}]}})

        assert cs.main(["listZones"]) is False

        out = capsys.readouterr().out
        assert json.loads(out) == {"zone": [{"id": "z-1"}]}
        assert query(send)["command"][0] == "listZones"

    def test_arguments_are_passed_on(self, send, response, capsys):
        send.return_value = response({"listzonesresponse": {}})

        cs.main(["listZones", "available=true", "name=zone 1"])

        qs = query(send)
        assert qs["available"][0] == "true"
        assert qs["name"][0] == "zone 1"

    def test_quoted_values_are_stripped(self, send, response):
        send.return_value = response({"listzonesresponse": {}})

        cs.main(["listZones", "name='zone 1'"])

        assert query(send)["name"][0] == "zone 1"

    def test_repeated_arguments_are_joined(self, send, response):
        send.return_value = response({"listzonesresponse": {}})

        cs.main(["listZones", "id=a", "id=b"])

        assert sorted(query(send)["id"][0].split(",")) == ["a", "b"]

    def test_empty_response_prints_nothing(self, send, response, capsys):
        send.return_value = response({"listzonesresponse": {}})

        assert cs.main(["listZones"]) is False
        assert capsys.readouterr().out == ""

    def test_post_option(self, send, response):
        send.return_value = response({"listzonesresponse": {}})

        cs.main(["--post", "listZones"])

        [request], _ = send.call_args
        assert request.method == "POST"

    def test_trace_option(self, send, response, capsys):
        send.return_value = response({"listzonesresponse": {"zone": []}})

        cs.main(["--trace", "listZones"])

        assert "GET https://localhost/?" in capsys.readouterr().err

    def test_async_option_skips_the_job_polling(self, send, response):
        send.return_value = response(
            {"deployvirtualmachineresponse": {"jobid": "job-1"}}
        )

        assert cs.main(["--async", "deployVirtualMachine"]) is False
        assert send.call_count == 1

    def test_async_commands_are_never_polled(self, send, response):
        send.return_value = response(
            {"queryasyncjobresultresponse": {"jobid": "job-1"}}
        )

        cs.main(["queryAsyncJobResult", "jobid=job-1"])

        assert send.call_count == 1

    def test_job_result_is_fetched_by_default(self, send, response):
        send.side_effect = [
            response({"deployvirtualmachineresponse": {"jobid": "job-1"}}),
            response(
                {
                    "queryasyncjobresultresponse": {
                        "jobstatus": 1,
                        "jobresultcode": 0,
                        "jobresult": {"virtualmachine": {"id": "vm-1"}},
                    }
                }
            ),
        ]

        assert cs.main(["deployVirtualMachine"]) is False
        assert send.call_count == 2


class TestRegion:
    def test_region_option(self, monkeypatch, write_ini, send, response):
        monkeypatch.delenv("CLOUDSTACK_ENDPOINT")
        monkeypatch.delenv("CLOUDSTACK_KEY")
        monkeypatch.delenv("CLOUDSTACK_SECRET")
        write_ini(
            """
            [hanibal]
            endpoint = https://api.example.com/hanibal
            key = key
            secret = secret
            """
        )
        send.return_value = response({"listzonesresponse": {}})

        cs.main(["--region", "hanibal", "listZones"])

        [request], _ = send.call_args
        assert request.url.startswith("https://api.example.com/hanibal")

    def test_unknown_region(self, monkeypatch, write_ini):
        monkeypatch.delenv("CLOUDSTACK_ENDPOINT")
        monkeypatch.delenv("CLOUDSTACK_KEY")
        monkeypatch.delenv("CLOUDSTACK_SECRET")
        write_ini(
            """
            [cloudstack]
            endpoint = https://api.example.com
            key = key
            secret = secret
            """
        )

        with pytest.raises(SystemExit, match="region 'nope' not in config"):
            cs.main(["--region", "nope", "listZones"])


class TestErrors:
    def test_api_error(self, send, response, capsys):
        error = {"errorcode": 530, "errortext": "Fail"}
        send.return_value = response(
            {"listzonesresponse": error}, status_code=530, reason="Unknown"
        )

        assert cs.main(["listZones"]) is True

        out, err = capsys.readouterr()
        assert "CloudStack error: HTTP 530 response from CloudStack" in err
        assert json.loads(out) == {"listzonesresponse": error}

    def test_quiet_option(self, send, response, capsys):
        send.return_value = response(
            {"listzonesresponse": {"errortext": "Fail"}},
            status_code=530,
            reason="Unknown",
        )

        assert cs.main(["--quiet", "listZones"]) is True

        out, err = capsys.readouterr()
        assert err == ""
        assert "Fail" in out

    def test_non_json_error_body(self, send, response, capsys):
        send.return_value = response(
            status_code=502,
            reason="Bad Gateway",
            text="<h1>Gateway timeout</h1>",
            content_type="text/html",
        )

        assert cs.main(["listZones"]) is True

        out, err = capsys.readouterr()
        assert "<h1>Gateway timeout</h1>" in err
        assert out == ""

    def test_error_without_a_response(self, send, capsys):
        send.side_effect = CloudStackException("boom", response=None)

        assert cs.main(["listZones"]) is True

        assert "Error: boom" in capsys.readouterr().err

    def test_malformed_option(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            cs.main(["listZones", "not-an-option"])

        assert exc_info.value.code == 2
        assert (
            "'not-an-option' is not a correctly formatted option"
            in capsys.readouterr().err
        )


class TestFormatting:
    def test_version(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            cs.main(["--version"])

        assert exc_info.value.code == 0
        assert capsys.readouterr().out.strip() == cs.__version__

    def test_no_highlighting_when_not_a_tty(self, send, response, capsys):
        send.return_value = response({"listzonesresponse": {"zone": []}})

        cs.main(["listZones"])

        assert capsys.readouterr().out == '{\n  "zone": []\n}\n'

    def test_highlighting_on_a_tty(self, monkeypatch, send, response, capsys):
        pytest.importorskip("pygments")
        monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
        send.return_value = response({"listzonesresponse": {"zone": []}})

        cs.main(["listZones"])

        assert "\x1b[" in capsys.readouterr().out

    def test_default_theme(self, monkeypatch, send, response):
        formatter = mock.Mock(return_value="{}")
        monkeypatch.setattr("cs._format_json", formatter)
        send.return_value = response({"listzonesresponse": {"zone": []}})

        cs.main(["listZones"])

        assert formatter.call_args.kwargs["theme"] == "default"

    def test_theme_option(self, monkeypatch, send, response):
        formatter = mock.Mock(return_value="{}")
        monkeypatch.setattr("cs._format_json", formatter)
        send.return_value = response({"listzonesresponse": {"zone": []}})

        cs.main(["--theme", "monokai", "listZones"])

        assert formatter.call_args.kwargs["theme"] == "monokai"

    def test_theme_environment_variable(self, monkeypatch, send, response):
        monkeypatch.setenv("CLOUDSTACK_THEME", "solarized-dark")
        formatter = mock.Mock(return_value="{}")
        monkeypatch.setattr("cs._format_json", formatter)
        send.return_value = response({"listzonesresponse": {"zone": []}})

        cs.main(["listZones"])

        assert formatter.call_args.kwargs["theme"] == "solarized-dark"

    def test_theme_from_the_config_file(self, monkeypatch, write_ini, send, response):
        for name in ("ENDPOINT", "KEY", "SECRET"):
            monkeypatch.delenv(f"CLOUDSTACK_{name}")
        write_ini(
            """
            [cloudstack]
            endpoint = https://localhost
            key = key
            secret = secret
            theme = monokai
            """
        )
        formatter = mock.Mock(return_value="{}")
        monkeypatch.setattr("cs._format_json", formatter)
        send.return_value = response({"listzonesresponse": {"zone": []}})

        cs.main(["listZones"])

        assert formatter.call_args.kwargs["theme"] == "monokai"


class TestMainModule:
    def test_python_dash_m(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["cs", "--version"])

        with pytest.raises(SystemExit) as exc_info:
            runpy.run_module("cs", run_name="__main__")

        assert exc_info.value.code == 0
        assert capsys.readouterr().out.strip() == cs.__version__
