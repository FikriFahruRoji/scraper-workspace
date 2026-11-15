"""Play Store parsing with browser-only network transport (parser 1.2.7)."""
import json
from google_play_scraper.constants.element import ElementSpecs
from google_play_scraper.constants.regex import Regex
from google_play_scraper.constants.request import Formats
from google_play_scraper.features.app import parse_dom
from google_play_scraper.exceptions import NotFoundError
from stealth_transport import StealthTransport

class PlayStealthClient:
    def __init__(self, *, real_chrome=True):
        self.transport = StealthTransport(real_chrome=real_chrome)

    def __enter__(self):
        self.transport.__enter__()
        return self

    def __exit__(self, *args):
        self.transport.__exit__(*args)

    def _text(self, response):
        if response.status == 404:
            raise NotFoundError('App not found (404).')
        if response.status != 200:
            raise RuntimeError(f'Play Store HTTP {response.status}')
        if 'com.google.play.gateway.proto.PlayGatewayError' in response.text:
            raise RuntimeError('Play Store gateway rejected the request')
        return response.text

    def app(self, app_id, *, lang, country):
        url = Formats.Detail.build(app_id=app_id, lang=lang, country=country)
        text = self._text(self.transport.get(url))
        if not Regex.SCRIPT.findall(text):
            raise RuntimeError('Play Store metadata missing; response may be a consent or login page')
        return parse_dom(text, app_id, url)

    def reviews(self, app_id, *, lang, country, sort, count,
                continuation_token=None, filter_score_with=None):
        url = Formats.Reviews.build(lang=lang, country=country)
        body = Formats.Reviews.build_body(app_id, sort.value, count,
            'null' if filter_score_with is None else filter_score_with,
            'null', continuation_token)
        text = self._text(self.transport.post(url, data=body,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}))
        matches = Regex.REVIEWS.findall(text)
        if not matches:
            raise ValueError('Unrecognized Play Store review response')
        envelope = json.loads(matches[0])
        data = json.loads(envelope[0][2])
        try:
            token = data[-2][-1]
        except (IndexError, TypeError):
            token = None
        if not isinstance(token, str) or not token:
            token = None
        items = data[0] if data and isinstance(data[0], list) else []
        rows = [{key: spec.extract_content(item)
                 for key, spec in ElementSpecs.Review.items()} for item in items]
        return rows, token
