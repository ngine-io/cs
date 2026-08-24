import asyncio
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
)


class AIOCloudStack(CloudStack):
    def __getattr__(self, command):
        def handler(**kwargs):
            return self._request(command, **kwargs)

        return handler

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

    async def _request(
        self,
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

        connector = aiohttp.TCPConnector(ssl=self._ssl_context())
        timeout = aiohttp.ClientTimeout(
            sock_connect=self.timeout, sock_read=self.timeout
        )

        async with aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
        ) as session:
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
                response = await handler(
                    self.endpoint, headers=headers, **{kwarg: kwargs}
                )

                ctype = response.headers["content-type"].split(";")[0]
                try:
                    data = await response.json(content_type=ctype)
                except ValueError as e:
                    msg = f"Make sure endpoint URL {self.endpoint!r} is correct."

                    raise CloudStackException(
                        f"HTTP {response.status} response from CloudStack",
                        f"{e}. {msg}",
                        response=response,
                    ) from e

                [key] = data.keys()
                data = data[key]
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
                        if len(final_data) >= data.get("count", PAGE_SIZE):
                            done = True
                elif fetch_result and "jobid" in data:
                    try:
                        final_data = await asyncio.wait_for(
                            self._jobresult(data["jobid"], response),
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

    async def _jobresult(self, jobid, response):
        failures = 0
        while True:
            try:
                j = await self.queryAsyncJobResult(jobid=jobid, fetch_result=False)
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
