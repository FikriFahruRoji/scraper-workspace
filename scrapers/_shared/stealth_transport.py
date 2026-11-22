"""Small HTTP-shaped adapter whose requests execute inside Scrapling Chromium.

Shared by the saham, playstore and youtube scrapers. No HTTP fallback.
Each instance belongs to one worker thread and owns an ephemeral browser profile.
"""
from __future__ import annotations
import json as json_module
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit, urlunsplit
from scrapling.fetchers import StealthySession

_FETCH = """async ({url, method, headers, body, timeout}) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(url, {method, headers, body,
      credentials: 'include', signal: controller.signal});
    return {status: response.status, url: response.url, text: await response.text()};
  } finally { clearTimeout(timer); }
}"""

@dataclass
class BrowserResponse:
    status: int
    url: str
    text: str

    @property
    def status_code(self):
        return self.status

    @property
    def body(self):
        return self.text.encode('utf-8')

    def json(self):
        return json_module.loads(self.text)

class StealthTransport:
    def __init__(self, *, headless=True, real_chrome=True, timeout=30, **_compat):
        self.headers = {}
        self.timeout = timeout
        self._session = StealthySession(headless=headless, real_chrome=real_chrome,
            locale='en-US', timezone_id='Asia/Jakarta', retries=1,
            timeout=int(timeout * 1000), load_dom=False, network_idle=False)
        self._page = None
        self._origin = None
        self._started = False

    def __enter__(self):
        self._session.__enter__()
        self._started = True
        return self

    def close(self):
        if self._started:
            self._started = False
            self._session.__exit__(None, None, None)
        self._page = None
        self._origin = None

    def __exit__(self, *args):
        self.close()

    def request(self, method, url, *, params=None, headers=None, data=None,
                json=None, timeout=None, **_compat):
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.hostname:
            raise ValueError('Transport requires an HTTP(S) URL')
        if parts.username or parts.password:
            raise ValueError('Credentials in URLs are not supported')
        if params:
            query = '&'.join(x for x in (parts.query, urlencode(params)) if x)
            url = urlunsplit(parts._replace(query=query))
        if not self._started:
            self.__enter__()
        origin = f'{parts.scheme}://{parts.netloc}'
        if self._origin != origin or self._page is None:
            # Start on the same origin so browser fetch obeys normal CORS rules.
            self._page = None
            self._session.fetch(origin + '/', google_search=False,
                page_action=lambda page: setattr(self, '_page', page))
            if self._page is None:
                raise RuntimeError(f'Browser page unavailable for {origin}')
            landed = urlsplit(self._page.url)
            if (landed.scheme, landed.netloc) != (parts.scheme, parts.netloc):
                self._page = None
                raise RuntimeError(f'Browser was redirected away from {origin}')
            self._origin = origin
        merged = {**self.headers, **(headers or {})}
        merged = {k: v for k, v in merged.items()
                  if k.lower() not in ('origin', 'referer', 'host', 'cookie', 'user-agent')}
        if json is not None:
            data = json_module.dumps(json)
            merged['Content-Type'] = 'application/json'
        if isinstance(data, bytes):
            data = data.decode('utf-8')
        if isinstance(data, dict):
            data = urlencode(data)
        result = self._page.evaluate(_FETCH, dict(url=url, method=method,
            headers=merged, body=data, timeout=int((timeout or self.timeout) * 1000)))
        return BrowserResponse(result['status'], result['url'], result['text'])

    def get(self, url, **kwargs):
        return self.request('GET', url, **kwargs)

    def post(self, url, **kwargs):
        return self.request('POST', url, **kwargs)
