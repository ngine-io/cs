import asyncio
import ssl
from types import SimpleNamespace

import pytest

from cs import CloudStackApiException, CloudStackException

aiohttp = pytest.importorskip("aiohttp")

from cs import AIOCloudStack  # noqa: E402


class FakeResponse:
    def __init__(self, payload, status=200, content_type="application/json"):
        self.status = status
        self.headers = {"content-type": f"{content_type};charset=utf-8"}
        self._payload = payload

    async def json(self, content_type=None):
        if isinstance(self._payload, str):
            raise ValueError(f"Expecting value: {self._payload!r}")
        return self._payload


class FakeSession:
    def __init__(self, stub, kwargs):
        self.stub = stub
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        self.stub.closed_sessions.append(self)
        return False

    async def get(self, url, **kwargs):
        return self._request("get", url, kwargs)

    async def post(self, url, **kwargs):
        return self._request("post", url, kwargs)

    def _request(self, method, url, kwargs):
        # the client reuses one params dict across pages, record a snapshot
        kwargs = {
            key: dict(value) if isinstance(value, dict) else value
            for key, value in kwargs.items()
        }
        self.stub.requests.append((method, url, kwargs))
        if not self.stub.responses:
            raise AssertionError(f"unexpected {method} request to {url}")
        answer = self.stub.responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeAiohttp:
    """Stand-in for the aiohttp module, without any I/O."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.sessions = []
        self.closed_sessions = []
        self.connector_kwargs = None
        self.timeout_kwargs = None

    def TCPConnector(self, **kwargs):  # mimics the aiohttp module
        self.connector_kwargs = kwargs
        return SimpleNamespace(**kwargs)

    def ClientTimeout(self, **kwargs):  # mimics the aiohttp module
        self.timeout_kwargs = kwargs
        return SimpleNamespace(**kwargs)

    def ClientSession(self, **kwargs):  # mimics the aiohttp module
        session = FakeSession(self, kwargs)
        self.sessions.append(session)
        return session

    @property
    def params(self):
        """The parameters of every request made so far."""
        return [
            kwargs.get("params", kwargs.get("data")) for _, _, kwargs in self.requests
        ]


@pytest.fixture
def make_async_client(monkeypatch):
    def factory(responses, **kwargs):
        stub = FakeAiohttp(responses)
        monkeypatch.setattr("cs._async.aiohttp", stub)
        kwargs.setdefault("endpoint", "https://localhost")
        kwargs.setdefault("key", "foo")
        kwargs.setdefault("secret", "bar")
        kwargs.setdefault("expiration", -1)
        kwargs.setdefault("poll_interval", 0)
        return AIOCloudStack(**kwargs), stub

    return factory


class TestSslContext:
    def test_verification_disabled(self):
        client = AIOCloudStack(
            endpoint="https://localhost",
            key="foo",
            secret="bar",
            dangerous_no_tls_verify=True,
        )
        assert client._ssl_context() is False

    def test_default_context(self):
        client = AIOCloudStack(endpoint="https://localhost", key="foo", secret="bar")
        assert isinstance(client._ssl_context(), ssl.SSLContext)

    def test_custom_ca_bundle(self, monkeypatch):
        calls = {}
        original = ssl.create_default_context

        def create_default_context(cafile=None):
            calls["cafile"] = cafile
            return original()

        monkeypatch.setattr(
            "cs._async.ssl.create_default_context", create_default_context
        )
        client = AIOCloudStack(
            endpoint="https://localhost",
            key="foo",
            secret="bar",
            verify="/ca.pem",
        )

        client._ssl_context()
        assert calls["cafile"] == "/ca.pem"

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({"cert": "/client.pem"}, ("/client.pem",)),
            (
                {"cert": "/client.pem", "cert_key": "/client.key"},
                ("/client.pem", "/client.key"),
            ),
        ],
    )
    def test_client_certificate(self, monkeypatch, kwargs, expected):
        loaded = []
        context = ssl.create_default_context()
        monkeypatch.setattr(context, "load_cert_chain", lambda *a: loaded.append(a))
        monkeypatch.setattr(
            "cs._async.ssl.create_default_context", lambda cafile=None: context
        )
        client = AIOCloudStack(
            endpoint="https://localhost", key="foo", secret="bar", **kwargs
        )

        client._ssl_context()
        assert loaded == [expected]


class TestAsyncRequest:
    def test_simple_request(self, make_async_client):
        client, stub = make_async_client(
            [FakeResponse({"listzonesresponse": {"zone": [{"id": "z-1"}]}})]
        )

        result = asyncio.run(client.listZones())

        assert result == {"zone": [{"id": "z-1"}]}
        method, url, kwargs = stub.requests[0]
        assert (method, url) == ("get", "https://localhost")
        assert kwargs["params"]["command"] == "listZones"
        assert kwargs["params"]["signature"]
        assert stub.closed_sessions == stub.sessions

    def test_post_sends_a_body(self, make_async_client):
        client, stub = make_async_client(
            [FakeResponse({"listzonesresponse": {}})], method="post"
        )

        asyncio.run(client.listZones())

        method, _, kwargs = stub.requests[0]
        assert method == "post"
        assert kwargs["data"]["command"] == "listZones"

    def test_headers_are_sent(self, make_async_client):
        client, stub = make_async_client(
            [FakeResponse({"listzonesresponse": {}})],
            headers={"X-Tenant": "acme"},
        )

        asyncio.run(client.listZones())

        assert stub.requests[0][2]["headers"] == {"X-Tenant": "acme"}

    def test_timeout_and_ssl_are_configured(self, make_async_client):
        client, stub = make_async_client(
            [FakeResponse({"listzonesresponse": {}})], timeout=30
        )

        asyncio.run(client.listZones())

        assert stub.timeout_kwargs == {
            "sock_connect": 30,
            "sock_read": 30,
        }
        assert isinstance(stub.connector_kwargs["ssl"], ssl.SSLContext)

    def test_fetch_list_walks_the_pages(self, make_async_client):
        client, stub = make_async_client(
            [
                FakeResponse(
                    {"listzonesresponse": {"count": 2, "zone": [{"id": "a"}]}}
                ),
                FakeResponse(
                    {"listzonesresponse": {"count": 2, "zone": [{"id": "b"}]}}
                ),
            ]
        )

        zones = asyncio.run(client.listZones(fetch_list=True))

        assert zones == [{"id": "a"}, {"id": "b"}]
        assert [params["page"] for params in stub.params] == ["1", "2"]

    def test_fetch_list_stops_on_an_empty_page(self, make_async_client):
        client, stub = make_async_client(
            [FakeResponse({"listzonesresponse": {"count": 0}})]
        )

        assert asyncio.run(client.listZones(fetch_list=True)) == []
        assert len(stub.requests) == 1

    def test_api_error(self, make_async_client):
        client, _ = make_async_client(
            [FakeResponse({"listzonesresponse": {"errortext": "Fail"}}, status=530)]
        )

        with pytest.raises(CloudStackApiException) as exc_info:
            asyncio.run(client.listZones())

        assert exc_info.value.error == {"errortext": "Fail"}

    def test_malformed_json(self, make_async_client):
        client, _ = make_async_client([FakeResponse("<html>")])

        with pytest.raises(CloudStackException, match="Make sure endpoint"):
            asyncio.run(client.listZones())


class TestAsyncJobResult:
    def deploy(self):
        return FakeResponse({"deployvirtualmachineresponse": {"jobid": "job-1"}})

    def job(self, **kwargs):
        return FakeResponse({"queryasyncjobresultresponse": kwargs})

    def test_polls_until_the_job_is_done(self, make_async_client):
        client, stub = make_async_client(
            [
                self.deploy(),
                self.job(jobstatus=0),
                self.job(
                    jobstatus=1,
                    jobresultcode=0,
                    jobresult={"virtualmachine": {"id": "vm-1"}},
                ),
            ]
        )

        result = asyncio.run(client.deployVirtualMachine(fetch_result=True))

        assert result == {"virtualmachine": {"id": "vm-1"}}
        assert len(stub.requests) == 3

    def test_job_failure(self, make_async_client):
        client, _ = make_async_client(
            [
                self.deploy(),
                self.job(
                    jobstatus=2,
                    jobresultcode=530,
                    jobresult={"errortext": "Insufficient capacity"},
                ),
            ]
        )

        with pytest.raises(CloudStackApiException) as exc_info:
            asyncio.run(client.deployVirtualMachine(fetch_result=True))

        assert exc_info.value.error == {"errortext": "Insufficient capacity"}

    def test_job_timeout(self, make_async_client):
        client, _ = make_async_client(
            [self.deploy()] + [self.job(jobstatus=0) for _ in range(20)],
            job_timeout=1,
            poll_interval=0.1,
        )

        with pytest.raises(
            CloudStackException, match="Timeout waiting for async job result"
        ):
            asyncio.run(client.deployVirtualMachine(fetch_result=True))

    def test_transient_errors_are_tolerated(self, make_async_client):
        client, stub = make_async_client(
            [
                self.deploy(),
                RuntimeError("connection reset"),
                RuntimeError("connection reset"),
                self.job(jobstatus=1, jobresultcode=0, jobresult={"ok": True}),
            ]
        )

        result = asyncio.run(client.deployVirtualMachine(fetch_result=True))

        assert result == {"ok": True}
        assert len(stub.requests) == 4

    def test_too_many_transient_errors(self, make_async_client):
        client, _ = make_async_client(
            [self.deploy()] + [RuntimeError("connection reset")] * 11
        )

        with pytest.raises(RuntimeError, match="connection reset"):
            asyncio.run(client.deployVirtualMachine(fetch_result=True))

    def test_job_without_a_result(self, make_async_client):
        client, _ = make_async_client(
            [self.deploy(), self.job(jobstatus=1, jobresultcode=0)]
        )

        with pytest.raises(CloudStackException, match="Unknown job result"):
            asyncio.run(client.deployVirtualMachine(fetch_result=True))

    def test_malformed_answer_while_polling_aborts(self, make_async_client):
        client, _ = make_async_client(
            [
                self.deploy(),
                FakeResponse("<html>oops"),
                self.job(jobstatus=1, jobresultcode=0, jobresult={"ok": True}),
            ]
        )

        # a malformed answer raises a CloudStackException, which aborts
        with pytest.raises(CloudStackException):
            asyncio.run(client.deployVirtualMachine(fetch_result=True))

    def test_without_fetch_result_the_job_id_is_returned(self, make_async_client):
        client, stub = make_async_client([self.deploy()])

        result = asyncio.run(client.deployVirtualMachine())

        assert result == {"jobid": "job-1"}
        assert len(stub.requests) == 1
