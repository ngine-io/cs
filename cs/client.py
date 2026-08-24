import base64
import contextlib
import hashlib
import hmac
import os
import re
import sys
import time
from configparser import ConfigParser
from datetime import datetime, timedelta
from fnmatch import fnmatch
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
from requests.structures import CaseInsensitiveDict

TIMEOUT = 10
PAGE_SIZE = 500
POLL_INTERVAL = 2.0
EXPIRATION = timedelta(minutes=10)
EXPIRES_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

REQUIRED_CONFIG_KEYS = {"endpoint", "key", "secret", "method", "timeout"}
ALLOWED_CONFIG_KEYS = {
    "verify",
    "cert",
    "cert_key",
    "retry",
    "theme",
    "expiration",
    "poll_interval",
    "trace",
    "dangerous_no_tls_verify",
    "header_*",
}
DEFAULT_CONFIG = {
    "timeout": 10,
    "method": "get",
    "retry": 0,
    "verify": None,
    "cert": None,
    "cert_key": None,
    "name": None,
    "expiration": 600,
    "poll_interval": POLL_INTERVAL,
    "trace": None,
    "dangerous_no_tls_verify": False,
}

PENDING = 0
SUCCESS = 1
FAILURE = 2


def strtobool(val):
    """Convert a string representation of truth to True or False.

    True values are 'y', 'yes', 't', 'true', 'on', and '1'; false values
    are 'n', 'no', 'f', 'false', 'off', and '0'.  Raises ValueError if
    'val' is anything else.

    This function has been borrowed from the distutils.util module, which
    is gone since Python 3.12.
    """
    val = val.lower()
    if val in ("y", "yes", "t", "true", "on", "1"):
        return True
    if val in ("n", "no", "f", "false", "off", "0"):
        return False
    raise ValueError(f"invalid truth value {val!r}")


def check_key(key, allowed):
    """
    Validate that the specified key is allowed according the provided
    list of patterns.
    """

    if key in allowed:
        return True

    return any(fnmatch(key, pattern) for pattern in allowed)


def cs_encode(s):
    """Encode URI component like CloudStack would do before signing.

    java.net.URLEncoder.encode(s).replace('+', '%20')
    """
    return quote(s, safe="*")


def transform(params):
    """
    Transforms an heterogeneous map of params into a CloudStack
    ready mapping of parameter to values.

    It handles lists and dicts.

    >>> p = {"a": 1, "b": "foo", "c": ["eggs", "spam"], "d": {"key": "value"}}
    >>> transform(p)
    >>> print(p)
    {'a': '1', 'b': 'foo', 'c': 'eggs,spam', 'd[0].key': 'value'}
    """
    for key, value in list(params.items()):
        if value is None:
            params.pop(key)
            continue

        if isinstance(value, (str, bytes)):
            continue

        if isinstance(value, int):
            params[key] = str(value)
        elif isinstance(value, (list, tuple, set, dict)):
            if not value:
                params.pop(key)
            else:
                if isinstance(value, dict):
                    value = [value]
                if isinstance(value, set):
                    value = list(value)
                if not isinstance(value[0], dict):
                    params[key] = ",".join(value)
                else:
                    params.pop(key)
                    for index, val in enumerate(value):
                        for name, v in val.items():
                            k = f"{key}[{index}].{name}"
                            params[k] = str(v)
        else:
            raise ValueError(type(value))


class CloudStackException(Exception):
    """Exception nicely wrapping a request response."""

    def __init__(self, *args, response=None):
        super().__init__(*args)
        self.response = response


class CloudStackApiException(CloudStackException):
    """Exception raised when CloudStack reports an API error."""

    def __init__(self, *args, error=None, response=None):
        super().__init__(*args, response=response)
        self.error = error

    def __str__(self):
        message = self.args[0] if self.args else self.__class__.__qualname__
        return f"{message}, error: {self.error}"


class CloudStack:
    def __init__(
        self,
        endpoint,
        key,
        secret,
        timeout=10,
        method="get",
        verify=None,
        cert=None,
        cert_key=None,
        name=None,
        retry=0,
        job_timeout=None,
        poll_interval=POLL_INTERVAL,
        expiration=EXPIRATION,
        trace=False,
        dangerous_no_tls_verify=False,
        headers=None,
        session=None,
        fetch_result=False,
    ):
        self.endpoint = endpoint
        self.key = key
        self.secret = secret
        self.timeout = int(timeout)
        self.method = method.lower()
        if verify:
            self.verify = verify
        else:
            self.verify = not dangerous_no_tls_verify
        if headers is None:
            headers = {}
        self.headers = headers
        self.session = session if session is not None else requests.Session()
        if cert and cert_key:
            cert = (cert, cert_key)
        self.cert = cert
        self.name = name
        self.retry = int(retry)
        self.job_timeout = int(job_timeout) if job_timeout else None
        self.poll_interval = float(poll_interval)
        if not hasattr(expiration, "seconds"):
            expiration = timedelta(seconds=int(expiration))
        self.expiration = expiration
        self.trace = bool(trace)
        self.fetch_result = fetch_result

    def __repr__(self):
        return f"<CloudStack: {self.name or self.endpoint}>"

    def _trace_request(self, prepped):
        print(prepped.method, prepped.url, file=sys.stderr)
        if prepped.headers:
            print(prepped.headers, "\n", file=sys.stderr)
        if prepped.body:
            print(prepped.body, file=sys.stderr)
        else:
            print(file=sys.stderr)

    def _trace_response(self, response):
        print(response.status_code, response.reason, file=sys.stderr)
        headers = "\n".join(f"{k}: {v}" for k, v in response.headers.items())
        print(headers, "\n", file=sys.stderr)
        print(response.text, "\n", file=sys.stderr)

    def __getattr__(self, command):
        def handler(**kwargs):
            return self._request(command, **kwargs)

        return handler

    def _prepare_request(
        self,
        command,
        json=True,
        opcode_name="command",
        fetch_list=False,
        **kwargs,
    ):
        params = CaseInsensitiveDict(**kwargs)
        params.update(
            {
                "apiKey": self.key,
                opcode_name: command,
            }
        )
        if json:
            params["response"] = "json"
        if "page" in kwargs or fetch_list:
            params.setdefault("pagesize", PAGE_SIZE)
        if "expires" not in params and self.expiration.total_seconds() >= 0:
            params["signatureVersion"] = "3"
            tz = ZoneInfo("UTC")
            expires = datetime.now(tz) + self.expiration
            params["expires"] = expires.astimezone(tz).strftime(EXPIRES_FORMAT)

        kind = "params" if self.method == "get" else "data"
        return kind, dict(params.items())

    def _request(
        self,
        command,
        json=True,
        opcode_name="command",
        fetch_list=False,
        headers=None,
        **params,
    ):
        fetch_result = params.pop("fetch_result", self.fetch_result)
        kind, params = self._prepare_request(
            command, json, opcode_name, fetch_list, **params
        )
        if headers is None:
            headers = {}
        headers.update(self.headers)

        done = False
        max_retry = self.retry
        final_data = []
        page = 1
        while not done:
            if fetch_list:
                params["page"] = page

            transform(params)
            params.pop("signature", None)
            self._sign(params)

            req = requests.Request(
                self.method, self.endpoint, headers=headers, **{kind: params}
            )
            prepped = req.prepare()
            if self.trace:
                self._trace_request(prepped)

            try:
                with self.session as session:
                    response = session.send(
                        prepped,
                        timeout=self.timeout,
                        verify=self.verify,
                        cert=self.cert,
                    )

            except requests.exceptions.ConnectionError:
                max_retry -= 1
                if max_retry < 0 or not command.startswith(("list", "queryAsync")):
                    raise
                continue
            max_retry = self.retry

            if self.trace:
                self._trace_response(response)

            data = self._response_value(response, json)

            if fetch_list:
                try:
                    [key] = [k for k in data if k != "count"]
                except ValueError:
                    done = True
                else:
                    final_data.extend(data[key])
                    page += 1
                    if len(final_data) >= data.get("count", PAGE_SIZE):
                        done = True
            elif fetch_result and "jobid" in data:
                final_data = self._jobresult(jobid=data["jobid"], headers=headers)
                done = True
            else:
                final_data = data
                done = True
        return final_data

    def _response_value(self, response, json=True):
        """Parses the HTTP response as a the cloudstack value.

        It throws an exception if the server didn't answer with a 200.
        """
        if json:
            ctype = response.headers.get("Content-Type", "")
            if not ctype.startswith(("application/json", "text/javascript")):
                if response.status_code == 200:
                    msg = f"JSON (application/json) was expected, got {ctype!r}"
                    raise CloudStackException(msg, response=response)

                raise CloudStackException(
                    f"HTTP {response.status_code} {response.reason}",
                    f"Make sure endpoint URL {self.endpoint!r} is correct.",
                    response=response,
                )

            try:
                data = response.json()
            except ValueError as e:
                raise CloudStackException(
                    f"HTTP {response.status_code} {response.reason}",
                    f"{e!s}. Malformed JSON document",
                    response=response,
                ) from e

            [key] = data.keys()
            data = data[key]
        else:
            data = response.text

        if response.status_code != 200:
            raise CloudStackApiException(
                f"HTTP {response.status_code} response from CloudStack",
                error=data,
                response=response,
            )

        return data

    def _jobresult(self, jobid, json=True, headers=None):
        """Poll the async job result.

        To be run via in a Thread, the result is put within
        the result list which is a hack.
        """
        failures = 0
        response = None

        total_time = self.job_timeout or 2**30
        remaining = timedelta(seconds=total_time)
        endtime = datetime.now() + remaining

        while remaining.total_seconds() > 0:
            timeout = max(min(self.timeout, remaining.total_seconds()), 1)
            try:
                kind, params = self._prepare_request("queryAsyncJobResult", jobid=jobid)

                transform(params)
                self._sign(params)

                req = requests.Request(
                    self.method,
                    self.endpoint,
                    headers=headers,
                    **{kind: params},
                )
                prepped = req.prepare()
                if self.trace:
                    self._trace_request(prepped)

                with self.session as session:
                    response = session.send(
                        prepped,
                        timeout=timeout,
                        verify=self.verify,
                        cert=self.cert,
                    )

                if self.trace:
                    self._trace_response(response)

                j = self._response_value(response, json)

                failures = 0
                if j["jobstatus"] != PENDING:
                    if j["jobresultcode"] or j["jobstatus"] != SUCCESS:
                        raise CloudStackApiException(
                            "Job failure",
                            error=j["jobresult"],
                            response=response,
                        )

                    if "jobresult" not in j:
                        raise CloudStackException(
                            "Unknown job result", response=response
                        )

                    return j["jobresult"]

            except CloudStackException:
                raise

            except Exception:
                failures += 1
                if failures > 10:
                    raise

            time.sleep(self.poll_interval)
            remaining = endtime - datetime.now()

        if response is not None:
            response.status_code = 408

        raise CloudStackException(
            "Timeout waiting for async job result", jobid, response=response
        )

    def _sign(self, data):
        """
        Compute a signature string according to the CloudStack
        signature method (hmac/sha1).
        """

        # Python2/3 urlencode aren't good enough for this task.
        params = "&".join(
            "=".join((key, cs_encode(value))) for key, value in sorted(data.items())
        )

        digest = hmac.new(
            self.secret.encode("utf-8"),
            msg=params.lower().encode("utf-8"),
            digestmod=hashlib.sha1,
        ).digest()

        data["signature"] = base64.b64encode(digest).decode("utf-8").strip()


def read_config_from_ini(ini_group=None):
    # Config file: $PWD/cloudstack.ini or $HOME/.cloudstack.ini
    # Last read wins in configparser
    paths = [
        os.path.join(os.path.expanduser("~"), ".cloudstack.ini"),
        os.path.join(os.getcwd(), "cloudstack.ini"),
    ]
    # Look at CLOUDSTACK_CONFIG first if present
    if "CLOUDSTACK_CONFIG" in os.environ:
        paths.append(os.path.expanduser(os.environ["CLOUDSTACK_CONFIG"]))
    if not any(os.path.exists(c) for c in paths):
        raise SystemExit("Config file not found. Tried " + ", ".join(paths))
    conf = ConfigParser()
    conf.read(paths)

    if not ini_group:
        ini_group = os.getenv("CLOUDSTACK_REGION", "cloudstack")

        if not conf.has_section(ini_group):
            return {"name": None}

    ini_config = {
        k: v
        for k, v in conf.items(ini_group)
        if v and check_key(k, REQUIRED_CONFIG_KEYS.union(ALLOWED_CONFIG_KEYS))
    }
    ini_config["name"] = ini_group

    # Convert individual header_* settings into a single dict
    for k in list(ini_config):
        if k.startswith("header_"):
            ini_config.setdefault("headers", {})
            start = len("header_")
            ini_config["headers"][k[start:]] = ini_config.pop(k)
    return ini_config


def read_config(ini_group=None):
    """
    Read the configuration from the environment, or config.

    First it try to go for the environment, then it overrides
    those with the cloudstack.ini file.
    """
    env_conf = dict(DEFAULT_CONFIG)
    for key in REQUIRED_CONFIG_KEYS.union(ALLOWED_CONFIG_KEYS):
        env_key = f"CLOUDSTACK_{key.upper()}"
        value = os.getenv(env_key)
        if value:
            env_conf[key] = value

    # overrides means we have a .ini to read
    overrides = os.getenv("CLOUDSTACK_OVERRIDES", "").strip()

    if not overrides and set(env_conf).issuperset(REQUIRED_CONFIG_KEYS):
        return env_conf

    ini_conf = read_config_from_ini(ini_group)

    overrides = {s.lower() for s in re.split(r"\W+", overrides)}
    config = dict(
        dict(env_conf, **ini_conf),
        **{k: v for k, v in env_conf.items() if k in overrides},
    )

    missings = REQUIRED_CONFIG_KEYS.difference(config)
    if missings:
        raise ValueError(
            "the configuration is missing the following keys: " + ", ".join(missings)
        )

    # convert booleans values.
    bool_keys = ("dangerous_no_tls_verify",)
    for bool_key in bool_keys:
        if isinstance(config[bool_key], str):
            with contextlib.suppress(ValueError):
                config[bool_key] = strtobool(config[bool_key])

    return config
