from urllib.parse import parse_qs, urlparse

import pytest

from cs import CloudStackApiException, CloudStackException

JOB = {"deployvirtualmachineresponse": {"jobid": "job-1"}}


def pending(response):
    return response({"queryasyncjobresultresponse": {"jobstatus": 0}})


def succeeded(response, result=None):
    return response(
        {
            "queryasyncjobresultresponse": {
                "jobstatus": 1,
                "jobresultcode": 0,
                "jobresult": {"virtualmachine": {"id": "vm-1"}}
                if result is None
                else result,
            }
        }
    )


class TestJobResult:
    def test_polls_until_the_job_is_done(
        self, make_client, send, response, monkeypatch
    ):
        client = make_client(poll_interval=0)
        send.side_effect = [
            response(JOB),
            pending(response),
            succeeded(response),
        ]

        result = client.deployVirtualMachine(fetch_result=True)

        assert result == {"virtualmachine": {"id": "vm-1"}}
        assert send.call_count == 3
        [request], _ = send.call_args
        qs = parse_qs(urlparse(request.url).query, True)
        assert qs["command"][0] == "queryAsyncJobResult"
        assert qs["jobid"][0] == "job-1"

    def test_fetch_result_can_be_a_client_default(self, make_client, send, response):
        client = make_client(poll_interval=0, fetch_result=True)
        send.side_effect = [response(JOB), succeeded(response)]

        assert client.deployVirtualMachine() == {"virtualmachine": {"id": "vm-1"}}

    def test_without_fetch_result_the_job_id_is_returned(self, client, send, response):
        send.return_value = response(JOB)

        assert client.deployVirtualMachine() == {"jobid": "job-1"}
        assert send.call_count == 1

    def test_headers_are_sent_while_polling(self, make_client, send, response):
        client = make_client(poll_interval=0, headers={"X-Tenant": "acme"})
        send.side_effect = [response(JOB), succeeded(response)]

        client.deployVirtualMachine(fetch_result=True)

        [request], _ = send.call_args
        assert request.headers["X-Tenant"] == "acme"

    def test_job_failure(self, make_client, send, response):
        client = make_client(poll_interval=0)
        send.side_effect = [
            response(JOB),
            response(
                {
                    "queryasyncjobresultresponse": {
                        "jobstatus": 2,
                        "jobresultcode": 530,
                        "jobresult": {"errortext": "Insufficient capacity"},
                    }
                }
            ),
        ]

        with pytest.raises(CloudStackApiException) as exc_info:
            client.deployVirtualMachine(fetch_result=True)

        assert exc_info.value.error == {"errortext": "Insufficient capacity"}
        assert str(exc_info.value).startswith("Job failure")

    def test_job_without_a_result(self, make_client, send, response):
        client = make_client(poll_interval=0)
        send.side_effect = [
            response(JOB),
            response(
                {
                    "queryasyncjobresultresponse": {
                        "jobstatus": 1,
                        "jobresultcode": 0,
                    }
                }
            ),
        ]

        with pytest.raises(CloudStackException, match="Unknown job result"):
            client.deployVirtualMachine(fetch_result=True)

    def test_transient_errors_are_tolerated(self, make_client, send, response):
        client = make_client(poll_interval=0)
        send.side_effect = [
            response(JOB),
            OSError("connection reset"),
            OSError("connection reset"),
            succeeded(response),
        ]

        assert client.deployVirtualMachine(fetch_result=True) == {
            "virtualmachine": {"id": "vm-1"}
        }

    def test_too_many_transient_errors(self, make_client, send, response):
        client = make_client(poll_interval=0)
        send.side_effect = [response(JOB)] + [OSError("connection reset")] * 11

        with pytest.raises(OSError, match="connection reset"):
            client.deployVirtualMachine(fetch_result=True)

    def test_api_errors_while_polling_are_not_retried(
        self, make_client, send, response
    ):
        client = make_client(poll_interval=0)
        send.side_effect = [
            response(JOB),
            response({"queryasyncjobresultresponse": {}}, status_code=531),
        ]

        with pytest.raises(CloudStackApiException):
            client.deployVirtualMachine(fetch_result=True)
        assert send.call_count == 2

    def test_job_timeout(self, make_client, send, response):
        client = make_client(poll_interval=0.1, job_timeout=1)
        send.side_effect = [response(JOB)] + [pending(response) for _ in range(100)]

        with pytest.raises(CloudStackException) as exc_info:
            client.deployVirtualMachine(fetch_result=True)

        assert exc_info.value.args[0] == "Timeout waiting for async job result"
        assert exc_info.value.args[1] == "job-1"
        assert exc_info.value.response.status_code == 408

    def test_timeout_without_any_answer(self, make_client, send, response):
        client = make_client(poll_interval=0.4, job_timeout=1)
        send.side_effect = [response(JOB)] + [OSError("unreachable")] * 10

        with pytest.raises(CloudStackException) as exc_info:
            client.deployVirtualMachine(fetch_result=True)

        assert exc_info.value.args[0] == "Timeout waiting for async job result"
        assert exc_info.value.response is None

    def test_job_result_is_traced(self, make_client, send, response, capsys):
        client = make_client(poll_interval=0, trace=True)
        send.side_effect = [response(JOB), succeeded(response)]

        client.deployVirtualMachine(fetch_result=True)

        err = capsys.readouterr().err
        assert "command=queryAsyncJobResult" in err
        assert '"jobstatus": 1' in err
