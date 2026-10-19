"""Small, dependency-free public metadata HTTP client with replayable raw cache."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class FetchError(RuntimeError):
    def __init__(self, message, *, http_status=None):
        super().__init__(message)
        self.http_status = http_status


class HTTPClient:
    def __init__(self, cache_dir, *, refresh=False, delay=1, timeout=60, retries=4):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.refresh, self.delay, self.timeout, self.retries = refresh, delay, timeout, retries
        self._lock = threading.Lock()
        self._last_request = 0.0
        self.requests = 0
        self.cache_hits = 0

    def get(self, url):
        key = hashlib.sha256(url.encode()).hexdigest()
        raw = self.cache_dir / (key + ".raw")
        metadata = self.cache_dir / (key + ".json")
        if raw.exists() and metadata.exists() and not self.refresh:
            self.cache_hits += 1
            return raw.read_bytes(), json.loads(metadata.read_text(encoding="utf-8"))
        last_error, last_http_status = None, None
        for attempt in range(self.retries):
            try:
                with self._lock:
                    remaining = self.delay - (time.monotonic() - self._last_request)
                    if remaining > 0:
                        time.sleep(remaining)
                    self._last_request = time.monotonic()
                    self.requests += 1
                request = Request(url, headers={"User-Agent": "RLDMJC-PreprintLibrary/1.0 (metadata-only; public research library)", "Accept": "application/json, application/atom+xml, */*"})
                with urlopen(request, timeout=self.timeout) as response:
                    content = response.read()
                    meta = {"url": url, "fetched_at": utc_now(), "status": response.status,
                            "content_type": response.headers.get("Content-Type"),
                            "sha256": hashlib.sha256(content).hexdigest()}
                temporary = raw.with_suffix(".tmp")
                temporary.write_bytes(content)
                temporary.replace(raw)
                write_json(metadata, meta)
                return content, meta
            except (HTTPError, URLError, TimeoutError, OSError) as exc:
                last_error = str(exc)
                last_http_status = exc.code if isinstance(exc, HTTPError) else None
                if isinstance(exc, HTTPError) and exc.code not in (408, 429, 500, 502, 503, 504):
                    break
                retry_after = exc.headers.get("Retry-After", "") if isinstance(exc, HTTPError) else ""
                time.sleep(min(60, int(retry_after) if retry_after.isdigit() else 2 ** (attempt + 1)))
        raise FetchError(f"{url}: {last_error}", http_status=last_http_status)

    def json(self, url):
        raw, meta = self.get(url)
        try:
            return json.loads(raw), meta
        except (ValueError, UnicodeError) as exc:
            raise FetchError(f"Invalid JSON at {url}: {exc}") from exc
