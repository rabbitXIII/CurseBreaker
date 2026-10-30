import re
import html
import httpx
from datetime import UTC, datetime, timedelta
from dataclasses import dataclass
from . import APIAuth, Verify

WOWI_FILELIST_URL = 'https://api.mmoui.com/v3/game/WOW/filelist.json'
GITHUB_SEARCH_URL = 'https://api.github.com/search/repositories'
WOWI_LIMIT = 10
GITHUB_LIMIT = 5
GITHUB_PER_PAGE = 30
RELEASES_PER_PAGE = 30
MAX_EPOCH_MS = 253402300800000
RATE_LIMIT_STATUSES = (403, 429)
UNAUTHORIZED_STATUS = 401
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
WOW_PATTERN = re.compile(r'\bwow|warcraft', re.I)
COUNT_LIMIT = 10 ** 18
COUNT_PATTERN = re.compile(r'[0-9]{1,18}')
DATE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}')
CONTROL_PATTERN = re.compile(r'[\x00-\x1f\x7f]')
GITHUB_HINT = 'Setting gh_api raises the rate limit.'
REJECTED_HINT = 'GitHub rejected the gh_api token; check set gh_api.'


class SearchError(RuntimeError):
    def __init__(self, label, reason, status=None):
        super().__init__(f'{label} search failed ({reason}).')
        self.reason = reason
        self.status = status


@dataclass
class SearchResult:
    slug: str
    url: str
    name: str
    source: str
    updated: str
    marker: str | None = None


@dataclass
class SearchOutcome:
    results: list[SearchResult]
    notes: list[str]


def attempt(method, *args):
    try:
        return method(*args), None
    except SearchError as e:
        return SearchOutcome([], []), e


def with_hint(text, error, token):
    if token and error.status == UNAUTHORIZED_STATUS:
        return f'{text} {REJECTED_HINT}'
    if not token and error.status in RATE_LIMIT_STATUSES:
        return f'{text} {GITHUB_HINT}'
    return text


def release_check_note(failures, token):
    text = f'GitHub release checks failed ({failures[0].reason}); some results have no marker.'
    return with_hint(text, failures[0], token)


def clean_text(text):
    return CONTROL_PATTERN.sub('', text)


def identity_key(url):
    if 'wowinterface.com/downloads/' in url and (uid := re.search(r'\d+', url)):
        return f'wowi:{uid.group()}'
    if url.startswith('https://github.com/'):
        return f'gh:{url.replace("https://github.com/", "").casefold()}'
    return url


def installed_keys(addons):
    return {identity_key(addon['URL']) for addon in addons}


def is_wow_repository(item):
    topics = item.get('topics')
    texts = [item.get('description'), *(topics if isinstance(topics, list) else [])]
    return any(WOW_PATTERN.search(text) for text in texts if isinstance(text, str))


def is_count(value):
    if isinstance(value, int) and not isinstance(value, bool):
        return 0 <= value < COUNT_LIMIT
    return isinstance(value, str) and COUNT_PATTERN.fullmatch(value) is not None


def is_listed_file(entry):
    return (isinstance(entry, dict) and isinstance(entry.get('UIName'), str)
            and is_count(entry.get('UID')) and is_count(entry.get('UIDownloadTotal'))
            and isinstance(entry.get('UIDate'), int) and 0 <= entry['UIDate'] < MAX_EPOCH_MS)


def listed_file(entry):
    return {'UID': str(entry['UID']), 'UIName': clean_text(html.unescape(entry['UIName'])),
            'UIDate': entry['UIDate'], 'UIDownloadTotal': int(entry['UIDownloadTotal'])}


def is_listed_repository(item):
    return isinstance(item, dict) and isinstance(item.get('full_name'), str)


def pushed_date(value):
    return value[:10] if isinstance(value, str) and DATE_PATTERN.match(value) else ''


def wowi_result(entry):
    updated = (EPOCH + timedelta(milliseconds=entry['UIDate'])).strftime('%Y-%m-%d')
    return SearchResult(f'wowi:{entry["UID"]}', f'https://www.wowinterface.com/downloads/info{entry["UID"]}.html',
                        entry['UIName'], 'WoWInterface', updated)


def is_installable(release):
    return (isinstance(release, dict) and isinstance(release.get('assets'), list) and bool(release['assets'])
            and not release.get('draft') and not release.get('prerelease'))


def has_digest(asset):
    digest = asset.get('digest') if isinstance(asset, dict) else None
    parsed = Verify.parse_digest(digest) if isinstance(digest, str) else None
    return bool(parsed and parsed[1])


def release_marker(releases):
    installable = [release for release in releases if is_installable(release)]
    if not installable:
        return 'no release'
    if any(has_digest(asset) for asset in installable[0]['assets']):
        return 'verified'
    return 'unverified'


class FreeSearch:
    def __init__(self, http):
        self.http = http
        self.fileList = None

    def get_json(self, label, url, **kwargs):
        try:
            response = self.http.get(url, **kwargs)
        except (httpx.HTTPError, httpx.InvalidURL) as e:
            raise SearchError(label, e.__class__.__name__) from e
        if response.status_code != 200:
            raise SearchError(label, f'HTTP {response.status_code}', response.status_code)
        try:
            return response.json()
        except (ValueError, RecursionError) as e:
            raise SearchError(label, 'invalid response') from e

    def search(self, query, token):
        wowinterface, wowinterface_error = attempt(self.wowinterface, query)
        github, github_error = attempt(self.github, query, token)
        errors = [wowinterface_error and str(wowinterface_error),
                  github_error and with_hint(str(github_error), github_error, token)]
        failures = [text for text in errors if text]
        if wowinterface_error and github_error:
            raise RuntimeError('\n'.join(failures))
        return SearchOutcome(wowinterface.results + github.results, failures + wowinterface.notes + github.notes)

    def wowinterface(self, query):
        if self.fileList is None:
            payload = self.get_json('WoWInterface', WOWI_FILELIST_URL)
            if not isinstance(payload, list):
                raise SearchError('WoWInterface', 'invalid response')
            listed = [listed_file(entry) for entry in payload if is_listed_file(entry)]
            if payload and not listed:
                raise SearchError('WoWInterface', 'invalid response')
            self.fileList = listed
        words = query.lower().split()
        matches = [entry for entry in self.fileList if all(word in entry['UIName'].lower() for word in words)]
        matches.sort(key=lambda entry: entry['UIDownloadTotal'], reverse=True)
        return SearchOutcome([wowi_result(entry) for entry in matches[:WOWI_LIMIT]], [])

    def github(self, query, token):
        auth = APIAuth('Bearer', token)
        payload = self.get_json('GitHub', GITHUB_SEARCH_URL, auth=auth,
                                params={'q': f'{query} in:name,description', 'per_page': GITHUB_PER_PAGE})
        if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
            raise SearchError('GitHub', 'invalid response')
        items = [item for item in payload['items'] if is_listed_repository(item) and is_wow_repository(item)]
        failures = []
        results = [self.github_result(item, auth, failures) for item in items[:GITHUB_LIMIT]]
        notes = [release_check_note(failures, token)] if failures else []
        return SearchOutcome(results, notes)

    def github_result(self, item, auth, failures):
        full_name = clean_text(item['full_name'])
        name = clean_text(str(item.get('name') or full_name))
        return SearchResult(f'gh:{full_name}', f'https://github.com/{full_name}', name, 'GitHub',
                            pushed_date(item.get('pushed_at')), self.github_marker(full_name, auth, failures))

    def github_marker(self, full_name, auth, failures):
        try:
            return release_marker(self.releases(full_name, auth))
        except SearchError as e:
            failures.append(e)
            return None

    def releases(self, full_name, auth):
        releases = self.get_json('GitHub releases', f'https://api.github.com/repos/{full_name}/releases',
                                 auth=auth, params={'per_page': RELEASES_PER_PAGE})
        if not isinstance(releases, list):
            raise SearchError('GitHub releases', 'invalid response')
        return releases
