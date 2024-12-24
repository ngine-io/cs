# coding: utf-8
import datetime
import os
from contextlib import contextmanager
from functools import partial
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from cs import (
    CloudStack,
    CloudStackApiException,
    CloudStackException,
    read_config,
)
from cs.client import EXPIRES_FORMAT

from requests.structures import CaseInsensitiveDict


@contextmanager
def env(**kwargs):
    old_env = {}
    for key in kwargs:
        if key in os.environ:
            old_env[key] = os.environ[key]
    os.environ.update(kwargs)
    try:
        yield
    finally:
        for key in kwargs:
            if key in old_env:
                os.environ[key] = old_env[key]
            else:
                del os.environ[key]


@contextmanager
def cwd(path):
    initial = os.getcwd()
    os.chdir(path)
    try:
        with patch("os.path.expanduser", new=lambda x: path):
            yield
    finally:
        os.chdir(initial)


class ExceptionTest(TestCase):
    def test_api_exception_str(self):
        e = CloudStackApiException(
            "CS failed", error={"test": 42}, response=None
        )
        self.assertEqual("CS failed, error: {'test': 42}", str(e))


class ConfigTest(TestCase):
    def test_env_vars(self):
        with env(
            CLOUDSTACK_KEY="test key from env",
            CLOUDSTACK_SECRET="test secret from env",
            CLOUDSTACK_ENDPOINT="https://api.example.com/from-env",
        ):
            conf = read_config()
            self.assertEqual(
                {
                    "key": "test key from env",
                    "secret": "test secret from env",
                    "endpoint": "https://api.example.com/from-env",
                    "expiration": 600,
                    "method": "get",
                    "trace": None,
                    "timeout": 10,
                    "poll_interval": 2.0,
                    "verify": None,
                    "dangerous_no_tls_verify": False,
                    "cert": None,
                    "cert_key": None,
                    "name": None,
                    "retry": 0,
                },
                conf,
            )

        with env(
            CLOUDSTACK_KEY="test key from env",
            CLOUDSTACK_SECRET="test secret from env",
            CLOUDSTACK_ENDPOINT="https://api.example.com/from-env",
            CLOUDSTACK_METHOD="post",
            CLOUDSTACK_TIMEOUT="99",
            CLOUDSTACK_RETRY="5",
            CLOUDSTACK_VERIFY="/path/to/ca.pem",
            CLOUDSTACK_CERT="/path/to/cert.pem",
        ):
            conf = read_config()
            self.assertEqual(
                {
                    "key": "test key from env",
                    "secret": "test secret from env",
                    "endpoint": "https://api.example.com/from-env",
                    "expiration": 600,
                    "method": "post",
                    "timeout": "99",
                    "trace": None,
                    "poll_interval": 2.0,
                    "verify": "/path/to/ca.pem",
                    "cert": "/path/to/cert.pem",
                    "cert_key": None,
                    "dangerous_no_tls_verify": False,
                    "name": None,
                    "retry": "5",
                },
                conf,
            )

    def test_env_var_combined_with_dir_config(self):
        with open("/tmp/cloudstack.ini", "w") as f:
            f.write(
                "[hanibal]\n"
                "endpoint = https://api.example.com/from-file\n"
                "key = test key from file\n"
                "secret = secret from file\n"
                "theme = monokai\n"
                "other = please ignore me\n"
                "timeout = 50"
            )
            self.addCleanup(partial(os.remove, "/tmp/cloudstack.ini"))
        # Secret gets read from env var
        with env(
            CLOUDSTACK_ENDPOINT="https://api.example.com/from-env",
            CLOUDSTACK_KEY="test key from env",
            CLOUDSTACK_SECRET="test secret from env",
            CLOUDSTACK_REGION="hanibal",
            CLOUDSTACK_DANGEROUS_NO_TLS_VERIFY="1",
            CLOUDSTACK_OVERRIDES="endpoint,secret",
        ), cwd("/tmp"):
            conf = read_config()
            self.assertEqual(
                {
                    "endpoint": "https://api.example.com/from-env",
                    "key": "test key from file",
                    "secret": "test secret from env",
                    "expiration": 600,
                    "theme": "monokai",
                    "timeout": "50",
                    "trace": None,
                    "poll_interval": 2.0,
                    "name": "hanibal",
                    "verify": None,
                    "dangerous_no_tls_verify": True,
                    "retry": 0,
                    "method": "get",
                    "cert": None,
                    "cert_key": None,
                },
                conf,
            )

    def test_current_dir_config(self):
        with open("/tmp/cloudstack.ini", "w") as f:
            f.write(
                "[cloudstack]\n"
                "endpoint = https://api.example.com/from-file\n"
                "key = test key from file\n"
                "secret = test secret from file\n"
                "dangerous_no_tls_verify = true\n"
                "theme = monokai\n"
                "other = please ignore me\n"
                "header_x-custom-header1 = foo\n"
                "header_x-custom-header2 = bar\n"
                "timeout = 50"
            )
            self.addCleanup(partial(os.remove, "/tmp/cloudstack.ini"))

        with cwd("/tmp"):
            conf = read_config()
            self.assertEqual(
                {
                    "endpoint": "https://api.example.com/from-file",
                    "key": "test key from file",
                    "secret": "test secret from file",
                    "expiration": 600,
                    "theme": "monokai",
                    "timeout": "50",
                    "trace": None,
                    "poll_interval": 2.0,
                    "name": "cloudstack",
                    "verify": None,
                    "dangerous_no_tls_verify": True,
                    "retry": 0,
                    "method": "get",
                    "cert": None,
                    "cert_key": None,
                    "headers": {
                        "x-custom-header1": "foo",
                        "x-custom-header2": "bar",
                    },
                },
                conf,
            )

    def test_incomplete_config(self):
        with open("/tmp/cloudstack.ini", "w") as f:
            f.write(
                "[hanibal]\n"
                "endpoint = https://api.example.com/from-file\n"
                "secret = secret from file\n"
                "theme = monokai\n"
                "other = please ignore me\n"
                "timeout = 50"
            )
            self.addCleanup(partial(os.remove, "/tmp/cloudstack.ini"))
        # Secret gets read from env var
        with cwd("/tmp"):
            self.assertRaises(ValueError, read_config)


class RequestTest(TestCase):
    @patch("requests.Session.send")
    def test_request_params(self, mock):
        cs = CloudStack(
            endpoint="https://localhost",
            key="foo",
            secret="bar",
            timeout=20,
            expiration=-1,
        )
        mock.return_value.status_code = 200
        mock.return_value.json.return_value = {
            "listvirtualmachinesresponse": {},
        }
        machines = cs.listVirtualMachines(
            listall="true", headers={"Accept-Encoding": "br"}
        )
        self.assertEqual(machines, {})

        self.assertEqual(1, mock.call_count)

        [request], kwargs = mock.call_args

        self.assertEqual(dict(cert=None, timeout=20, verify=True), kwargs)
        self.assertEqual("GET", request.method)
        self.assertEqual("br", request.headers["Accept-Encoding"])

        url = urlparse(request.url)
        qs = parse_qs(url.query, True)

        self.assertEqual("listVirtualMachines", qs["command"][0])
        self.assertEqual("B0d6hBsZTcFVCiioSxzwKA9Pke8=", qs["signature"][0])
        self.assertEqual("true", qs["listall"][0])

    @patch("requests.Session.send")
    def test_request_params_casing(self, mock):
        cs = CloudStack(
            endpoint="https://localhost",
            key="foo",
            secret="bar",
            timeout=20,
            expiration=-1,
        )
        mock.return_value.status_code = 200
        mock.return_value.json.return_value = {
            "listvirtualmachinesresponse": {},
        }
        machines = cs.listVirtualMachines(
            zoneId=2,
            templateId="3",
            temPlateidd="4",
            pageSize="10",
            fetch_list=True,
        )
        self.assertEqual(machines, [])

        self.assertEqual(1, mock.call_count)

        [request], kwargs = mock.call_args

        self.assertEqual(dict(cert=None, timeout=20, verify=True), kwargs)
        self.assertEqual("GET", request.method)
        self.assertFalse(request.headers)

        url = urlparse(request.url)
        qs = parse_qs(url.query, True)

        self.assertEqual("listVirtualMachines", qs["command"][0])
        self.assertEqual("mMS7XALuGkCXk7kj5SywySku0Z0=", qs["signature"][0])
        self.assertEqual("3", qs["templateId"][0])
        self.assertEqual("4", qs["temPlateidd"][0])

    @patch("requests.Session.send")
    def test_encoding(self, mock):
        cs = CloudStack(
            endpoint="https://localhost",
            key="foo",
            secret="bar",
            expiration=-1,
        )
        mock.return_value.status_code = 200
        mock.return_value.json.return_value = {
            "listvirtualmachinesresponse": {},
        }
        cs.listVirtualMachines(listall=1, unicode_param="éèààû")
        self.assertEqual(1, mock.call_count)

        [request], _ = mock.call_args

        url = urlparse(request.url)
        qs = parse_qs(url.query, True)

        self.assertEqual("listVirtualMachines", qs["command"][0])
        self.assertEqual("gABU/KFJKD3FLAgKDuxQoryu4sA=", qs["signature"][0])
        self.assertEqual("éèààû", qs["unicode_param"][0])

    @patch("requests.Session.send")
    def test_transform(self, mock):
        cs = CloudStack(
            endpoint="https://localhost",
            key="foo",
            secret="bar",
            expiration=-1,
        )
        mock.return_value.status_code = 200
        mock.return_value.json.return_value = {
            "listvirtualmachinesresponse": {},
        }
        cs.listVirtualMachines(
            foo=["foo", "bar"],
            bar=[{"baz": "blah", "foo": 1000}],
            bytes_param=b"blah",
        )
        self.assertEqual(1, mock.call_count)

        [request], kwargs = mock.call_args

        self.assertEqual(dict(cert=None, timeout=10, verify=True), kwargs)
        self.assertEqual("GET", request.method)
        self.assertFalse(request.headers)

        url = urlparse(request.url)
        qs = parse_qs(url.query, True)

        self.assertEqual("listVirtualMachines", qs["command"][0])
        self.assertEqual("ImJ/5F0P2RDL7yn4LdLnGcEx5WE=", qs["signature"][0])
        self.assertEqual("1000", qs["bar[0].foo"][0])
        self.assertEqual("blah", qs["bar[0].baz"][0])
        self.assertEqual("blah", qs["bytes_param"][0])
        self.assertEqual("foo,bar", qs["foo"][0])

    @patch("requests.Session.send")
    def test_transform_dict(self, mock):
        cs = CloudStack(
            endpoint="https://localhost",
            key="foo",
            secret="bar",
            expiration=-1,
        )
        mock.return_value.status_code = 200
        mock.return_value.json.return_value = {
            "scalevirtualmachineresponse": {},
        }
        cs.scaleVirtualMachine(
            id="a", details={"cpunumber": 1000, "memory": "640k"}
        )
        self.assertEqual(1, mock.call_count)

        [request], kwargs = mock.call_args

        self.assertEqual(dict(cert=None, timeout=10, verify=True), kwargs)
        self.assertEqual("GET", request.method)
        self.assertFalse(request.headers)

        url = urlparse(request.url)
        qs = parse_qs(url.query, True)

        self.assertEqual("scaleVirtualMachine", qs["command"][0])
        self.assertEqual("ZNl66z3gFhnsx2Eo3vvCIM0kAgI=", qs["signature"][0])
        self.assertEqual("1000", qs["details[0].cpunumber"][0])
        self.assertEqual("640k", qs["details[0].memory"][0])

    @patch("requests.Session.send")
    def test_transform_empty(self, mock):
        cs = CloudStack(
            endpoint="https://localhost",
            key="foo",
            secret="bar",
            expiration=-1,
        )
        mock.return_value.status_code = 200
        mock.return_value.json.return_value = {
            "createnetworkresponse": {},
        }
        cs.createNetwork(name="", display_text="")
        self.assertEqual(1, mock.call_count)

        [request], kwargs = mock.call_args

        self.assertEqual(dict(cert=None, timeout=10, verify=True), kwargs)
        self.assertEqual("GET", request.method)
        self.assertFalse(request.headers)

        url = urlparse(request.url)
        qs = parse_qs(url.query, True)

        self.assertEqual("createNetwork", qs["command"][0])
        self.assertEqual("CistTEiPt/4Rv1v4qSyILvPbhmg=", qs["signature"][0])
        self.assertEqual("", qs["name"][0])
        self.assertEqual("", qs["display_text"][0])

    @patch("requests.Session.send")
    def test_method(self, mock):
        cs = CloudStack(
            endpoint="https://localhost",
            key="foo",
            secret="bar",
            method="post",
            expiration=-1,
        )
        mock.return_value.status_code = 200
        mock.return_value.json.return_value = {
            "listvirtualmachinesresponse": {},
        }
        cs.listVirtualMachines(blah="brah")
        self.assertEqual(1, mock.call_count)

        [request], kwargs = mock.call_args

        self.assertEqual(dict(cert=None, timeout=10, verify=True), kwargs)
        self.assertEqual("POST", request.method)
        self.assertEqual(
            "application/x-www-form-urlencoded",
            request.headers["Content-Type"],
        )

        qs = parse_qs(request.body, True)

        self.assertEqual("listVirtualMachines", qs["command"][0])
        self.assertEqual("58VvLSaVUqHnG9DhXNOAiDFwBoA=", qs["signature"][0])
        self.assertEqual("brah", qs["blah"][0])

    @patch("requests.Session.send")
    def test_error(self, mock):
        mock.return_value.status_code = 530
        mock.return_value.json.return_value = {
            "listvirtualmachinesresponse": {
                "errorcode": 530,
                "uuidList": [],
                "cserrorcode": 9999,
                "errortext": "Fail",
            }
        }
        cs = CloudStack(endpoint="https://localhost", key="foo", secret="bar")
        self.assertRaises(CloudStackException, cs.listVirtualMachines)

    @patch("requests.Session.send")
    def test_bad_content_type(self, get):
        get.return_value.status_code = 502
        get.return_value.headers = CaseInsensitiveDict(
            **{"content-type": "text/html;charset=utf-8"}
        )
        get.return_value.text = (
            "<!DOCTYPE html><title>502</title>" "<h1>Gateway timeout</h1>"
        )

        cs = CloudStack(endpoint="https://localhost", key="foo", secret="bar")
        self.assertRaises(CloudStackException, cs.listVirtualMachines)

    @patch("requests.Session.send")
    def test_signature_v3(self, mock):
        cs = CloudStack(
            endpoint="https://localhost",
            key="foo",
            secret="bar",
            expiration=600,
        )
        mock.return_value.status_code = 200
        mock.return_value.json.return_value = {
            "createnetworkresponse": {},
        }
        cs.createNetwork(name="", display_text="")
        self.assertEqual(1, mock.call_count)

        [request], _ = mock.call_args

        url = urlparse(request.url)
        qs = parse_qs(url.query, True)

        self.assertEqual("createNetwork", qs["command"][0])
        self.assertEqual("3", qs["signatureVersion"][0])

        expires = qs["expires"][0]
        # we ignore the timezone for Python2's lack of %z
        expires = datetime.datetime.strptime(expires[:19], EXPIRES_FORMAT[:-2])

        self.assertTrue(expires > datetime.datetime.utcnow(), expires)
