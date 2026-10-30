import re
import httpx
from datetime import UTC, datetime
from dataclasses import dataclass
from . import APIAuth, Verify

WOWI_FILELIST_URL = 'https://api.mmoui.com/v3/game/WOW/filelist.json'
GITHUB_SEARCH_URL = 'https://api.github.com/search/repositories'
WOWI_LIMIT = 10
GITHUB_LIMIT = 5
GITHUB_PER_PAGE = 30
WOW_PATTERN = re.compile(r'wow|warcraft|addon', re.I)
GITHUB_HINT = 'Setting gh_api raises the rate limit.'


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
    except RuntimeError as e:
        return [], str(e)


def is_wow_repository(item):
    topics = item.get('topics') or []
    return any(WOW_PATTERN.search(text) for text in [item.get('description') or '', *topics])


def wowi_result(entry):
    updated = datetime.fromtimestamp(entry['UIDate'] / 1000, UTC).strftime('%Y-%m-%d')
    return SearchResult(f'wowi:{entry["UID"]}', f'https://www.wowinterface.com/downloads/info{entry["UID"]}.html',
                        entry['UIName'], 'WoWInterface', updated)


def release_marker(releases):
    if not releases:
        return 'no release'
    if any(Verify.parse_digest(asset.get('digest')) for asset in releases[0].get('assets') or []):
        return 'verified'
    return 'unverified'


class FreeSearch:
    def __init__(self, http):
        self.http = http
        self.fileList = None

    def get_json(self, label, url, **kwargs):
        try:
            response = self.http.get(url, **kwargs)
        except httpx.HTTPError as e:
            raise RuntimeError(f'{label} search failed ({e.__class__.__name__}).') from e
        if response.status_code != 200:
            raise RuntimeError(f'{label} search failed (HTTP {response.status_code}).')
        try:
            return response.json()
        except ValueError as e:
            raise RuntimeError(f'{label} search failed (invalid response).') from e

    def search(self, query, token):
        wowinterface, wowinterface_error = attempt(self.wowinterface, query)
        github, github_error = attempt(self.github, query, token)
        if wowinterface_error and github_error:
            raise RuntimeError(f'{wowinterface_error}\n{github_error} {GITHUB_HINT}')
        notes = [note for note in (wowinterface_error, github_error and f'{github_error} {GITHUB_HINT}') if note]
        return SearchOutcome(wowinterface + github, notes)

    def wowinterface(self, query):
        if self.fileList is None:
            self.fileList = self.get_json('WoWInterface', WOWI_FILELIST_URL)
        words = query.lower().split()
        matches = [entry for entry in self.fileList if all(word in entry['UIName'].lower() for word in words)]
        matches.sort(key=lambda entry: int(entry['UIDownloadTotal']), reverse=True)
        return [wowi_result(entry) for entry in matches[:WOWI_LIMIT]]

    def github(self, query, token):
        auth = APIAuth('Bearer', token)
        payload = self.get_json('GitHub', GITHUB_SEARCH_URL, auth=auth,
                                params={'q': f'{query} in:name,description', 'per_page': GITHUB_PER_PAGE})
        items = [item for item in payload['items'] if is_wow_repository(item)][:GITHUB_LIMIT]
        return [self.github_result(item, auth) for item in items]

    def github_result(self, item, auth):
        full_name = item['full_name']
        return SearchResult(f'gh:{full_name}', f'https://github.com/{full_name}', item['name'], 'GitHub',
                            (item.get('pushed_at') or '')[:10], self.github_marker(full_name, auth))

    def github_marker(self, full_name, auth):
        try:
            releases = self.get_json('GitHub releases', f'https://api.github.com/repos/{full_name}/releases',
                                     auth=auth, params={'per_page': 1})
        except RuntimeError:
            return None
        return release_marker(releases)
