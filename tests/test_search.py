import re
import httpx
import pytest
import test_tui
from datetime import UTC, datetime
from CB.Search import SearchResult, identity_key

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
    return str(httpx.URL(f'https://api.github.com/repos/{full_name}/releases', params={'per_page': 30}))


@pytest.fixture
def free(router):
    def publish(query, wowi=(), repos=(), releases=None):
        router.add(FILELIST, list(wowi))
        router.add(search_url(query), {'items': list(repos)})
        for full_name, release in (releases or {}).items():
            router.add(releases_url(full_name), release)
    return publish


def release_with(*assets, **flags):
    return {'tag_name': 'v1', 'draft': False, 'prerelease': False, 'assets': list(assets), **flags}


def hosts(router):
    return [request.url.host for request in router.requests]


def test_no_key_prints_wowi_and_github_results(tui, router, free):
    free('bar', wowi=[wowi_entry(7, 'Bar Frames', day='2024-03-05')],
         repos=[gh_repo('owner/BarAddon')],
         releases={'owner/BarAddon': [release_with({'digest': 'sha256:abc'})]})
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


@pytest.mark.parametrize(('description', 'topics', 'kept'), [
    ('Blender addon', [], False),
    ('Blender addon', ['blender'], False),
    ('World of Warcraft addon', [], True),
    (None, ['warcraft-addon'], True),
    ('Tweaks (WoW Addon)', [], True),
])
def test_github_filter_requires_a_wow_mention(tui, free, description, topics, kept):
    free('bar', repos=[gh_repo('owner/Bar', description=description, topics=topics)],
         releases={'owner/Bar': []})
    tui.c_search('bar')
    assert ('gh:owner/Bar' in output(tui)) is kept


def test_topics_match_when_description_does_not(tui, free):
    free('bar', repos=[gh_repo('owner/Topical', description=None, topics=['warcraft'])])
    tui.c_search('bar')
    assert 'gh:owner/Topical' in output(tui)


@pytest.mark.parametrize(('release', 'status', 'marker'), [
    ([release_with({'digest': 'sha256:abc'})], 200, ' [verified]'),
    ([release_with({'digest': None})], 200, ' [unverified]'),
    ([], 200, ' [no release]'),
    ({'message': 'boom'}, 500, ''),
    ({'message': 'not a list'}, 200, ''),
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


@pytest.mark.parametrize('url', ['wowi:100', 'https://www.wowinterface.com/downloads/info100-Foo.html'])
def test_installed_wowi_addon_is_marked(tui, wowi, free, url):
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'})
    tui.core.add_addon(url, False)
    free('addon', wowi=[wowi_entry(100, 'Addon100'), wowi_entry(101, 'Addon101')])
    tui.c_search('addon')
    lines = output(tui).splitlines()
    assert any(line.startswith('wowi:100') and line.endswith('[Installed]') for line in lines)
    assert any(line.startswith('wowi:101') and 'Installed' not in line for line in lines)


@pytest.mark.parametrize('url', ['gh:owner/repo', 'https://github.com/owner/repo'])
def test_installed_github_addon_is_marked_ignoring_case(tui, github, free, url):
    github('owner/repo', 'v1.0', {'Repo/Repo.toc': 'x'})
    tui.core.add_addon(url, False)
    free('repo', repos=[gh_repo('Owner/Repo'), gh_repo('Other/Repo')],
         releases={'Owner/Repo': [release_with({'digest': 'sha256:abc'})], 'Other/Repo': []})
    tui.c_search('repo')
    lines = output(tui).splitlines()
    assert any(line.startswith('gh:Owner/Repo') and line.endswith('[Installed]') for line in lines)
    assert any(line.startswith('gh:Other/Repo') and 'Installed' not in line for line in lines)


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


def test_remote_dates_print_literally(tui, free):
    free('bar', repos=[gh_repo('owner/Bar', pushed_at='[/]x'), gh_repo('owner/Baz', pushed_at='2025-02-03[/]')],
         releases={'owner/Bar': [], 'owner/Baz': []})
    tui.c_search('bar')
    text = output(tui)
    assert 'gh:owner/Bar Bar GitHub  [no release]' in text
    assert 'gh:owner/Baz Baz GitHub 2025-02-03 [no release]' in text


def test_line_escapes_every_remote_field(tui):
    result = SearchResult('gh:[a]', 'https://github.com/x/y', '[b]', '[c]', '[d]', 'e')
    tui.console.print(tui.free_search_line(result, set()), highlight=False, emoji=False)
    assert 'gh:[a] [b] [c] [d] [e]' in output(tui)


def test_control_characters_are_stripped_from_remote_names(tui, free):
    free('bar', wowi=[wowi_entry(7, 'Bar\x1b[2JFrames\x07')], repos=[gh_repo('owner/Ba\x1br\x00')],
         releases={'owner/Bar': []})
    tui.c_search('bar')
    text = output(tui)
    assert 'wowi:7 Bar[2JFrames WoWInterface' in text
    assert 'gh:owner/Bar Bar GitHub' in text
    assert not any(char in text for char in '\x1b\x07\x00')


def test_wowi_names_are_unescaped_and_matched_unescaped(tui, free):
    free('new & improved', wowi=[wowi_entry(7, 'Bar new &amp; improved'), wowi_entry(8, 'Bar new amp improved')])
    tui.c_search('new & improved')
    lines = [line for line in output(tui).splitlines() if line.startswith('wowi:')]
    assert lines == ['wowi:7 Bar new & improved WoWInterface 2024-03-05']


def test_release_check_failure_adds_one_note(tui, router, free):
    free('bar', repos=[gh_repo('owner/A'), gh_repo('owner/B')])
    router.add(releases_url('owner/A'), {'message': 'limit'}, status=403)
    router.add(releases_url('owner/B'), {'message': 'limit'}, status=403)
    tui.c_search('bar')
    text = output(tui)
    assert text.count('GitHub release checks failed') == 1
    assert ('GitHub release checks failed (HTTP 403); some results have no marker. '
            'Setting gh_api raises the rate limit.') in text


def test_hint_is_omitted_when_a_token_is_set(tui, router, free):
    free('bar', repos=[gh_repo('owner/A')])
    router.add(releases_url('owner/A'), {'message': 'limit'}, status=403)
    tui.core.config['GHAPIKey'] = 'secret'
    tui.c_search('bar')
    text = output(tui)
    assert 'GitHub release checks failed (HTTP 403); some results have no marker.' in text
    assert 'gh_api' not in text


def test_rejected_token_is_reported_for_search_and_release_checks(tui, router, free):
    free('bar', wowi=[wowi_entry(7, 'Bar Frames')])
    router.add(search_url('bar'), {'message': 'bad credentials'}, status=401)
    tui.core.config['GHAPIKey'] = 'secret'
    tui.c_search('bar')
    text = output(tui)
    assert 'GitHub search failed (HTTP 401). GitHub rejected the gh_api token; check set gh_api.' in text
    assert 'raises the rate limit' not in text
    router.add(search_url('baz'), {'items': [gh_repo('owner/Baz')]})
    router.add(releases_url('owner/Baz'), {'message': 'bad credentials'}, status=401)
    tui.c_search('baz')
    assert ('GitHub release checks failed (HTTP 401); some results have no marker. '
            'GitHub rejected the gh_api token; check set gh_api.') in output(tui)


def test_hint_is_omitted_for_other_statuses(tui, router, free):
    free('bar', wowi=[wowi_entry(7, 'Bar Frames')], repos=[gh_repo('owner/A')])
    router.add(releases_url('owner/A'), {'message': 'boom'}, status=500)
    router.add(search_url('bar'), {'message': 'boom'}, status=502)
    tui.c_search('bar')
    text = output(tui)
    assert 'GitHub search failed (HTTP 502).' in text
    assert 'gh_api' not in text


def test_search_failure_hint_is_omitted_when_a_token_is_set(tui, router, free):
    free('bar', wowi=[wowi_entry(7, 'Bar Frames')])
    router.add(search_url('bar'), {'message': 'limit'}, status=403)
    tui.core.config['GHAPIKey'] = 'secret'
    tui.c_search('bar')
    text = output(tui)
    assert 'GitHub search failed (HTTP 403).' in text
    assert 'gh_api' not in text


def test_empty_digest_is_unverified(tui, free):
    free('bar', repos=[gh_repo('owner/Bar')], releases={'owner/Bar': [release_with({'digest': 'sha256:'})]})
    tui.c_search('bar')
    assert 'gh:owner/Bar Bar GitHub 2025-01-02 [unverified]\n' in output(tui)


def test_marker_uses_the_release_the_installer_would_pick(tui, free):
    asset = {'digest': 'sha256:abc'}
    releases = [release_with(asset, prerelease=True), release_with(asset, draft=True), release_with(),
                release_with({'digest': None}), release_with(asset)]
    free('bar', repos=[gh_repo('owner/Bar')], releases={'owner/Bar': releases})
    tui.c_search('bar')
    assert 'gh:owner/Bar Bar GitHub 2025-01-02 [unverified]\n' in output(tui)


def test_prerelease_first_list_marks_from_the_stable_release(tui, free):
    releases = [release_with({'digest': None}, prerelease=True), release_with({'digest': 'sha256:abc'})]
    free('bar', repos=[gh_repo('owner/Bar')], releases={'owner/Bar': releases})
    tui.c_search('bar')
    assert 'gh:owner/Bar Bar GitHub 2025-01-02 [verified]\n' in output(tui)


def test_only_assetless_releases_mean_no_release(tui, free):
    free('bar', repos=[gh_repo('owner/Bar')], releases={'owner/Bar': [release_with(), release_with()]})
    tui.c_search('bar')
    assert 'gh:owner/Bar Bar GitHub 2025-01-02 [no release]\n' in output(tui)


def test_invalid_file_list_is_reported_and_not_cached(tui, router, free):
    router.add(FILELIST, {'ERROR': 'x'})
    router.add(search_url('bar'), {'items': [gh_repo('owner/Bar')]})
    router.add(releases_url('owner/Bar'), [])
    tui.c_search('bar')
    assert 'WoWInterface search failed (invalid response).' in output(tui)
    assert tui.core.freeSearch.fileList is None
    router.add(FILELIST, [wowi_entry(7, 'Bar Frames')])
    tui.c_search('bar')
    assert 'wowi:7 Bar Frames' in output(tui)


def test_invalid_file_list_alone_raises(tui, router):
    router.add(FILELIST, {'ERROR': 'x'})
    router.add(search_url('bar'), {'message': 'down'}, status=500)
    with pytest.raises(RuntimeError, match=r'WoWInterface search failed \(invalid response\)\.'):
        tui.c_search('bar')


def test_file_list_entries_with_the_wrong_shape_are_skipped(tui, free):
    broken = [None, 'x', [], {'UID': '1'}, {**wowi_entry(2, 'Bar'), 'UIDate': 'yesterday'},
              {**wowi_entry(4, 'Bar'), 'UIDownloadTotal': 'many'},
              {**wowi_entry(5, 'Bar'), 'UIName': None}, {**wowi_entry(6, 'Bar'), 'UIDate': 10 ** 30},
              {**wowi_entry(8, 'Bar'), 'UID': 'x[/]'}, {**wowi_entry(9, 'Bar'), 'UID': '\u00b2'},
              {**wowi_entry(10, 'Bar'), 'UIDownloadTotal': '\u00b2'}, {**wowi_entry(11, 'Bar'), 'UID': '9' * 5000},
              {**wowi_entry(12, 'Bar'), 'UIDownloadTotal': '9' * 5000}, {**wowi_entry(13, 'Bar'), 'UID': True},
              {**wowi_entry(14, 'Bar'), 'UIDownloadTotal': -1}, {**wowi_entry(15, 'Bar'), 'UID': '1\n'}]
    free('bar', wowi=[*broken, wowi_entry(7, 'Bar Frames')])
    tui.c_search('bar')
    lines = [line for line in output(tui).splitlines() if line.startswith('wowi:')]
    assert [line.split()[0] for line in lines] == ['wowi:7']


def test_integer_uid_and_download_total_are_accepted(tui, free):
    entry = {**wowi_entry(7, 'Bar Frames'), 'UID': 7, 'UIDownloadTotal': 12}
    free('bar', wowi=[entry])
    tui.c_search('bar')
    assert 'wowi:7 Bar Frames WoWInterface 2024-03-05' in output(tui)


def test_far_future_date_is_formatted(tui, free):
    entry = {**wowi_entry(7, 'Bar Frames'), 'UIDate': 253402300799999}
    free('bar', wowi=[entry])
    tui.c_search('bar')
    assert 'wowi:7 Bar Frames WoWInterface 9999-12-31' in output(tui)


def test_file_list_without_a_valid_entry_is_reported_and_not_cached(tui, router, free):
    router.add(FILELIST, [{'UID': 'x'}, None])
    router.add(search_url('bar'), {'items': [gh_repo('owner/Bar')]})
    router.add(releases_url('owner/Bar'), [])
    tui.c_search('bar')
    assert 'WoWInterface search failed (invalid response).' in output(tui)
    assert tui.core.freeSearch.fileList is None


def test_empty_file_list_is_cached(tui, free):
    free('bar')
    tui.c_search('bar')
    assert tui.core.freeSearch.fileList == []


@pytest.mark.parametrize('payload', [[], {'message': 'x'}, {'items': {}}, {'items': 'x'}])
def test_search_payload_without_items_is_reported(tui, router, free, payload):
    router.add(FILELIST, [wowi_entry(7, 'Bar Frames')])
    router.add(search_url('bar'), payload)
    tui.c_search('bar')
    text = output(tui)
    assert 'wowi:7 Bar Frames' in text
    assert 'GitHub search failed (invalid response).' in text


def test_search_items_with_the_wrong_shape_are_skipped(tui, free):
    items = [None, 'x', {'name': 'NoFullName', 'description': 'addon'}, {'full_name': 5, 'description': 'addon'},
             {'full_name': 'owner/Bad', 'description': 5, 'topics': 'warcraft'}, gh_repo('owner/Good')]
    free('bar', repos=items, releases={'owner/Good': []})
    tui.c_search('bar')
    lines = [line for line in output(tui).splitlines() if line.startswith('gh:')]
    assert [line.split()[0] for line in lines] == ['gh:owner/Good']


def test_releases_payload_that_is_not_a_list_is_a_failed_check(tui, router, free):
    free('bar', repos=[gh_repo('owner/Bar')])
    router.add(releases_url('owner/Bar'), {'message': 'x'})
    tui.c_search('bar')
    text = output(tui)
    assert 'gh:owner/Bar Bar GitHub 2025-01-02\n' in text
    assert 'GitHub release checks failed (invalid response); some results have no marker.' in text


def test_free_search_only_issues_get_requests_of_the_expected_shapes(tui, router, free):
    free('bar', wowi=[wowi_entry(7, 'Bar Frames')], repos=[gh_repo('owner/Bar'), gh_repo('owner/Baz')],
         releases={'owner/Bar': [], 'owner/Baz': []})
    tui.c_search('bar')
    shapes = [re.compile(re.escape(FILELIST) + '$'),
              re.compile(re.escape(GITHUB_SEARCH) + r'\?'),
              re.compile(r'https://api\.github\.com/repos/[^/?]+/[^/?]+/releases\?per_page=30$')]
    assert len(router.requests) == 4
    for request in router.requests:
        assert request.method == 'GET'
        assert any(shape.match(str(request.url)) for shape in shapes)


def test_invalid_url_error_is_a_failed_source(tui, router, free):
    free('bar', wowi=[wowi_entry(7, 'Bar Frames')])

    def fail(request):
        if request.url.host == 'api.github.com':
            raise httpx.InvalidURL('bad')
        return router(request)
    tui.core.http = httpx.Client(transport=httpx.MockTransport(fail))
    tui.c_search('bar')
    text = output(tui)
    assert 'wowi:7 Bar Frames' in text
    assert 'GitHub search failed (InvalidURL).' in text


def test_deeply_nested_json_is_an_invalid_response(tui, router, free):
    free('bar', wowi=[wowi_entry(7, 'Bar Frames')])
    router.add(search_url('bar'), b'[' * 100000 + b']' * 100000)
    tui.c_search('bar')
    text = output(tui)
    assert 'wowi:7 Bar Frames' in text
    assert 'GitHub search failed (invalid response).' in text


def test_query_is_url_encoded_in_the_search_request(tui, router):
    router.add(FILELIST, [])
    tui.c_search('a&b #c "d"')
    urls = [str(request.url) for request in router.requests]
    assert ('https://api.github.com/search/repositories'
            '?q=a%26b+%23c+%22d%22+in%3Aname%2Cdescription&per_page=30') in urls


@pytest.mark.parametrize(('url', 'key'), [
    ('https://www.wowinterface.com/downloads/info5086-BigWigs.html', 'wowi:5086'),
    ('https://www.wowinterface.com/downloads/info5086.html', 'wowi:5086'),
    ('https://github.com/BigWigsMods/BigWigs', 'gh:bigwigsmods/bigwigs'),
    ('https://example.invalid/Addon', 'https://example.invalid/Addon'),
])
def test_identity_key(url, key):
    assert identity_key(url) == key
