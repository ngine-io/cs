import argparse
import json
import os
import sys
from collections import defaultdict
from configparser import NoSectionError

try:
    import pygments
    from pygments.lexers import JsonLexer
    from pygments.styles import get_style_by_name
    from pygments.formatters import Terminal256Formatter
except ImportError:
    pygments = None

from .client import (
    CloudStack,
    CloudStackApiException,
    CloudStackException,
    read_config,
)
from .version import __version__


__all__ = [
    "read_config",
    "CloudStack",
    "CloudStackException",
    "CloudStackApiException",
]

try:
    import aiohttp  # noqa
except ImportError:
    pass
else:
    from ._async import AIOCloudStack  # noqa

    __all__.append("AIOCloudStack")


def _format_json(data, theme):
    """Pretty print a dict as a JSON, with colors if pygments is present."""
    output = json.dumps(data, indent=2, sort_keys=True)

    if pygments and sys.stdout.isatty():
        style = get_style_by_name(theme)
        formatter = Terminal256Formatter(style=style)
        return pygments.highlight(output, JsonLexer(), formatter)

    return output


def main(args=None):
    parser = argparse.ArgumentParser(description="Cloudstack client.")
    parser.add_argument(
        "--region",
        "-r",
        metavar="REGION",
        help="Cloudstack region in ~/.cloudstack.ini",
        default=os.environ.get("CLOUDSTACK_REGION", "cloudstack"),
    )
    parser.add_argument(
        "--theme",
        metavar="THEME",
        help="Pygments style",
        default=os.environ.get("CLOUDSTACK_THEME", "default"),
    )
    parser.add_argument(
        "--post",
        action="store_true",
        default=False,
        help="use POST instead of GET",
    )
    parser.add_argument(
        "--async",
        action="store_true",
        default=False,
        help="do not wait for async result",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        default=False,
        help="do not display additional status messages",
    )
    parser.add_argument(
        "--trace",
        "-t",
        action="store_true",
        default=os.environ.get("CLOUDSTACK_TRACE", False),
        help="trace the HTTP requests done on stderr",
    )
    parser.add_argument(
        "command",
        metavar="COMMAND",
        help="Cloudstack API command to execute",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=__version__,
    )

    def parse_option(x):
        if "=" not in x:
            raise ValueError(
                "{!r} is not a correctly formatted " "option".format(x)
            )
        return x.split("=", 1)

    parser.add_argument(
        "arguments",
        metavar="OPTION=VALUE",
        nargs="*",
        type=parse_option,
        help="Cloudstack API argument",
    )

    options = parser.parse_args(args=args)

    command = options.command
    kwargs = defaultdict(set)
    for arg in options.arguments:
        key, value = arg
        kwargs[key].add(value.strip(" \"'"))

    try:
        config = read_config(ini_group=options.region)
    except NoSectionError:
        raise SystemExit("Error: region '%s' not in config" % options.region)

    theme = config.pop("theme", "default")

    fetch_result = "Async" not in command and not getattr(options, "async")

    if options.post:
        config["method"] = "post"

    if options.trace:
        config["trace"] = True

    cs = CloudStack(**config)
    ok = True
    response = None

    try:
        response = getattr(cs, command)(fetch_result=fetch_result, **kwargs)
    except CloudStackException as e:
        ok = False
        if e.response is not None:
            if not options.quiet:
                sys.stderr.write("CloudStack error: ")
                sys.stderr.write("\n".join((str(arg) for arg in e.args)))
                sys.stderr.write("\n")

            try:
                response = json.loads(e.response.text)
            except ValueError:
                sys.stderr.write(e.response.text)
                sys.stderr.write("\n")
        else:
            message, data = (e.args[0], e.args[0:])
            sys.stderr.write("Error: {0}\n{1}\n".format(message, data))

    if response:
        sys.stdout.write(_format_json(response, theme=theme))
        sys.stdout.write("\n")

    return not ok
