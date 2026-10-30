import io
import json
import stat
import httpx
import hashlib
import zipfile
import pytest
from pathlib import Path
from rich.console import Console
from CB.Core import Core
from CurseBreaker import TUI


def make_zip(files, symlinks=()):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        for name, target in symlinks:
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, target)
    return buffer.getvalue()


def md5(content):
    return hashlib.md5(content).hexdigest()


def sha256(content):
    return hashlib.sha256(content).hexdigest()


class Router:
    def __init__(self):
        self.routes = {}
        self.failures = {}
        self.requests = []

    def add(self, url, content, status=200, headers=None):
        if not isinstance(content, bytes):
            content = json.dumps(content).encode()
        self.routes[url] = (status, content, headers or {})

    def fail(self, url, error):
        self.failures[url] = error

    def __call__(self, request):
        self.requests.append(request)
        url = str(request.url)
        if url in self.failures:
            raise self.failures[url]
        if url not in self.routes:
            return httpx.Response(404, content=b'not found')
        status, content, headers = self.routes[url]
        return httpx.Response(status, content=content, headers=headers)


@pytest.fixture
def router():
    return Router()


@pytest.fixture
def http(router):
    with httpx.Client(transport=httpx.MockTransport(router), follow_redirects=True) as client:
        yield client


@pytest.fixture
def client_dir(tmp_path, monkeypatch):
    (tmp_path / 'Interface' / 'AddOns').mkdir(parents=True)
    (tmp_path / 'WTF').mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('CURSEBREAKER_INSECURE_SKIP_VERIFY', raising=False)
    monkeypatch.delenv('CURSEBREAKER_DEBUG', raising=False)
    return tmp_path


@pytest.fixture
def addons(client_dir):
    return client_dir / 'Interface' / 'AddOns'


@pytest.fixture
def core(client_dir, http):
    instance = Core()
    instance.http.close()
    instance.http = http
    instance.clientType = 'retail'
    instance.masterConfig = {'ClientTypes': {'retail': {'CurrentVersion': 110200}},
                             'CustomRepository': {'raw:dev': {'Repository': 'owner/Raw', 'Branch': 'main',
                                                              'Name': 'Raw', 'Directories': ['Raw'],
                                                              'Authors': ['owner']}}}
    instance.init_config()
    return instance


@pytest.fixture
def tui(core):
    instance = object.__new__(TUI)
    instance.core = core
    instance.console = Console(record=True, width=200, color_system=None)
    instance.headless = False
    instance.os = 'Darwin'
    instance.setup_table()
    return instance


def output(tui):
    return tui.console.export_text(clear=False)


def wowi_payload(uid, version, url, uimd5, pending='0'):
    return [{'UID': uid, 'UIName': f'Addon{uid}', 'UIDownload': url, 'UIVersion': version, 'UIMD5': uimd5,
             'UIAuthorName': 'author', 'UIPending': pending}]


@pytest.fixture
def wowi(router):
    def publish(uid, version, files, uimd5=True, content=None):
        url = f'https://cdn.wowinterface.com/downloads/file{uid}/{version}.zip'
        content = content if content is not None else make_zip(files)
        checksum = md5(content) if uimd5 is True else uimd5
        router.add(f'https://api.mmoui.com/v3/game/WOW/filedetails/{uid}.json',
                   wowi_payload(uid, version, url, checksum))
        router.add(url, content)
        return content
    return publish


def gh_release(repo, tag, content, digest=True, asset_id=1):
    name = repo.split('/')[1]
    return {'tag_name': tag, 'name': tag, 'html_url': f'https://github.com/{repo}/releases/tag/{tag}',
            'draft': False, 'prerelease': False,
            'assets': [{'node_id': f'RA_{asset_id}', 'name': f'{name}-{tag}.zip', 'content_type': 'application/zip',
                        'url': f'https://api.github.com/repos/{repo}/releases/assets/{asset_id}',
                        'digest': f'sha256:{sha256(content)}' if digest is True else digest}]}


@pytest.fixture
def github(router):
    def publish(repo, tag, files, digest=True, content=None):
        content = content if content is not None else make_zip(files)
        release = gh_release(repo, tag, content, digest)
        router.add(f'https://api.github.com/repos/{repo}/releases', [release])
        router.add(release['assets'][0]['url'], content)
        return content, release
    return publish


def tree(path):
    return sorted(str(p.relative_to(path)) for p in Path(path).rglob('*'))
