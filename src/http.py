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


def open_text(url, params=None, headers=None, timeout=120):
    """Open a large text/CSV download as a text stream (used for the crime CSV). Handles gzip and
    quoted newlines correctly because the csv module reads the stream directly."""
    import io
    r = _request(url, params, headers, timeout, stream=True)
    r.raw.decode_content = True
    return io.TextIOWrapper(r.raw, encoding="utf-8-sig", newline="")
