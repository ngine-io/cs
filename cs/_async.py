import asyncio
import contextlib
import functools
import ssl

import aiohttp

from .client import (
    PAGE_SIZE,
    PENDING,
    SUCCESS,
    CloudStack,
    CloudStackApiException,
    CloudStackException,
    transform,
    unwrap_response,
)


class AIOCloudStack(CloudStack):
    """Async CloudStack client.

    Used as an async context manager, all the requests share one aiohttp
    session, and thus its connection pool. An existing
    ``aiohttp.ClientSession`` can also be passed as ``session``, it is left
    open. Otherwise each call opens its own session.
    """

    def __init__(self, *args, session=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.session = session
        self._owns_session = False

    async def __aenter__(self):
        if self.session is None:
            self.session = self._new_session()
            self._owns_session = True
        return self

    async def __aexit__(self, *exc_info):
        await self.close()

    async def close(self):
        """Close the session opened by ``async with``, if any."""
        if self._owns_session:
            await self.session.close()
            self.session = None
            self._owns_session = False

    @functools.cached_property
    def _ssl(self):
        return self._ssl_context()

    def _new_session(self):
        connector = aiohttp.TCPConnector(ssl=self._ssl)
        timeout = aiohttp.ClientTimeout(
            sock_connect=self.timeout, sock_read=self.timeout
        )
        return aiohttp.ClientSession(timeout=timeout, connector=connector)

    @contextlib.asynccontextmanager
    async def _session_scope(self):
        """The shared session, or a session for the duration of one call."""
        if self.session is not None:
            yield self.session
        else:
            async with self._new_session() as session:
                yield session

    def _ssl_context(self):
        """Build the ssl argument for aiohttp's TCPConnector.

        ``verify`` is either a boolean or the path of a CA bundle, ``cert``
        is either the path of a client certificate or a (cert, key) tuple,
        following the semantics of the requests based client.
        """
        if self.verify is False:
            return False

        cafile = self.verify if isinstance(self.verify, str) else None
        context = ssl.create_default_context(cafile=cafile)
        if self.cert:
            if isinstance(self.cert, tuple):
                context.load_cert_chain(*self.cert)
            else:
                context.load_cert_chain(self.cert)
        return context

    async def _request(self, command, **kwargs):
        async with self._session_scope() as session:
            return await self._execute(session, command, **kwargs)

    async def _execute(
        self,
        session,
        command,
        json=True,
        opcode_name="command",
        fetch_list=False,
        headers=None,
        **params,
    ):
        fetch_result = params.pop("fetch_result", self.fetch_result)
        kwarg, kwargs = self._prepare_request(
            command, json, opcode_name, fetch_list, **params
        )

        headers = {**self.headers, **(headers or {})}

        handler = getattr(session, self.method)

        done = False
        final_data = []
        page = 1
        while not done:
            if fetch_list:
                kwargs["page"] = page

            transform(kwargs)
            kwargs.pop("signature", None)
            self._sign(kwargs)
            response = await handler(self.endpoint, headers=headers, **{kwarg: kwargs})

            ctype = response.headers.get("content-type", "").split(";")[0]
            try:
                data = await response.json(content_type=ctype)
            except ValueError as e:
                msg = f"Make sure endpoint URL {self.endpoint!r} is correct."

                raise CloudStackException(
                    f"HTTP {response.status} response from CloudStack",
                    f"{e}. {msg}",
                    response=response,
                ) from e

            data = unwrap_response(data, response)
            if response.status != 200:
                raise CloudStackApiException(
                    f"HTTP {response.status} response from CloudStack",
                    error=data,
                    response=response,
                )
            if fetch_list:
                try:
                    [key] = [k for k in data if k != "count"]
                except ValueError:
                    done = True
                else:
                    final_data.extend(data[key])
                    page += 1
                    # An empty page means there is nothing left, whatever
                    # count claims.
                    if not data[key] or len(final_data) >= data.get("count", PAGE_SIZE):
                        done = True
            elif fetch_result and "jobid" in data:
                try:
                    final_data = await asyncio.wait_for(
                        self._jobresult(session, data["jobid"], response, headers),
                        self.job_timeout,
                    )
                except asyncio.TimeoutError as e:
                    raise CloudStackException(
                        "Timeout waiting for async job result",
                        data["jobid"],
                        response=response,
                    ) from e
                done = True
            else:
                final_data = data
                done = True
        return final_data

    async def _jobresult(self, session, jobid, response, headers):
        failures = 0
        while True:
            try:
                j = await self._execute(
                    session,
                    "queryAsyncJobResult",
                    headers=headers,
                    jobid=jobid,
                    fetch_result=False,
                )
                failures = 0
                if j["jobstatus"] != PENDING:
                    if j["jobresultcode"] != 0 or j["jobstatus"] != SUCCESS:
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

            await asyncio.sleep(self.poll_interval)
