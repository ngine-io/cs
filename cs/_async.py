import asyncio
import ssl

import aiohttp

from . import CloudStack, CloudStackApiException, CloudStackException
from .client import PENDING, SUCCESS, transform


class AIOCloudStack(CloudStack):
    def __getattr__(self, command):
        def handler(**kwargs):
            return self._request(command, **kwargs)

        return handler

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

        ssl_context = None
        if self.cert:
            ssl_context = ssl.create_default_context(cafile=self.cert)
        connector = aiohttp.TCPConnector(
            verify_ssl=self.verify, ssl_context=ssl_context
        )

        async with aiohttp.ClientSession(
            read_timeout=self.timeout,
            conn_timeout=self.timeout,
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
                    msg = (
                        f"Make sure endpoint URL {self.endpoint!r} is "
                        f"correct."
                    )

                    raise CloudStackException(
                        f"HTTP {response.status} response from CloudStack",
                        f"{e}. {msg}",
                        response=response,
                    )

                [key] = data.keys()
                data = data[key]
                if response.status != 200:
                    raise CloudStackApiException(
                        "HTTP {0} response from CloudStack".format(
                            response.status
                        ),
                        error=data,
                        response=response,
                    )
                if fetch_list:
                    try:
                        [key] = [k for k in data.keys() if k != "count"]
                    except ValueError:
                        done = True
                    else:
                        final_data.extend(data[key])
                        page += 1
                elif fetch_result and "jobid" in data:
                    try:
                        final_data = await asyncio.wait_for(
                            self._jobresult(data["jobid"], response),
                            self.job_timeout,
                        )
                    except asyncio.TimeoutError:
                        raise CloudStackException(
                            "Timeout waiting for async job result",
                            data["jobid"],
                            response=response,
                        )
                    done = True
                else:
                    final_data = data
                    done = True
            return final_data

    async def _jobresult(self, jobid, response):
        failures = 0
        while True:
            try:
                j = await self.queryAsyncJobResult(
                    jobid=jobid, fetch_result=False
                )
                failures = 0
                if j["jobstatus"] != PENDING:
                    if j["jobresultcode"] != 0 or j["jobstatus"] != SUCCESS:
                        raise CloudStackApiException(
                            "Job failure",
                            j,
                            error=j["jobresult"],
                            response=response,
                        )
                    if "jobresult" not in j:
                        raise CloudStackException(
                            "Unknown job result", j, response=response
                        )
                    return j["jobresult"]

            except CloudStackException:
                raise

            except Exception:
                failures += 1
                if failures > 10:
                    raise

            await asyncio.sleep(self.poll_interval)
