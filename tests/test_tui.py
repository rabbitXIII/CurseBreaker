import sys
import httpx
import pytest
from rich.console import Console
from CurseBreaker import TUI
from conftest import tree


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


def raised(error):
    with pytest.raises(type(error)) as info:
        raise error
    return info.value


def test_unsupported_url_prints_one_message(tui):
    tui.c_install('ftp://nope:')
    text = output(tui)
    assert 'Provided URL is not supported.' in text
    assert 'Traceback' not in text


def test_verify_refusal_prints_one_message(tui, wowi):
    tui.c_protect('Foo')
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'})
    tui.c_install('wowi:100')
    text = output(tui)
    assert 'Refusing to install' in text
    assert 'Traceback' not in text


def test_network_error_prints_one_message(tui):
    tui.handle_exception(raised(httpx.ConnectError('boom')), False)
    assert output(tui) == 'boom\n'


def test_genuine_bug_prints_traceback(tui):
    tui.handle_exception(raised(KeyError('x')), False)
    assert 'Traceback' in output(tui)


def test_debug_variable_restores_traceback(tui, monkeypatch):
    monkeypatch.setenv('CURSEBREAKER_DEBUG', '1')
    tui.handle_exception(raised(RuntimeError('expected')), False)
    assert 'Traceback' in output(tui)


def test_error_list_prints_each_message(tui):
    tui.handle_exception([raised(RuntimeError('first')), raised(RuntimeError('second'))], False)
    text = output(tui)
    assert 'first' in text and 'second' in text
    assert 'Traceback' not in text


def test_empty_error_falls_back_to_cause_then_class_name(tui):
    with pytest.raises(RuntimeError) as info:
        raise RuntimeError from ValueError('bad json')
    tui.handle_exception(info.value, False)
    tui.handle_exception(raised(RuntimeError()), False)
    assert output(tui) == 'bad json\nRuntimeError\n'


def test_error_message_is_not_parsed_as_markup(tui):
    tui.handle_exception(raised(RuntimeError('[bold]raw[/bold]')), False)
    assert output(tui) == '[bold]raw[/bold]\n'


def test_help_documents_direct_github_install(tui, monkeypatch):
    monkeypatch.setattr(tui, 'parse_custom_addons', lambda: '')
    tui.c_help(None)
    assert 'installed directly with gh:[username]/[repository_name]' in output(tui)


def test_error_message_keeps_emoji_codes_literal(tui):
    tui.handle_exception(raised(RuntimeError('wowi:100:dev :warning:')), False)
    assert output(tui) == 'wowi:100:dev :warning:\n'


def test_notices_keep_emoji_codes_literal(tui):
    tui.core.notices.append('see :100: and :warning:')
    tui.print_notices()
    assert 'see :100: and :warning:' in output(tui)


def test_recursion_error_keeps_traceback(tui):
    tui.handle_exception(raised(RecursionError()), False)
    assert 'Traceback' in output(tui)


def test_verify_mismatch_prints_full_refusal(tui, wowi):
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'}, uimd5='0' * 32)
    tui.c_install('wowi:100')
    text = output(tui)
    assert 'failed md5 verification' in text
    assert 'Expected:' in text
    assert 'Actual:' in text
    assert 'Traceback' not in text


@pytest.fixture
def break_downloads(tui, router):
    def handler(request):
        if request.url.path.endswith('.zip'):
            raise httpx.ConnectError('Network is unreachable')
        return router(request)

    def apply():
        tui.core.http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    yield apply
    tui.core.http.close()


def test_install_network_error_prints_one_message(tui, wowi, break_downloads):
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'})
    break_downloads()
    tui.c_install('wowi:100')
    text = output(tui)
    assert 'Network is unreachable' in text
    assert 'Traceback' not in text


def test_update_network_error_prints_one_message(tui, wowi, break_downloads):
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'})
    tui.c_install('wowi:100')
    wowi(100, '2.0', {'Foo/Foo.toc': 'y'})
    break_downloads()
    tui.setup_table()
    tui.c_update(None)
    text = output(tui)
    assert 'Network is unreachable' in text
    assert 'Traceback' not in text


def raising(error):
    def fail(*_):
        raise error
    return fail


def test_master_config_failure_prints_message_and_exits(tui, monkeypatch):
    monkeypatch.setattr(tui.core, 'init_master_config', raising(RuntimeError('offline')))
    tui.headless = True
    with pytest.raises(SystemExit):
        tui.load_master_config()
    assert 'offline' in output(tui)


def test_master_config_bug_is_reraised(tui, monkeypatch):
    monkeypatch.setattr(tui.core, 'init_master_config', raising(KeyError('x')))
    with pytest.raises(KeyError):
        tui.load_master_config()


def test_motd_network_error_prints_message_and_continues(tui, monkeypatch):
    monkeypatch.setattr(tui, 'motd_parser', raising(httpx.ConnectError('motd down')))
    tui.show_motd()
    assert output(tui) == 'motd down\n'


def test_motd_bug_is_reraised(tui, monkeypatch):
    monkeypatch.setattr(tui, 'motd_parser', raising(KeyError('x')))
    with pytest.raises(KeyError):
        tui.show_motd()
