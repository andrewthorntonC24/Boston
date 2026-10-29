"""Small HTTP layer: retries, timeouts, one User-Agent, and error messages that never contain
query strings (which can hold API keys)."""
import time
from urllib.parse import urlsplit

import requests

from .config import USER_AGENT

_session = requests.Session()


class SourceError(Exception):
    """A data source failed in a way the pipeline should handle (stale data, alert)."""


def _clean(url):
    p = urlsplit(url)
    return "%s://%s%s" % (p.scheme, p.netloc, p.path)


def _request(url, params=None, headers=None, timeout=30, retries=3, stream=False):
    h = {"User-Agent": USER_AGENT}
    h.update(headers or {})
    last = "unknown error"
    for attempt in range(retries):
        try:
            r = _session.get(url, params=params, headers=h, timeout=timeout, stream=stream)
        except requests.RequestException as e:
            last = "%s: %s" % (type(e).__name__, str(e).split("\n")[0][:120])
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 410:
            raise SourceError("%s returned 410 (endpoint retired)" % _clean(url))
        if r.status_code in (429, 500, 502, 503, 504):
            last = "HTTP %s" % r.status_code
            time.sleep(2 ** attempt)
            continue
        if r.status_code in (401, 403):
            raise SourceError("HTTP %s from %s (the service rejected the API key: check the secret's name and value, "
                              "and that it is the key itself with no extra spaces)" % (r.status_code, _clean(url)))
        if r.status_code >= 400:
            raise SourceError("HTTP %s from %s" % (r.status_code, _clean(url)))
        return r
    raise SourceError("%s failed after %d tries (%s)" % (_clean(url), retries, last))


def get_json(url, params=None, headers=None, timeout=30):
    r = _request(url, params, headers, timeout)
    try:
        return r.json()
    except ValueError:
        raise SourceError("%s did not return JSON" % _clean(url))


def get_text(url, params=None, headers=None, timeout=30):
    return _request(url, params, headers, timeout).text


def iter_lines(url, params=None, headers=None, timeout=120):
    """Stream a large CSV/text download line by line (used for the crime file).
    This is a generator on purpose: the generator's frame keeps the response object alive until the last
    line is read. (An earlier version returned a wrapper around response.raw, and the connection was
    closed before reading finished: "I/O operation on closed file".)"""
    r = _request(url, params, headers, timeout, stream=True)
    r.encoding = "utf-8-sig"          # strips a leading BOM; undecodable bytes are replaced, not fatal
    try:
        for line in r.iter_lines(decode_unicode=True):
            if line:
                yield line
    finally:
        r.close()
