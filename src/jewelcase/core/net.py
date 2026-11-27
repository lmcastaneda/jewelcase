"""The few network requests Jewelcase makes: anonymous, verified, and only when the user asks."""
from __future__ import annotations

import http.client
import ssl
import urllib.request


def request(url: str) -> urllib.request.Request:
    """A generic client name and nothing about the user or the machine."""
    return urllib.request.Request(url, headers={"User-Agent": "jewelcase", "Accept": "application/json, */*"})


def context() -> ssl.SSLContext:
    """System certificates, plus the bundled set so HTTPS works where the system paths differ."""
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except (ImportError, OSError):
        pass
    return ctx


class HttpsOnly(urllib.request.HTTPRedirectHandler):
    """A redirect may not lead away from https (urllib would follow http:// and other schemes)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise OSError("a redirect away from https was refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(HttpsOnly, urllib.request.HTTPSHandler(context=context()))


def open_url(url: str, timeout: float = 60, opener=None):
    """`opener` (anything with .open(request, timeout=)) is for tests; the real one checks certificates."""
    if not url.startswith("https://"):
        raise OSError("only https downloads are allowed")
    return (opener or build_opener()).open(request(url), timeout=timeout)


def probe(url: str = "https://api.github.com/zen", timeout: float = 15) -> str:
    """'ok' or a short reason; used by the self-test to show HTTPS works on this system."""
    try:
        with open_url(url, timeout) as r:
            return "ok" if r.status == 200 else f"http {r.status}"
    except Exception as e:      # reported, never fatal
        return f"failed ({type(e).__name__})"


def fetch_bytes(url: str, limit: int, timeout: float = 30, opener=None) -> bytes | None:
    """The body of a page (at most `limit` + 1 bytes are read), or None when the server says 404. A body
    shorter than the server announced is an OSError: the download was cut."""
    import urllib.error
    try:
        with open_url(url, timeout, opener) as r:
            body = r.read(limit + 1)
            announced = r.headers.get("Content-Length") if getattr(r, "headers", None) is not None else None
            if announced and announced.isdigit() and len(body) < min(int(announced), limit + 1):
                raise OSError("the download was cut short")
            return body
    except http.client.HTTPException as e:
        raise OSError(str(e) or type(e).__name__) from e
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise OSError(f"http {e.code}") from e
    except urllib.error.URLError as e:
        raise OSError(str(e.reason)) from e
