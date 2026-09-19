"""Fetch a URL and return readable text (no external HTML-parsing deps)."""
from __future__ import annotations

import urllib.error
import urllib.request
from html.parser import HTMLParser

MAX_CHARS = 20_000
SKIP_TAGS = {"script", "style", "noscript", "svg", "head"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self.chunks.append(text)


def fetch_url(url: str) -> str:
    if not (url.startswith("http://") or url.startswith("https://")):
        return "ERROR: url must start with http:// or https://"

    req = urllib.request.Request(url, headers={"User-Agent": "termagent/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            content_type = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return f"ERROR: HTTP {e.code} fetching {url}"
    except urllib.error.URLError as e:
        return f"ERROR: could not reach {url}: {e.reason}"

    text = raw.decode("utf-8", errors="replace")

    if "html" in content_type or text.lstrip().startswith("<"):
        parser = _TextExtractor()
        parser.feed(text)
        result = "\n".join(parser.chunks)
    else:
        result = text

    if len(result) > MAX_CHARS:
        result = result[:MAX_CHARS] + "\n... [truncated]"
    return result or "(no readable text content found)"
