"""The network helper: anonymous, https only (also after a redirect), bounded, and checked for short reads."""
import http.client
import io
import urllib.error
import urllib.request

import pytest

from jewelcase.core import net


class Reply(io.BytesIO):
    def __init__(self, body=b"", length=None, status=200):
        super().__init__(body)
        self.headers = {} if length is None else {"Content-Length": str(length)}
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Opener:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.requests = reply, error, []

    def open(self, request, timeout=None):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.reply


def test_a_request_says_only_that_it_is_jewelcase():
    opener = Opener(Reply(b"x"))
    net.fetch_bytes("https://example.org/a.png", 10, opener=opener)
    assert opener.requests[0].get_header("User-agent") == "jewelcase"


def test_only_https_is_asked_for():
    opener = Opener(Reply(b"x"))
    for url in ("http://example.org/a", "file:///etc/passwd", "ftp://example.org/a"):
        with pytest.raises(OSError):
            net.fetch_bytes(url, 10, opener=opener)
    assert opener.requests == []


def test_a_redirect_that_leaves_https_is_refused():
    handler = net.HttpsOnly()
    request = urllib.request.Request("https://example.org/a")
    for target in ("http://example.org/b", "ftp://example.org/b", "file:///etc/passwd"):
        with pytest.raises(OSError):
            handler.redirect_request(request, None, 302, "Found", {}, target)
    assert handler.redirect_request(request, None, 302, "Found", {}, "https://example.org/b") is not None


def test_the_real_opener_refuses_redirects_out_of_https():
    assert any(isinstance(h, net.HttpsOnly) for h in net.build_opener().handlers)


def test_no_more_than_the_limit_plus_one_byte_is_read():
    body = net.fetch_bytes("https://example.org/a", 5, opener=Opener(Reply(b"0123456789")))
    assert body == b"012345"


def test_a_404_is_none_and_other_http_errors_are_oserrors():
    def error(code):
        return urllib.error.HTTPError("https://example.org/a", code, "x", {}, None)
    assert net.fetch_bytes("https://example.org/a", 5, opener=Opener(error=error(404))) is None
    for code in (429, 503):
        with pytest.raises(OSError, match=str(code)):
            net.fetch_bytes("https://example.org/a", 5, opener=Opener(error=error(code)))


def test_fewer_bytes_than_the_server_announced_is_a_failed_download():
    with pytest.raises(OSError):
        net.fetch_bytes("https://example.org/a", 100, opener=Opener(Reply(b"abc", length=10)))
    assert net.fetch_bytes("https://example.org/a", 100, opener=Opener(Reply(b"abc", length=3))) == b"abc"


def test_a_connection_cut_in_the_body_is_an_oserror():
    class Cut(Reply):
        def read(self, n=-1):
            raise http.client.IncompleteRead(b"ab", 5)
    with pytest.raises(OSError):
        net.fetch_bytes("https://example.org/a", 100, opener=Opener(Cut()))
