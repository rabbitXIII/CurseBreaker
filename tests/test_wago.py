import json
import httpx
import pytest
from conftest import make_zip, output, tree

COMPANION_URL = 'https://cursebreaker.acidweb.dev/CurseBreakerCompanion.zip'
CHECK_URL = 'https://data.wago.io/api/check/weakauras'
SAVED_VARIABLES = ('WeakAurasSaved = {\n    ["displays"] = {\n        ["Aura"] = {\n'
                   '            ["url"] = "https://wago.io/abc/1",\n        },\n    },\n}\n')


@pytest.fixture
def wago(tui, router, client_dir, addons):
    (addons / 'WeakAuras').mkdir()
    saved = client_dir / 'WTF' / 'Account' / 'ACCOUNT' / 'SavedVariables'
    saved.mkdir(parents=True)
    (saved / 'WeakAuras.lua').write_text(SAVED_VARIABLES)
    tui.core.masterConfig.update({'ConfigVersion': '4.9.1', 'CBCompanionVersion': 0})
    router.add(CHECK_URL, [])
    router.add(COMPANION_URL, make_zip({'CurseBreakerCompanion/CurseBreakerCompanion.toc': 'new'}))
    return tui


def enable(tui):
    tui.core.config['WagoSync'] = True


def requested(router, url):
    return [request for request in router.requests if str(request.url) == url]


def test_sync_is_off_for_new_config(core):
    assert 'WagoSync' not in core.config
    assert core.wago_sync_enabled() is False


def test_sync_is_off_for_upgraded_config(client_dir, core):
    (client_dir / 'WTF' / 'CurseBreaker.json').write_text(json.dumps({
        'Addons': [], 'WAStash': [], 'IgnoreClientVersion': {}, 'Backup': {'Enabled': True, 'Number': 7},
        'Version': '4.9.1', 'WAUsername': '', 'WAAccountName': '', 'WAAPIKey': '', 'GHAPIKey': '', 'WAAAPIKey': '',
        'CBCompanionVersion': 0, 'CompactMode': False, 'AutoUpdate': True, 'ShowAuthors': True,
        'ShowSources': False, 'AutoUpdateDelay': True, 'ProtectedDirectories': []}))
    core.init_config()
    assert 'WagoSync' not in core.config
    assert core.wago_sync_enabled() is False


def test_sync_is_off_while_username_is_disabled(core):
    core.config.update({'WagoSync': True, 'WAUsername': 'DISABLED'})
    assert core.wago_sync_enabled() is False


def test_update_with_sync_off_does_nothing(wago, router, addons):
    before = tree(addons)
    wago.c_wago_update(None)
    assert router.requests == []
    assert tree(addons) == before
    assert 'toggle wago' in output(wago)


def test_toggle_enables_then_disables(tui, addons):
    tui.c_toggle('wago')
    assert tui.core.wago_sync_enabled() is True
    assert tui.core.config['WAUsername'] == ''
    (addons / 'CurseBreakerCompanion').mkdir()
    tui.c_toggle('wago')
    assert tui.core.config['WagoSync'] is False
    assert tui.core.config['WAUsername'] == 'DISABLED'
    assert not (addons / 'CurseBreakerCompanion').exists()
    assert 'DISABLED' in output(tui)


def test_toggle_with_username_enables_and_filters(tui):
    tui.c_toggle('wago Someone')
    assert tui.core.wago_sync_enabled() is True
    assert tui.core.config['WAUsername'] == 'Someone'


def test_toggle_on_keeps_username_filter(tui):
    tui.core.config.update({'WagoSync': False, 'WAUsername': 'Someone'})
    tui.c_toggle('wago')
    assert tui.core.wago_sync_enabled() is True
    assert tui.core.config['WAUsername'] == 'Someone'


def test_toggle_on_clears_disabled_username(tui):
    tui.core.config['WAUsername'] = 'DISABLED'
    tui.c_toggle('wago')
    assert tui.core.wago_sync_enabled() is True
    assert tui.core.config['WAUsername'] == ''


def test_toggle_off_keeps_protected_companion(tui, addons):
    enable(tui)
    tui.core.protect_directories(['CurseBreakerCompanion'])
    (addons / 'CurseBreakerCompanion').mkdir()
    (addons / 'CurseBreakerCompanion' / 'mine.lua').write_text('mine')
    tui.c_toggle('wago')
    assert tui.core.config['WagoSync'] is False
    assert (addons / 'CurseBreakerCompanion' / 'mine.lua').read_text() == 'mine'


def test_companion_installs_only_its_own_folder(wago, router, addons):
    enable(wago)
    router.add(COMPANION_URL, make_zip({'CurseBreakerCompanion/CurseBreakerCompanion.toc': 'toc',
                                        'README.txt': 'loose', 'Other/x.lua': 'other'}))
    wago.c_wago_update(None)
    assert tree(addons / 'CurseBreakerCompanion') == ['CurseBreakerCompanion.toc', 'Data.lua']
    assert not (addons / 'Other').exists()
    assert not (addons / 'README.txt').exists()
    assert len(requested(router, CHECK_URL)) == 1


def test_companion_download_is_reported_as_unverified(wago):
    enable(wago)
    wago.c_wago_update(None)
    assert 'source publishes no hash: CurseBreakerCompanion' in output(wago)


def test_installed_companion_is_not_downloaded_again(wago, router, addons):
    enable(wago)
    (addons / 'CurseBreakerCompanion').mkdir()
    wago.c_wago_update(None)
    assert requested(router, COMPANION_URL) == []
    assert 'source publishes no hash' not in output(wago)


def test_traversal_archive_is_refused_and_keeps_old_companion(wago, router, addons):
    enable(wago)
    wago.core.masterConfig['CBCompanionVersion'] = 1
    (addons / 'CurseBreakerCompanion').mkdir()
    (addons / 'CurseBreakerCompanion' / 'old.lua').write_text('old')
    router.add(COMPANION_URL, make_zip({'CurseBreakerCompanion/../../evil.lua': 'evil'}))
    with pytest.raises(RuntimeError, match='path traversal'):
        wago.c_wago_update(None)
    assert tree(addons / 'CurseBreakerCompanion') == ['old.lua']
    assert not (addons.parent.parent / 'evil.lua').exists()


def test_failed_download_keeps_old_companion(wago, router, addons):
    enable(wago)
    wago.core.masterConfig['CBCompanionVersion'] = 1
    (addons / 'CurseBreakerCompanion').mkdir()
    (addons / 'CurseBreakerCompanion' / 'old.lua').write_text('old')
    router.add(COMPANION_URL, b'gone', status=404)
    with pytest.raises(httpx.HTTPStatusError):
        wago.c_wago_update(None)
    assert tree(addons / 'CurseBreakerCompanion') == ['old.lua']
    assert (addons / 'CurseBreakerCompanion' / 'old.lua').read_text() == 'old'


def test_protected_companion_is_refused_before_any_request(wago, router, addons):
    enable(wago)
    wago.core.protect_directories(['CurseBreakerCompanion'])
    (addons / 'CurseBreakerCompanion').mkdir()
    (addons / 'CurseBreakerCompanion' / 'mine.lua').write_text('mine')
    with pytest.raises(RuntimeError, match='protected'):
        wago.c_wago_update(None)
    assert router.requests == []
    assert tree(addons / 'CurseBreakerCompanion') == ['mine.lua']


def test_companion_owned_by_managed_addon_is_refused(wago, router, addons):
    enable(wago)
    wago.core.config['Addons'].append({'Name': 'Squatter', 'Directories': ['CurseBreakerCompanion']})
    with pytest.raises(RuntimeError, match='belongs to Squatter'):
        wago.c_wago_update(None)
    assert router.requests == []
    assert not (addons / 'CurseBreakerCompanion').exists()
