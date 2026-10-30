import httpx
import pytest
import test_tui
from datetime import UTC, datetime

tui = test_tui.tui
output = test_tui.output

FILELIST = 'https://api.mmoui.com/v3/game/WOW/filelist.json'
GITHUB_SEARCH = 'https://api.github.com/search/repositories'


def epoch_ms(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)


def wowi_entry(uid, name, downloads=1, day='2024-03-05'):
    return {'UID': str(uid), 'UIName': name, 'UIAuthorName': 'author', 'UIDownloadTotal': str(downloads),
            'UIDate': epoch_ms(day), 'UIVersion': '1.0', 'UIFileInfoURL': f'https://example.invalid/{uid}'}


def gh_repo(full_name, description='A WoW addon', topics=(), pushed_at='2025-01-02T03:04:05Z'):
    return {'full_name': full_name, 'name': full_name.split('/')[1], 'description': description,
            'topics': list(topics), 'pushed_at': pushed_at}


def search_url(query, per_page=30):
    return str(httpx.URL(GITHUB_SEARCH, params={'q': f'{query} in:name,description', 'per_page': per_page}))


def releases_url(full_name):
    return str(httpx.URL(f'https://api.github.com/repos/{full_name}/releases', params={'per_page': 1}))


@pytest.fixture
def free(router):
    def publish(query, wowi=(), repos=(), releases=None):
        router.add(FILELIST, list(wowi))
        router.add(search_url(query), {'items': list(repos)})
        for full_name, release in (releases or {}).items():
            router.add(releases_url(full_name), release)
    return publish


def release_with(*assets):
    return [{'tag_name': 'v1', 'assets': list(assets)}]


def hosts(router):
    return [request.url.host for request in router.requests]


def test_no_key_prints_wowi_and_github_results(tui, router, free):
    free('bar', wowi=[wowi_entry(7, 'Bar Frames', day='2024-03-05')],
         repos=[gh_repo('owner/BarAddon')],
         releases={'owner/BarAddon': release_with({'digest': 'sha256:abc'})})
    tui.c_search('bar')
    text = output(tui)
    assert 'Top results of your search:' in text
    assert 'wowi:7 Bar Frames WoWInterface 2024-03-05' in text
    assert 'gh:owner/BarAddon BarAddon GitHub 2025-01-02 [verified]' in text
    assert 'addons.wago.io' not in hosts(router)


def test_file_list_fetched_once_across_searches(tui, router, free):
    free('bar', wowi=[wowi_entry(7, 'Bar Frames')])
    router.add(search_url('baz'), {'items': []})
    tui.c_search('bar')
    tui.c_search('baz')
    assert [str(r.url) for r in router.requests].count(FILELIST) == 1


def test_wowi_match_is_case_insensitive_all_words_ranked_and_capped(tui, free):
    entries = [wowi_entry(100 + i, f'Big Wigs {i}', downloads=i) for i in range(12)]
    entries += [wowi_entry(1, 'BIGWIGS'), wowi_entry(2, 'wigs only'), wowi_entry(3, 'big only')]
    free('wIgS bIg', wowi=entries)
    tui.c_search('wIgS bIg')
    lines = [line for line in output(tui).splitlines() if line.startswith('wowi:')]
    assert [line.split()[0] for line in lines] == [f'wowi:{100 + i}' for i in range(11, 1, -1)]


def test_github_filter_drops_non_addons_caps_at_five_and_keeps_order(tui, free):
    repos = [gh_repo('bio/bigWig', description='bigWig files for genomics')]
    repos += [gh_repo(f'owner/Addon{i}') for i in range(7)]
    repos += [gh_repo('owner/Topical', description=None, topics=['warcraft'])]
    free('bigwig', repos=repos)
    tui.c_search('bigwig')
    lines = [line for line in output(tui).splitlines() if line.startswith('gh:')]
    assert [line.split()[0] for line in lines] == [f'gh:owner/Addon{i}' for i in range(5)]


def test_topics_match_when_description_does_not(tui, free):
    free('bar', repos=[gh_repo('owner/Topical', description=None, topics=['warcraft'])])
    tui.c_search('bar')
    assert 'gh:owner/Topical' in output(tui)


@pytest.mark.parametrize(('release', 'status', 'marker'), [
    (release_with({'digest': 'sha256:abc'}), 200, ' [verified]'),
    (release_with({'digest': None}), 200, ' [unverified]'),
    ([], 200, ' [no release]'),
    ({'message': 'boom'}, 500, ''),
])
def test_digest_markers(tui, router, free, release, status, marker):
    free('bar', repos=[gh_repo('owner/Bar')])
    router.add(releases_url('owner/Bar'), release, status=status)
    tui.c_search('bar')
    assert f'gh:owner/Bar Bar GitHub 2025-01-02{marker}\n' in output(tui)


def test_github_token_sent_as_authorization_header(tui, router, free):
    free('bar', repos=[gh_repo('owner/Bar')], releases={'owner/Bar': []})
    tui.core.config['GHAPIKey'] = 'secret'
    tui.c_search('bar')
    github = [r for r in router.requests if r.url.host == 'api.github.com']
    assert len(github) == 2
    assert all(r.headers['Authorization'] == 'Bearer secret' for r in github)


def test_no_authorization_header_without_token(tui, router, free):
    free('bar', repos=[gh_repo('owner/Bar')], releases={'owner/Bar': []})
    tui.c_search('bar')
    github = [r for r in router.requests if r.url.host == 'api.github.com']
    assert len(github) == 2
    assert all('Authorization' not in r.headers for r in github)


def test_github_rate_limit_keeps_wowi_results_and_adds_note(tui, router, free):
    free('bar', wowi=[wowi_entry(7, 'Bar Frames')])
    router.add(search_url('bar'), {'message': 'rate limited'}, status=403)
    tui.c_search('bar')
    text = output(tui)
    assert 'wowi:7 Bar Frames' in text
    assert 'GitHub search failed (HTTP 403). Setting gh_api raises the rate limit.' in text


def test_github_transport_error_keeps_wowi_results(tui, router, free):
    free('bar', wowi=[wowi_entry(7, 'Bar Frames')])

    def fail(request):
        if request.url.host == 'api.github.com':
            raise httpx.ConnectError('down')
        return router(request)
    tui.core.http = httpx.Client(transport=httpx.MockTransport(fail))
    tui.c_search('bar')
    text = output(tui)
    assert 'wowi:7 Bar Frames' in text
    assert 'GitHub search failed (ConnectError).' in text


def test_wowi_failure_keeps_github_results_and_adds_note(tui, router, free):
    free('bar', repos=[gh_repo('owner/Bar')], releases={'owner/Bar': []})
    router.add(FILELIST, {'ERROR': 'down'}, status=500)
    tui.c_search('bar')
    text = output(tui)
    assert 'gh:owner/Bar' in text
    assert 'WoWInterface search failed (HTTP 500).' in text


def test_both_sources_failing_raises_with_both_reasons(tui, router):
    router.add(FILELIST, {}, status=500)
    router.add(search_url('bar'), {}, status=429)
    with pytest.raises(RuntimeError) as error:
        tui.c_search('bar')
    assert 'WoWInterface search failed (HTTP 500).' in str(error.value)
    assert 'GitHub search failed (HTTP 429).' in str(error.value)


def test_installed_wowi_addon_is_marked(tui, wowi, free):
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'})
    tui.core.add_addon('wowi:100', False)
    free('addon', wowi=[wowi_entry(100, 'Addon100'), wowi_entry(101, 'Addon101')])
    tui.c_search('addon')
    lines = output(tui).splitlines()
    assert any(line.startswith('wowi:100') and line.endswith('[Installed]') for line in lines)
    assert any(line.startswith('wowi:101') and 'Installed' not in line for line in lines)


def test_installed_github_addon_is_marked(tui, github, free):
    github('owner/Repo', 'v1.0', {'Repo/Repo.toc': 'x'})
    tui.core.add_addon('gh:owner/Repo', False)
    free('repo', repos=[gh_repo('owner/Repo')], releases={'owner/Repo': release_with({'digest': 'sha256:abc'})})
    tui.c_search('repo')
    assert any(line.startswith('gh:owner/Repo') and line.endswith('[Installed]') for line in output(tui).splitlines())


def test_wago_key_uses_wago_search_and_no_free_requests(tui, router):
    tui.core.config['WAAAPIKey'] = 'key'
    router.add('https://addons.wago.io/api/external/addons/_search?query=bar&game_version=retail',
               {'data': [{'website_url': 'https://addons.wago.io/addons/bar'}]})
    tui.c_search('bar')
    assert 'https://addons.wago.io/addons/bar' in output(tui)
    assert hosts(router) == ['addons.wago.io']


def test_remote_names_print_literally(tui, free):
    name = '[bold]Bar[/bold] :smile:'
    free('bar', wowi=[wowi_entry(7, name)], repos=[gh_repo('owner/[red]x')],
         releases={'owner/[red]x': []})
    tui.c_search('bar')
    text = output(tui)
    assert f'wowi:7 {name} WoWInterface' in text
    assert 'gh:owner/[red]x [red]x GitHub' in text


