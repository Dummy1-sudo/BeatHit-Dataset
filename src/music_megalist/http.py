from __future__ import annotations
import hashlib, json, time
from pathlib import Path
from typing import Any
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

class CachedHttp:
    def __init__(self, cache_dir: str | Path = ".cache/http", min_interval: float = 0.35):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.client = httpx.Client(timeout=45, follow_redirects=True, headers={
            "User-Agent": "music-megalist-dataset/0.1 (public research dataset; cached requests)"
        })
        self.min_interval = min_interval
        self._last = 0.0

    def _key(self, method: str, url: str, params: Any = None, payload: Any = None) -> Path:
        raw = json.dumps([method, url, params, payload], sort_keys=True, ensure_ascii=False, default=str).encode()
        return self.cache_dir / (hashlib.sha256(raw).hexdigest() + ".json")

    def _pace(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last = time.monotonic()

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=1, max=30),
           retry=retry_if_exception_type((httpx.HTTPError, TimeoutError)))
    def get_json(self, url: str, params: dict[str, Any] | None = None, *, refresh: bool = False) -> Any:
        p = self._key("GET", url, params)
        if p.exists() and not refresh:
            return json.loads(p.read_text("utf-8"))
        self._pace()
        r = self.client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
        p.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        return data

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=1, max=30),
           retry=retry_if_exception_type((httpx.HTTPError, TimeoutError)))
    def post_json(self, url: str, payload: dict[str, Any], *, refresh: bool = False) -> Any:
        p = self._key("POST", url, payload=payload)
        if p.exists() and not refresh:
            return json.loads(p.read_text("utf-8"))
        self._pace()
        r = self.client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
        p.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        return data

    def get_text(self, url: str, *, refresh: bool = False) -> str:
        p = self._key("GET_TEXT", url)
        if p.exists() and not refresh:
            return json.loads(p.read_text("utf-8"))["text"]
        self._pace()
        r = self.client.get(url)
        r.raise_for_status()
        text = r.text
        p.write_text(json.dumps({"text": text}, ensure_ascii=False), "utf-8")
        return text
