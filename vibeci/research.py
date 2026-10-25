"""Opt-in, tokenless web research with strict URL and response bounds.

Search uses an explicitly configured SearXNG JSON endpoint. Fetch reads only
allowlisted hosts. Returned text is always untrusted evidence and is never
executed or inserted into trusted instructions.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:token|password|secret|api[_ -]?key|private[_ -]?key)\s*[:=]\s*\S+|-----BEGIN [A-Z ]*PRIVATE KEY-----",
)
_SENSITIVE_QUERY_KEYS = {"access_token", "api_key", "apikey", "key", "password", "secret", "signature", "token"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, _req, _fp, _code, _msg, _headers, _newurl):
        return None


@dataclass(frozen=True)
class FetchedPage:
    url: str
    content_type: str
    text: str
    untrusted: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "content_type": self.content_type,
            "text": self.text,
            "untrusted": self.untrusted,
        }


class WebResearchClient:
    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        if not settings.get("enabled", False):
            raise ValueError("web research is disabled in .agents/settings.yml")
        if settings.get("provider") != "searxng":
            raise ValueError("only the reviewed searxng provider is supported")
        self.timeout = float(settings.get("timeout_seconds", 15))
        self.maximum_bytes = int(settings.get("maximum_response_bytes", 500_000))
        self.maximum_results = int(settings.get("maximum_results", 10))
        if not 1 <= self.timeout <= 60 or not 1_000 <= self.maximum_bytes <= 2_000_000:
            raise ValueError("web research timeout or response bound is outside policy")
        if not 1 <= self.maximum_results <= 25:
            raise ValueError("web research result limit must be 1-25")
        hosts = settings.get("fetch_allowed_hosts", [])
        if not isinstance(hosts, list) or any(not isinstance(host, str) or not host for host in hosts):
            raise ValueError("fetch_allowed_hosts must be a list of host names")
        self.fetch_allowed_hosts = {host.casefold().rstrip(".") for host in hosts}
        self.allow_loopback = settings.get("allow_loopback") is True
        endpoint = settings.get("searxng_url", "")
        if not isinstance(endpoint, str) or not endpoint:
            raise ValueError("research.searxng_url must name a reviewed endpoint")
        self.searxng_url = self._validated_url(endpoint, {urllib.parse.urlsplit(endpoint).hostname or ""})
        if urllib.parse.urlsplit(self.searxng_url).query:
            raise ValueError("research.searxng_url cannot contain a query string")
        self._opener = urllib.request.build_opener(_NoRedirect)

    def _validated_url(self, value: str, allowed_hosts: set[str]) -> str:
        try:
            parsed = urllib.parse.urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise ValueError(f"invalid research URL: {exc}") from exc
        host = (parsed.hostname or "").casefold().rstrip(".")
        loopback = host in {"127.0.0.1", "localhost", "::1"}
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError("research URLs cannot contain credentials or fragments")
        query_keys = {key.casefold() for key, _value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)}
        if query_keys & _SENSITIVE_QUERY_KEYS or _SENSITIVE_TEXT.search(urllib.parse.unquote_plus(parsed.query)):
            raise ValueError("research URLs cannot contain credential-like query parameters")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback and self.allow_loopback):
            raise ValueError("research URLs require HTTPS; HTTP is allowed only for configured loopback tests")
        if not host or host not in {item.casefold().rstrip(".") for item in allowed_hosts}:
            raise ValueError(f"research host is not allowlisted: {host or '(missing)'}")
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("research URL port is outside 1-65535")
        return urllib.parse.urlunsplit(parsed)

    def _read(self, url: str, *, accept: str) -> tuple[str, str]:
        request = urllib.request.Request(  # noqa: S310
            url,
            headers={
                "Accept": accept,
                "User-Agent": "VibeCI/0.1 tokenless-research",
            },
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                body = response.read(self.maximum_bytes + 1)
                content_type = response.headers.get_content_type()
                charset = response.headers.get_content_charset() or "utf-8"
        except urllib.error.HTTPError as exc:
            redirect_note = "; redirects are not followed" if 300 <= exc.code < 400 else ""
            raise ValueError(f"research endpoint returned HTTP {exc.code}{redirect_note}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ValueError(f"research request failed: {exc}") from exc
        if len(body) > self.maximum_bytes:
            raise ValueError(f"research response exceeds {self.maximum_bytes} bytes")
        try:
            return body.decode(charset), content_type
        except (LookupError, UnicodeDecodeError) as exc:
            raise ValueError("research response is not decodable text") from exc

    def search(self, query: str, limit: int | None = None) -> list[dict[str, str]]:
        clean_query = " ".join(query.strip().split())
        if not clean_query or len(clean_query) > 500:
            raise ValueError("search query must contain 1-500 characters")
        if _SENSITIVE_TEXT.search(clean_query):
            raise ValueError("search query appears to contain a credential; remove it before network research")
        selected_limit = self.maximum_results if limit is None else min(limit, self.maximum_results)
        if selected_limit < 1:
            raise ValueError("search result limit must be positive")
        base = self.searxng_url.rstrip("/")
        endpoint = base if base.endswith("/search") else base + "/search"
        separator = "&" if urllib.parse.urlsplit(endpoint).query else "?"
        url = endpoint + separator + urllib.parse.urlencode({"q": clean_query, "format": "json"})
        raw, content_type = self._read(url, accept="application/json")
        if content_type not in {"application/json", "text/json"}:
            raise ValueError(f"SearXNG returned unexpected content type: {content_type}")
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("SearXNG returned invalid JSON") from exc
        results = document.get("results") if isinstance(document, dict) else None
        if not isinstance(results, list):
            raise ValueError("SearXNG JSON does not contain a results list")
        normalized: list[dict[str, str]] = []
        for item in results:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                continue
            normalized.append({
                "title": str(item.get("title", ""))[:300],
                "url": item["url"][:2000],
                "content": str(item.get("content", ""))[:1000],
                "engine": str(item.get("engine", ""))[:80],
                "trust": "untrusted_search_result",
            })
            if len(normalized) >= selected_limit:
                break
        return normalized

    def fetch(self, url: str) -> FetchedPage:
        validated = self._validated_url(url, self.fetch_allowed_hosts)
        text, content_type = self._read(
            validated,
            accept="text/plain,text/markdown,text/html,application/json;q=0.9",
        )
        if not (
            content_type.startswith("text/")
            or content_type in {"application/json", "application/xml", "application/xhtml+xml"}
        ):
            raise ValueError(f"web fetch rejected non-text content type: {content_type}")
        return FetchedPage(validated, content_type, text)
