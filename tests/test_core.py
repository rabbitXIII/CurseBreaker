import json
import httpx
import pytest
from CB import Verify, retry
from conftest import make_zip, sha256, gh_release, tree

WOWI = 'https://www.wowinterface.com/downloads/info100.html'
GH = 'https://github.com/owner/Repo'


def saved_config(client_dir):
    return json.loads((client_dir / 'WTF' / 'CurseBreaker.json').read_text())


def test_new_config_has_protected_directories(core):
    assert core.config['ProtectedDirectories'] == []


def test_wowi_md5_verified_install(core, wowi, addons, client_dir):
    content = wowi(100, '1.0', {'Foo/Foo.toc': 'x', 'Foo/a.lua': 'a', 'README.txt': 'loose'})
    assert core.add_addon('wowi:100', False) == (True, 'Addon100', '1.0')
    assert tree(addons) == ['Foo', 'Foo/Foo.toc', 'Foo/a.lua']
    entry = saved_config(client_dir)['Addons'][0]
    assert entry['Lock'] == {'Version': '1.0', 'SHA256': sha256(content), 'Verified': 'md5', 'Directories': ['Foo']}
    assert core.verification == [('Addon100', 'md5', True)]


def test_wowi_md5_mismatch_installs_nothing(core, wowi, addons):
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'}, uimd5='0' * 32)
    with pytest.raises(Verify.Refusal, match='failed md5 verification'):
        core.add_addon('wowi:100', False)
    assert tree(addons) == []
    assert core.config['Addons'] == []


def test_wowi_missing_md5_is_unverified_but_flagged(core, wowi):
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'}, uimd5=None)
    core.add_addon('wowi:100', False)
    assert core.config['Addons'][0]['Lock']['Verified'] is None
    _, alert = Verify.summarize(core.verification)
    assert 'Addon100' in alert


def test_wowi_bulk_cache_carries_md5(core, wowi, router):
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'})
    router.add('https://api.mmoui.com/v3/game/WOW/filedetails/100.json',
               json.loads(router.routes['https://api.mmoui.com/v3/game/WOW/filedetails/100.json'][1]))
    core.bulk_wowi_check(['100'])
    new = core.parse_url(WOWI)
    assert new.expectedHash[0] == 'md5'


def test_github_sha256_verified_install(core, github, addons):
    content, _ = github('owner/Repo', 'v1.0', {'Repo/Repo.toc': 'x'})
    core.add_addon('gh:owner/Repo', False)
    assert tree(addons) == ['Repo', 'Repo/Repo.toc']
    assert core.config['Addons'][0]['Lock']['Verified'] == 'sha256'
    assert core.config['Addons'][0]['Lock']['SHA256'] == sha256(content)


def test_github_sha256_mismatch(core, github, addons):
    github('owner/Repo', 'v1.0', {'Repo/Repo.toc': 'x'}, digest='sha256:' + 'f' * 64)
    with pytest.raises(Verify.Refusal, match='failed sha256 verification'):
        core.add_addon('gh:owner/Repo', False)
    assert tree(addons) == []


def test_github_null_digest_is_unverified_but_flagged(core, github):
    github('owner/Repo', 'v1.0', {'Repo/Repo.toc': 'x'}, digest=None)
    core.add_addon('gh:owner/Repo', False)
    summary, alert = Verify.summarize(core.verification)
    assert '0 verified' in summary and 'Repo' in alert


def graphql_payload(release):
    node = dict(release)
    node['assets'] = {'nodes': release['assets']}
    return {'data': {'search': {'nodes': [{'nameWithOwner': 'owner/Repo', 'releases': {'nodes': [node]}}]}}}


@pytest.mark.parametrize('tamper', [False, True])
def test_github_graphql_cache_carries_digest(core, router, addons, tamper):
    core.config['GHAPIKey'] = 'token'
    content = make_zip({'Repo/Repo.toc': 'x'})
    release = gh_release('owner/Repo', 'v1.0', content)
    router.add('https://api.github.com/graphql', graphql_payload(release))
    router.add(release['assets'][0]['url'], make_zip({'Repo/Repo.toc': 'evil'}) if tamper else content)
    core.bulk_check([{'URL': GH}])
    assert 'digest' in json.loads(router.requests[0].content)['query']
    assert core.githubCache['owner/Repo'][0]['assets'][0]['digest'] == f'sha256:{sha256(content)}'
    if tamper:
        with pytest.raises(Verify.Refusal, match='failed sha256 verification'):
            core.add_addon(GH, False)
        assert tree(addons) == []
    else:
        core.add_addon(GH, False)
        assert core.config['Addons'][0]['Lock']['Verified'] == 'sha256'
    assert not any(str(r.url).endswith('/releases') for r in router.requests)


def test_protected_directory_refused_even_with_adopt(core, wowi, addons):
    (addons / 'MyStuff').mkdir()
    (addons / 'MyStuff' / 'keep.lua').write_text('mine')
    core.protect_directories(['MyStuff'])
    wowi(100, '1.0', {'Foo/Foo.toc': 'x', 'mystuff/evil.lua': 'evil'})
    with pytest.raises(Verify.Refusal, match='mystuff is a protected directory'):
        core.add_addon('wowi:100', False, adopt=True)
    assert tree(addons) == ['MyStuff', 'MyStuff/keep.lua']


def test_directory_owned_by_other_addon_refused(core, wowi, github, addons):
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'})
    core.add_addon('wowi:100', False)
    github('owner/Repo', 'v1.0', {'Repo/Repo.toc': 'x', 'Foo/evil.lua': 'evil'})
    with pytest.raises(Verify.Refusal, match='Foo belongs to Addon100'):
        core.add_addon('gh:owner/Repo', False, adopt=True)
    assert (addons / 'Foo' / 'evil.lua').exists() is False


def test_unmanaged_directory_refused_without_adopt(core, wowi, addons):
    (addons / 'Local').mkdir()
    (addons / 'Local' / 'keep.lua').write_text('mine')
    wowi(100, '1.0', {'Foo/Foo.toc': 'x', 'Local/evil.lua': 'evil'})
    with pytest.raises(Verify.Refusal, match='Local already exists and is not managed'):
        core.add_addon('wowi:100', False)
    assert tree(addons) == ['Local', 'Local/keep.lua']
    assert core.config['Addons'] == []


def test_unmanaged_directory_adopted_with_flag(core, wowi, addons):
    (addons / 'Foo').mkdir()
    (addons / 'Foo' / 'stale.lua').write_text('old')
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'})
    assert core.add_addon('wowi:100', False, adopt=True)[0]
    assert tree(addons) == ['Foo', 'Foo/Foo.toc']


def test_update_with_new_unmanaged_directory_refused_even_forced(core, wowi, addons):
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'})
    core.add_addon('wowi:100', False)
    (addons / 'Local').mkdir()
    wowi(100, '1.1', {'Foo/Foo.toc': 'y', 'Local/evil.lua': 'evil'})
    for force in (False, True):
        with pytest.raises(Verify.Refusal, match='Local already exists'):
            core.update_addon(WOWI, True, force)
    assert (addons / 'Foo' / 'Foo.toc').read_text() == 'x'
    assert list((addons / 'Local').iterdir()) == []
    assert core.config['Addons'][0]['Version'] == '1.0'


def test_update_may_rewrite_own_directories(core, wowi, addons):
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'})
    core.add_addon('wowi:100', False)
    wowi(100, '1.1', {'Foo/Foo.toc': 'y'})
    core.update_addon(WOWI, True, False)
    assert (addons / 'Foo' / 'Foo.toc').read_text() == 'y'
    assert core.config['Addons'][0]['Lock']['Version'] == '1.1'


def test_cleanup_and_uninstall_never_remove_protected(core, wowi, addons):
    wowi(100, '1.0', {'Foo/Foo.toc': 'x', 'FooExtra/FooExtra.toc': 'x'})
    core.add_addon('wowi:100', False)
    core.protect_directories(['fooextra'])
    core.cleanup(['FooExtra'])
    assert (addons / 'FooExtra').is_dir()
    core.del_addon('Addon100', False)
    assert tree(addons) == ['FooExtra', 'FooExtra/FooExtra.toc']


def test_protect_and_unprotect(core, client_dir):
    assert core.protect_directories(['A', 'B', 'a']) == ['A', 'B']
    assert core.protect_directories(['b']) == []
    assert core.unprotect_directories(['a']) == ['A']
    assert saved_config(client_dir)['ProtectedDirectories'] == ['B']


def test_tofu_same_version_different_hash_refused(core, wowi, addons):
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'})
    core.add_addon('wowi:100', False)
    wowi(100, '1.0', {'Foo/Foo.toc': 'replaced'})
    with pytest.raises(Verify.Refusal, match=r'publisher replaced the file for version 1\.0'):
        core.update_addon(WOWI, True, True)
    assert (addons / 'Foo' / 'Foo.toc').read_text() == 'x'


def test_tofu_same_version_same_hash_allowed(core, wowi, addons):
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'})
    core.add_addon('wowi:100', False)
    (addons / 'Foo' / 'Foo.toc').write_text('local edit')
    core.update_addon(WOWI, True, True)
    assert (addons / 'Foo' / 'Foo.toc').read_text() == 'x'


def test_tofu_insecure_env_allows_with_notice(core, wowi, addons, monkeypatch):
    locked = wowi(100, '1.0', {'Foo/Foo.toc': 'x'})
    core.add_addon('wowi:100', False)
    replaced = wowi(100, '1.0', {'Foo/Foo.toc': 'replaced'})
    monkeypatch.setenv(Verify.INSECURE_ENV, '1')
    core.update_addon(WOWI, True, True)
    assert (addons / 'Foo' / 'Foo.toc').read_text() == 'replaced'
    assert core.notices[-1] == ('WARNING: Addon100.\nThe publisher replaced the file for version 1.0.\n'
                                f'Locked SHA256: {sha256(locked)}\nDownloaded SHA256: {sha256(replaced)}\n'
                                'Installed anyway because CURSEBREAKER_INSECURE_SKIP_VERIFY=1.')


def reinstall_after_republish(core, wowi, files=None):
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'})
    core.add_addon('wowi:100', False)
    core.del_addon(WOWI, False)
    wowi(100, '1.0', files or {'Foo/Foo.toc': 'replaced'})
    return core.add_addon('wowi:100', False)


def test_lock_survives_uninstall_and_refuses_replaced_file(core, wowi, addons):
    with pytest.raises(Verify.Refusal, match='replaced the file'):
        reinstall_after_republish(core, wowi)
    assert tree(addons) == []
    assert core.config['Addons'] == []


def test_lock_survives_uninstall_and_allows_identical_file(core, wowi, addons):
    assert reinstall_after_republish(core, wowi, files={'Foo/Foo.toc': 'x'}) == (True, 'Addon100', '1.0')
    assert (addons / 'Foo' / 'Foo.toc').read_text() == 'x'


def test_lock_matches_the_same_source_under_another_url(core, wowi, addons):
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'})
    core.add_addon('https://www.wowinterface.com/downloads/info100-Foo.html', False)
    core.del_addon('https://www.wowinterface.com/downloads/info100-Foo.html', False)
    wowi(100, '1.0', {'Foo/Foo.toc': 'replaced'})
    with pytest.raises(Verify.Refusal, match='replaced the file'):
        core.add_addon('wowi:100', False)
    assert tree(addons) == []


def test_lock_history_is_seeded_from_entries_of_an_older_config(core, wowi, addons):
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'})
    core.add_addon('wowi:100', False)
    del core.config['LockHistory']
    core.save_config()
    core.init_config()
    assert core.config['LockHistory']
    core.del_addon(WOWI, False)
    wowi(100, '1.0', {'Foo/Foo.toc': 'replaced'})
    with pytest.raises(Verify.Refusal, match='replaced the file'):
        core.add_addon('wowi:100', False)
    assert tree(addons) == []


def test_lock_matches_github_case_variants(core, github, addons):
    github('Owner/Repo', 'v1.0', {'Repo/Repo.toc': 'x'})
    core.add_addon('gh:Owner/Repo', False)
    core.del_addon('https://github.com/Owner/Repo', False)
    github('owner/repo', 'v1.0', {'Repo/Repo.toc': 'replaced'})
    with pytest.raises(Verify.Refusal, match='replaced the file'):
        core.add_addon('https://github.com/owner/repo', False)
    assert tree(addons) == []


def test_forced_downgrade_to_a_republished_version_refused(core, wowi, addons):
    wowi(100, '1.0', {'Foo/Foo.toc': 'old'})
    core.add_addon('wowi:100', False)
    wowi(100, '2.0', {'Foo/Foo.toc': 'new'})
    core.update_addon(WOWI, True, False)
    wowi(100, '1.0', {'Foo/Foo.toc': 'republished'})
    with pytest.raises(Verify.Refusal, match='replaced the file'):
        core.update_addon(WOWI, True, True)
    assert (addons / 'Foo' / 'Foo.toc').read_text() == 'new'
    assert core.config['Addons'][0]['Version'] == '2.0'


def test_insecure_env_overrides_the_lock_and_updates_the_history(core, wowi, addons, monkeypatch):
    locked = wowi(100, '1.0', {'Foo/Foo.toc': 'x'})
    core.add_addon('wowi:100', False)
    core.del_addon(WOWI, False)
    replaced = wowi(100, '1.0', {'Foo/Foo.toc': 'replaced'})
    monkeypatch.setenv(Verify.INSECURE_ENV, '1')
    core.add_addon('wowi:100', False)
    assert core.notices[-1] == (f'WARNING: Addon100.\nThe publisher replaced the file for version 1.0.\n'
                                f'Locked SHA256: {sha256(locked)}\nDownloaded SHA256: {sha256(replaced)}\n'
                                'Installed anyway because CURSEBREAKER_INSECURE_SKIP_VERIFY=1.')
    monkeypatch.delenv(Verify.INSECURE_ENV)
    core.del_addon(WOWI, False)
    assert core.add_addon('wowi:100', False) == (True, 'Addon100', '1.0')
    assert (addons / 'Foo' / 'Foo.toc').read_text() == 'replaced'


def test_lock_history_is_saved(core, wowi, client_dir):
    content = wowi(100, '1.0', {'Foo/Foo.toc': 'x'})
    core.add_addon('wowi:100', False)
    assert saved_config(client_dir)['LockHistory'] == {'wowi:100': [['1.0', sha256(content)]]}


def test_insecure_env_allows_hash_mismatch_with_notice(core, wowi, addons, monkeypatch):
    monkeypatch.setenv(Verify.INSECURE_ENV, '1')
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'}, uimd5='0' * 32)
    core.add_addon('wowi:100', False)
    assert tree(addons) == ['Foo', 'Foo/Foo.toc']
    assert core.config['Addons'][0]['Lock']['Verified'] is None
    assert any('failed md5 verification' in notice for notice in core.notices)


def test_raw_branch_archive_exempt_from_tofu(core, router, addons):
    router.add('https://api.github.com/repos/owner/Raw/branches/main', {'commit': {'sha': 'abcdef0123'}})
    archive = 'https://github.com/owner/Raw/archive/refs/heads/main.zip'
    router.add(archive, make_zip({'Raw-main/Raw/Raw.toc': 'x', 'Raw-main/README.md': 'loose',
                                  'Raw-main/Other/x.lua': 'x'}))
    core.add_addon('raw:dev', False)
    assert tree(addons) == ['Raw', 'Raw/Raw.toc']
    assert core.config['Addons'][0]['Lock']['Verified'] is None
    router.add(archive, make_zip({'Raw-main/Raw/Raw.toc': 'rebuilt'}))
    core.update_addon('raw:dev', True, True)
    assert (addons / 'Raw' / 'Raw.toc').read_text() == 'rebuilt'
    assert core.config['LockHistory'] == {'raw:dev': [['abcdef0', core.config['Addons'][0]['Lock']['SHA256']]]}


def test_downgrade_refused_then_forced(core, wowi, addons):
    wowi(100, '2.0', {'Foo/Foo.toc': 'new'})
    core.add_addon('wowi:100', False)
    wowi(100, '1.9', {'Foo/Foo.toc': 'old'})
    result = core.update_addon(WOWI, True, False)
    assert result[2:4] == ('1.9', '2.0')
    assert result[6] is True
    assert (addons / 'Foo' / 'Foo.toc').read_text() == 'new'
    assert any('older version 1.9' in notice for notice in core.notices)
    core.reset_notices()
    core.update_addon(WOWI, True, True)
    assert (addons / 'Foo' / 'Foo.toc').read_text() == 'old'
    assert core.config['Addons'][0]['Version'] == '1.9'


def test_unparseable_versions_skip_downgrade_check(core, wowi, addons):
    wowi(100, 'beta', {'Foo/Foo.toc': 'a'})
    core.add_addon('wowi:100', False)
    wowi(100, 'alpha', {'Foo/Foo.toc': 'b'})
    core.update_addon(WOWI, True, False)
    assert (addons / 'Foo' / 'Foo.toc').read_text() == 'b'


def test_directory_set_change_notice(core, wowi, addons):
    wowi(100, '1.0', {'Foo/Foo.toc': 'x', 'FooOld/FooOld.toc': 'x'})
    core.add_addon('wowi:100', False)
    assert core.notices == []
    wowi(100, '1.1', {'Foo/Foo.toc': 'x', 'FooNew/FooNew.toc': 'x'})
    core.update_addon(WOWI, True, False)
    assert core.notices == ['Addon100: directory set changed. Added: FooNew. Removed: FooOld.']
    assert tree(addons) == ['Foo', 'Foo/Foo.toc', 'FooNew', 'FooNew/FooNew.toc']
    assert core.config['Addons'][0]['Lock']['Directories'] == ['Foo', 'FooNew']


def test_tukui_install_is_unverified_by_design(core, router, addons):
    url = 'https://api.tukui.org/v1/download/elvui.zip'
    router.add('https://api.tukui.org/v1/addons', [{'slug': 'elvui', 'name': 'ElvUI', 'url': url, 'version': '13.0',
                                                    'patch': ['110200'], 'directories': ['ElvUI'], 'author': 'Elv',
                                                    'changelog_url': 'https://tukui.org'}])
    router.add(url, make_zip({'ElvUI/ElvUI.toc': 'x', 'Evil/evil.lua': 'x'}))
    core.add_addon('ElvUI', False)
    assert tree(addons) == ['ElvUI', 'ElvUI/ElvUI.toc', 'Evil', 'Evil/evil.lua']
    summary, alert = Verify.summarize(core.verification)
    assert summary.endswith('source publishes no hash: ElvUI') and alert is None


def test_wago_install_goes_through_guard(core, router, addons):
    core.config['WAAAPIKey'] = 'key'
    url = 'https://addons.wago.io/download/foo.zip'
    router.add('https://addons.wago.io/api/external/addons/foo?game_version=retail',
               {'display_name': 'Foo', 'authors': ['a'], 'website_url': 'https://addons.wago.io/addons/foo',
                'recent_release': {'stable': {'created_at': '2026-01-01T00:00:00Z', 'download_link': url,
                                              'supported_patches': ['11.2.0'], 'label': '1.0'}}})
    router.add(url, make_zip({'Foo/Foo.toc': 'x', 'Local/evil.lua': 'x'}))
    (addons / 'Local').mkdir()
    with pytest.raises(Verify.Refusal, match='Local already exists'):
        core.add_addon('wa:foo', False)
    assert tree(addons) == ['Local']


def download_requests(router, suffix):
    return [request for request in router.requests if str(request.url).endswith(suffix)]


def test_wowi_hash_mismatch_downloads_once(core, wowi, router):
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'}, uimd5='0' * 32)
    with pytest.raises(Verify.Refusal):
        core.add_addon('wowi:100', False)
    assert len(download_requests(router, '/1.0.zip')) == 1


def test_github_hash_mismatch_downloads_once(core, github, router):
    github('owner/Repo', 'v1.0', {'Repo/Repo.toc': 'x'}, digest=f'sha256:{"0" * 64}')
    with pytest.raises(Verify.Refusal):
        core.add_addon(GH, False)
    assert len(download_requests(router, '/releases/assets/1')) == 1


def test_ownership_refusal_message_lists_conflicts_for_addon(core, wowi, addons):
    (addons / 'Local').mkdir()
    wowi(100, '1.0', {'Local/Local.toc': 'x'})
    with pytest.raises(Verify.Refusal) as error:
        core.add_addon('wowi:100', False)
    assert str(error.value) == ('Addon100: Local already exists and is not managed by CurseBreaker. '
                                'Install with -a to adopt it.')


def test_http_error_still_retries_with_parse_prefix(core, wowi, router):
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'})
    router.add('https://cdn.wowinterface.com/downloads/file100/1.0.zip', b'down', status=500)
    with pytest.raises(RuntimeError, match=r'^Failed to parse addon data: ') as error:
        core.add_addon('wowi:100', False)
    assert not isinstance(error.value, Verify.Refusal)
    assert len(download_requests(router, '/1.0.zip')) == 2


def test_transport_error_still_retries_with_parse_prefix(core, wowi, router):
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'})
    router.fail('https://cdn.wowinterface.com/downloads/file100/1.0.zip', httpx.ConnectError('unreachable'))
    with pytest.raises(RuntimeError, match=r'^Failed to parse addon data: unreachable$') as error:
        core.add_addon('wowi:100', False)
    assert not isinstance(error.value, Verify.Refusal)
    assert len(download_requests(router, '/1.0.zip')) == 2


def test_corrupt_download_still_retries_with_parse_prefix(core, wowi, router):
    wowi(100, '1.0', {}, content=b'<html>', uimd5=True)
    with pytest.raises(RuntimeError, match=r'^Failed to parse addon data: [\s\S]*not a valid ZIP') as error:
        core.add_addon('wowi:100', False)
    assert not isinstance(error.value, Verify.Refusal)
    assert len(download_requests(router, '/1.0.zip')) == 2


def test_retry_reraises_refusal_immediately():
    refusal = Verify.Refusal('nope', 'Foo')
    calls = []

    @retry()
    def refuse():
        calls.append(1)
        raise refusal

    with pytest.raises(Verify.Refusal) as error:
        refuse()
    assert error.value is refusal
    assert len(calls) == 1


def test_retry_retries_plain_runtime_error_and_adds_prefix():
    calls = []

    @retry()
    def fail():
        calls.append(1)
        raise RuntimeError('boom')

    with pytest.raises(RuntimeError, match=r'^Failed to parse addon data: boom$'):
        fail()
    assert len(calls) == 2
