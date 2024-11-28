CS
==

.. image:: https://img.shields.io/pypi/l/cs.svg
   :alt: License
   :target: https://pypi.org/project/cs/

.. image:: https://img.shields.io/pypi/pyversions/cs.svg
   :alt: Python versions
   :target: https://pypi.org/project/cs/

A simple, yet powerful CloudStack API client for python and the command-line.

* Async support.
* All present and future CloudStack API calls and parameters are supported.
* Syntax highlight in the command-line client if Pygments is installed.
* BSD license.

Installation
------------

::

    pip install cs

    # with the colored output
    pip install cs[highlight]

    # with the async support
    pip install cs[async]

    # with both
    pip install cs[async,highlight]

Usage
-----

In Python:

.. code-block:: python

    from cs import CloudStack

    cs = CloudStack(endpoint='https://cloudstack.example.com/client/api',
                    key='cloudstack api key',
                    secret='cloudstack api secret')

    vms = cs.listVirtualMachines()

    cs.createSecurityGroup(name='web', description='HTTP traffic')

From the command-line, this requires some configuration:

.. code-block:: console

    cat $HOME/.cloudstack.ini

.. code-block:: ini

    [cloudstack]
    endpoint = https://cloudstack.example.com/client/api
    key = cloudstack api key
    secret = cloudstack api secret
    # Optional ca authority certificate
    verify = /path/to/certs/ca.crt
    # Optional client PEM certificate
    cert = /path/to/client.pem
    # If you need to pass the certificate and key as separate files
    cert_key = /path/to/client_key.pem

Then:

.. code-block:: console

    $ cs listVirtualMachines

.. code-block:: json

    {
      "count": 1,
      "virtualmachine": [
        {
          "account": "...",
          ...
        }
      ]
    }

.. code-block:: console

    $ cs authorizeSecurityGroupIngress \
        cidrlist="0.0.0.0/0" endport=443 startport=443 \
        securitygroupname="blah blah" protocol=tcp

The command-line client polls when async results are returned. To disable
polling, use the ``--async`` flag.

To find the list CloudStack API calls go to
http://cloudstack.apache.org/api.html

Configuration
-------------

Configuration is read from several locations, in the following order:

* The ``CLOUDSTACK_ENDPOINT``, ``CLOUDSTACK_KEY``, ``CLOUDSTACK_SECRET`` and
  ``CLOUDSTACK_METHOD`` environment variables,
* A ``CLOUDSTACK_CONFIG`` environment variable pointing to an ``.ini`` file,
* A ``CLOUDSTACK_VERIFY`` (optional) environment variable pointing to a CA authority cert file,
* A ``CLOUDSTACK_CERT`` (optional) environment variable pointing to a client PEM cert file,
* A ``CLOUDSTACK_CERT_KEY`` (optional) environment variable pointing to a client PEM certificate key file,
* A ``cloudstack.ini`` file in the current working directory,
* A ``.cloudstack.ini`` file in the home directory.

To use that configuration scheme from your Python code:

.. code-block:: python

    from cs import CloudStack, read_config

    cs = CloudStack(**read_config())

Note that ``read_config()`` can raise ``SystemExit`` if no configuration is
found.

``CLOUDSTACK_METHOD`` or the ``method`` entry in the configuration file can be
used to change the HTTP verb used to make CloudStack requests. By default,
requests are made with the GET method but CloudStack supports POST requests.
POST can be useful to overcome some length limits in the CloudStack API.

``CLOUDSTACK_TIMEOUT`` or the ``timeout`` entry in the configuration file can
be used to change the HTTP timeout when making CloudStack requests (in
seconds). The default value is 10.

``CLOUDSTACK_RETRY`` or the ``retry`` entry in the configuration file
(integer) can be used to retry ``list`` and ``queryAsync`` requests on
failure. The default value is 0, meaning no retry.

``CLOUDSTACK_JOB_TIMEOUT`` or the `job_timeout`` entry in the configuration file
(float) can be used to set how long an async call is retried assuming ``fetch_result`` is set to true). The default value is ``None``, it waits forever.

``CLOUDSTACK_POLL_INTERVAL`` or the ``poll_interval`` entry in the configuration file (number of seconds, float) can be used to set how frequently polling an async job result is done. The default value is 2.

``CLOUDSTACK_EXPIRATION`` or the ``expiration`` entry in the configuration file
(integer) can be used to set how long a signature is valid. By default, it picks
10 minutes but may be deactivated using any negative value, e.g. -1.

``CLOUDSTACK_DANGEROUS_NO_TLS_VERIFY`` or the ``dangerous_no_tls_verify`` entry
in the configuration file (boolean) can be used to deactivate the TLS verification
made when using the HTTPS protocol.

Multiple credentials can be set in ``.cloudstack.ini``. This allows selecting
the credentials or endpoint to use with a command-line flag.

.. code-block:: ini

    [cloudstack]
    endpoint = https://some-host/api/v1
    key = api key
    secret = api secret

    [region-example]
    endpoint = https://cloudstack.example.com/client/api
    key = api key
    secret = api secret

Usage::

    $ cs listVirtualMachines --region=region-example

Optionally ``CLOUDSTACK_REGION`` can be used to overwrite the default region ``cloudstack``.

For the power users that don't want to put any secrets on disk,
``CLOUDSTACK_OVERRIDES`` let you pick which key will be set from the
environment even if present in the ini file.


Pagination
----------

CloudStack paginates requests. ``cs`` is able to abstract away the pagination
logic to allow fetching large result sets in one go. This is done with the
``fetch_list`` parameter::

    $ cs listVirtualMachines fetch_list=true

Or in Python::

    cs.listVirtualMachines(fetch_list=True)

Tracing HTTP requests
---------------------

Once in a while, it could be useful to understand, see what HTTP calls are made
under the hood. The ``trace`` flag (or ``CLOUDSTACK_TRACE``) does just that::

   $ cs --trace listVirtualMachines

   $ cs -t listZones

Async client
------------

``cs`` provides the ``AIOCloudStack`` class for async/await calls in Python
3.5+.

.. code-block:: python

    import asyncio
    from cs import AIOCloudStack, read_config

    cs = AIOCloudStack(**read_config())

    async def main():
       vms = await cs.listVirtualMachines(fetch_list=True)
       print(vms)

    asyncio.run(main())

Async deployment of multiple VMs
________________________________

.. code-block:: python

    import asyncio
    from cs import AIOCloudStack, read_config

    cs = AIOCloudStack(**read_config())

    machine = {"zoneid": ..., "serviceofferingid": ..., "templateid": ...}

    async def main():
       tasks = asyncio.gather(*(cs.deployVirtualMachine(name=f"vm-{i}",
                                                        **machine,
                                                        fetch_result=True)
                                for i in range(5)))

       results = await tasks

       # Destroy all of them, but skip waiting on the job results
       await asyncio.gather(*(cs.destroyVirtualMachine(id=result['virtualmachine']['id'])
                              for result in results))

    asyncio.run(main())

Release Procedure
-----------------

.. code-block:: shell-session

    mktmpenv -p /usr/bin/python3
    pip install -U twine wheel build
    cd ./cs
    rm -rf build dist
    python -m build
    twine upload dist/*

Links
-----

* CloudStack API: http://cloudstack.apache.org/api.html
