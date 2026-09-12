import datetime
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from cs import CloudStack, CloudStackApiException, CloudStackException
from cs.client import EXPIRES_FORMAT


def query(request):
    """The parameters sent along a prepared request."""
    if request.method == "POST":
        return parse_qs(request.body, True)
    return parse_qs(urlparse(request.url).query, True)


class TestClient:
    def test_repr_falls_back_to_the_endpoint(self, make_client):
        assert repr(make_client()) == "<CloudStack: https://localhost>"
        assert repr(make_client(name="prod")) == "<CloudStack: prod>"

    def test_client_certificate_is_a_pair(self, make_client):
        client = make_client(cert="cert.pem", cert_key="key.pem")
        assert client.cert == ("cert.pem", "key.pem")

    def test_verify_defaults_to_true(self, make_client):
        assert make_client().verify is True
        assert make_client(dangerous_no_tls_verify=True).verify is False
        assert make_client(verify="/ca.pem").verify == "/ca.pem"

    def test_expiration_accepts_seconds_and_timedelta(self, make_client):
        assert make_client(expiration=42).expiration == datetime.timedelta(seconds=42)
        delta = datetime.timedelta(minutes=5)
        assert make_client(expiration=delta).expiration == delta


class TestRequest:
    def test_request_params(self, client, send, response):
        send.return_value = response({"listvirtualmachinesresponse": {}})

        machines = client.listVirtualMachines(
            listall="true", headers={"Accept-Encoding": "br"}
        )

        assert machines == {}
        assert send.call_count == 1
        [request], kwargs = send.call_args
        assert kwargs == {"cert": None, "timeout": 10, "verify": True}
        assert request.method == "GET"
        assert request.headers["Accept-Encoding"] == "br"

        qs = query(request)
        assert qs["command"][0] == "listVirtualMachines"
        assert qs["signature"][0] == "B0d6hBsZTcFVCiioSxzwKA9Pke8="
        assert qs["listall"][0] == "true"

    def test_timeout_is_forwarded(self, make_client, send, response):
        send.return_value = response({"listzonesresponse": {}})
        make_client(timeout=20).listZones()

        _, kwargs = send.call_args
        assert kwargs["timeout"] == 20

    def test_request_params_casing(self, make_client, send, response):
        client = make_client(timeout=20)
        send.return_value = response({"listvirtualmachinesresponse": {}})

        machines = client.listVirtualMachines(
            zoneId=2,
            templateId="3",
            temPlateidd="4",
            pageSize="10",
            fetch_list=True,
        )

        assert machines == []
        assert send.call_count == 1
        [request], _ = send.call_args
        assert request.method == "GET"
        assert not request.headers

        qs = query(request)
        assert qs["command"][0] == "listVirtualMachines"
        assert qs["signature"][0] == "mMS7XALuGkCXk7kj5SywySku0Z0="
        assert qs["templateId"][0] == "3"
        assert qs["temPlateidd"][0] == "4"

    def test_encoding(self, client, send, response):
        send.return_value = response({"listvirtualmachinesresponse": {}})

        client.listVirtualMachines(listall=1, unicode_param="éèààû")

        [request], _ = send.call_args
        qs = query(request)
        assert qs["signature"][0] == "gABU/KFJKD3FLAgKDuxQoryu4sA="
        assert qs["unicode_param"][0] == "éèààû"

    def test_transform(self, client, send, response):
        send.return_value = response({"listvirtualmachinesresponse": {}})

        client.listVirtualMachines(
            foo=["foo", "bar"],
            bar=[{"baz": "blah", "foo": 1000}],
            bytes_param=b"blah",
        )

        [request], _ = send.call_args
        qs = query(request)
        assert qs["signature"][0] == "ImJ/5F0P2RDL7yn4LdLnGcEx5WE="
        assert qs["bar[0].foo"][0] == "1000"
        assert qs["bar[0].baz"][0] == "blah"
        assert qs["bytes_param"][0] == "blah"
        assert qs["foo"][0] == "foo,bar"

    def test_transform_dict(self, client, send, response):
        send.return_value = response({"scalevirtualmachineresponse": {}})

        client.scaleVirtualMachine(
            id="a", details={"cpunumber": 1000, "memory": "640k"}
        )

        [request], _ = send.call_args
        qs = query(request)
        assert qs["command"][0] == "scaleVirtualMachine"
        assert qs["signature"][0] == "ZNl66z3gFhnsx2Eo3vvCIM0kAgI="
        assert qs["details[0].cpunumber"][0] == "1000"
        assert qs["details[0].memory"][0] == "640k"

    def test_transform_empty(self, client, send, response):
        send.return_value = response({"createnetworkresponse": {}})

        client.createNetwork(name="", display_text="")

        [request], _ = send.call_args
        qs = query(request)
        assert qs["signature"][0] == "CistTEiPt/4Rv1v4qSyILvPbhmg="
        assert qs["name"][0] == ""
        assert qs["display_text"][0] == ""

    def test_post_method(self, make_client, send, response):
        client = make_client(method="post")
        send.return_value = response({"listvirtualmachinesresponse": {}})

        client.listVirtualMachines(blah="brah")

        [request], _ = send.call_args
        assert request.method == "POST"
        assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"

        qs = query(request)
        assert qs["command"][0] == "listVirtualMachines"
        assert qs["signature"][0] == "58VvLSaVUqHnG9DhXNOAiDFwBoA="
        assert qs["blah"][0] == "brah"

    def test_client_headers_are_sent(self, make_client, send, response):
        client = make_client(headers={"X-Tenant": "acme"})
        send.return_value = response({"listzonesresponse": {}})

        client.listZones()

        [request], _ = send.call_args
        assert request.headers["X-Tenant"] == "acme"

    def test_signature_v3(self, make_client, send, response):
        client = make_client(expiration=600)
        send.return_value = response({"createnetworkresponse": {}})

        client.createNetwork(name="", display_text="")

        [request], _ = send.call_args
        qs = query(request)
        assert qs["signatureVersion"][0] == "3"

        expires = datetime.datetime.strptime(qs["expires"][0], EXPIRES_FORMAT)
        assert expires > datetime.datetime.now(datetime.timezone.utc)

    def test_raw_response(self, client, send, response):
        send.return_value = response({"listzonesresponse": {}}, text="not json at all")

        assert client.listZones(json=False) == "not json at all"

    def test_custom_opcode_name(self, client, send, response):
        send.return_value = response({"listzonesresponse": {}})

        client.listZones(opcode_name="cmd")

        [request], _ = send.call_args
        assert query(request)["cmd"][0] == "listZones"


class TestPagination:
    def test_fetch_list_walks_the_pages(self, client, send, response):
        send.side_effect = [
            response({"listzonesresponse": {"count": 3, "zone": [{"id": "a"}]}}),
            response({"listzonesresponse": {"count": 3, "zone": [{"id": "b"}]}}),
            response({"listzonesresponse": {"count": 3, "zone": [{"id": "c"}]}}),
        ]

        zones = client.listZones(fetch_list=True)

        assert zones == [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        assert send.call_count == 3
        pages = [query(call[0][0])["page"][0] for call in send.call_args_list]
        assert pages == ["1", "2", "3"]
        assert query(send.call_args_list[0][0][0])["pagesize"][0] == "500"

    def test_fetch_list_stops_on_an_empty_page(self, client, send, response):
        send.return_value = response({"listzonesresponse": {"count": 0}})

        assert client.listZones(fetch_list=True) == []
        assert send.call_count == 1


class TestRetry:
    def test_list_commands_are_retried(self, make_client, send, response):
        client = make_client(retry=2)
        send.side_effect = [
            requests.exceptions.ConnectionError(),
            requests.exceptions.ConnectionError(),
            response({"listzonesresponse": {"zone": []}}),
        ]

        assert client.listZones() == {"zone": []}
        assert send.call_count == 3

    def test_retries_are_exhausted(self, make_client, send):
        client = make_client(retry=1)
        send.side_effect = requests.exceptions.ConnectionError()

        with pytest.raises(requests.exceptions.ConnectionError):
            client.listZones()
        assert send.call_count == 2

    def test_other_commands_are_not_retried(self, make_client, send):
        client = make_client(retry=5)
        send.side_effect = requests.exceptions.ConnectionError()

        with pytest.raises(requests.exceptions.ConnectionError):
            client.deployVirtualMachine()
        assert send.call_count == 1


class TestErrors:
    def test_api_error(self, client, send, response):
        error = {
            "errorcode": 530,
            "uuidList": [],
            "cserrorcode": 9999,
            "errortext": "Fail",
        }
        send.return_value = response(
            {"listvirtualmachinesresponse": error},
            status_code=530,
            reason="Unknown Status Code",
        )

        with pytest.raises(CloudStackApiException) as exc_info:
            client.listVirtualMachines()

        assert exc_info.value.error == error
        assert exc_info.value.response is send.return_value
        # api exceptions are catchable as plain CloudStack exceptions
        assert isinstance(exc_info.value, CloudStackException)

    def test_bad_content_type(self, client, send, response):
        send.return_value = response(
            status_code=502,
            reason="Bad Gateway",
            text="<!DOCTYPE html><title>502</title>",
            content_type="text/html;charset=utf-8",
        )

        with pytest.raises(CloudStackException, match="HTTP 502 Bad Gateway"):
            client.listVirtualMachines()

    def test_html_answered_with_a_200(self, client, send, response):
        send.return_value = response(
            text="<!DOCTYPE html><title>hello</title>",
            content_type="text/html",
        )

        with pytest.raises(CloudStackException, match="JSON"):
            client.listVirtualMachines()

    def test_malformed_json(self, client, send, response):
        send.return_value = response(text="{'oops'")

        with pytest.raises(CloudStackException, match="Malformed JSON"):
            client.listVirtualMachines()

    def test_javascript_content_type_is_accepted(self, client, send, response):
        send.return_value = response(
            {"listzonesresponse": {}}, content_type="text/javascript"
        )

        assert client.listZones() == {}


class TestTrace:
    def test_get_request_and_response_are_traced(
        self, make_client, send, response, capsys
    ):
        client = make_client(trace=True)
        send.return_value = response({"listzonesresponse": {"zone": []}})

        client.listZones()

        err = capsys.readouterr().err
        assert "GET https://localhost/?" in err
        assert "200 OK" in err
        assert "Content-Type: application/json" in err
        assert '{"listzonesresponse": {"zone": []}}' in err

    def test_post_body_is_traced(self, make_client, send, response, capsys):
        client = make_client(trace=True, method="post")
        send.return_value = response({"listzonesresponse": {}})

        client.listZones(blah="brah")

        err = capsys.readouterr().err
        assert "POST https://localhost" in err
        assert "blah=brah" in err


class TestSigning:
    def test_unauthenticated_requests_are_not_signed(self, send, response):
        """The integration port rejects requests carrying api key parameters."""
        client = CloudStack(endpoint="http://localhost:8096/client/api")
        send.return_value = response({"listzonesresponse": {"zone": []}})

        client.listZones()

        params = query(send.call_args[0][0])
        assert params == {"command": ["listZones"], "response": ["json"]}

    def test_signature_is_stable(self):
        client = CloudStack(endpoint="https://localhost", key="foo", secret="bar")
        data = {"command": "listZones", "apiKey": "foo"}
        client._sign(data)
        # hmac-sha1 of "apikey=foo&command=listzones" keyed with the secret
        assert data["signature"] == "vRCL4rttwUS7s3a3m53Py1iHaAw="

    def test_signature_is_recomputed_on_every_page(self, client, send, response):
        send.side_effect = [
            response({"listzonesresponse": {"count": 2, "zone": [{"id": "a"}]}}),
            response({"listzonesresponse": {"count": 2, "zone": [{"id": "b"}]}}),
        ]

        client.listZones(fetch_list=True)

        signatures = [query(call[0][0])["signature"][0] for call in send.call_args_list]
        assert len(set(signatures)) == 2
