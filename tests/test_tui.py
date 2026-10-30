import sys
from conftest import tree, output


def test_install_adopt_flag_reaches_core(tui, wowi, addons):
    (addons / 'Foo').mkdir()
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'})
    tui.c_install('wowi:100')
    assert 'Foo already exists and is not managed' in output(tui)
    assert tui.core.config['Addons'] == []
    tui.setup_table()
    tui.c_install('-a wowi:100')
    text = output(tui)
    assert 'Installed' in text
    assert 'Verification: 1 verified (md5: 1), 0 unverified' in text
    assert tree(addons) == ['Foo', 'Foo/Foo.toc']


def test_install_reports_missing_hash(tui, github):
    github('owner/Repo', 'v1.0', {'Repo/Repo.toc': 'x'}, digest=None)
    tui.c_install('gh:owner/Repo')
    assert 'No hash available although the source normally publishes one: Repo' in output(tui)


def test_update_prints_downgrade_notice_and_suppresses(tui, wowi, addons):
    wowi(100, '2.0', {'Foo/Foo.toc': 'new'})
    tui.core.add_addon('wowi:100', False)
    wowi(100, '1.0', {'Foo/Foo.toc': 'old'})
    tui.c_update('Addon100')
    text = output(tui)
    assert 'Update suppressed' in text
    assert 'not updating from 2.0 to older version 1.0' in text
    assert (addons / 'Foo' / 'Foo.toc').read_text() == 'new'


def test_protect_command(tui):
    tui.c_protect('Alpha Beta')
    tui.c_protect('-r alpha')
    tui.c_protect('../escape')
    tui.c_protect(False)
    text = output(tui)
    assert 'Now protected: Alpha, Beta' in text
    assert 'No longer protected: Alpha' in text
    assert 'Invalid directory name: ../escape' in text
    assert text.rstrip().endswith('Protected directories:\nBeta')
    assert tui.core.config['ProtectedDirectories'] == ['Beta']


def test_protect_is_completed_and_documented(tui, addons, monkeypatch):
    (addons / 'Local').mkdir()
    tui.slugs = {'wa': [], 'wowi': [], 'gh': [], 'custom': []}
    tui.setup_completer()
    assert 'protect' in tui.completer.options
    assert {'-r', 'Local'} <= set(tui.completer.options['protect'].words)
    monkeypatch.setattr(tui, 'parse_custom_addons', lambda: '')
    tui.c_help(None)
    assert 'protect [Directory]' in output(tui) and 'Adopt existing directories' in output(tui)


def test_auto_update_is_disabled(tui, monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(tui.core, 'http', None)
    assert tui.auto_update() is None
