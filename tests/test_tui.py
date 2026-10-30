import sys
import httpx
import pytest
from rich.prompt import Confirm
from conftest import make_zip, tree, output


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
    assert 'Refused: Addon100: Foo is a protected directory.' in text
    assert 'Failed to parse addon data' not in text
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
    assert 'Refused: Addon100: Downloaded file failed md5 verification.' in text
    assert 'Failed to parse addon data' not in text
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


def test_archive_refusal_prints_one_message(tui, wowi):
    wowi(100, '1.0', {'Foo/Foo.toc': 'x', '../evil.lua': 'x'})
    tui.c_install('wowi:100')
    text = output(tui)
    assert 'Refused: Addon100: Archive contains a path traversal: ../evil.lua' in text
    assert 'Failed to parse addon data' not in text
    assert 'Traceback' not in text


def test_symlink_refusal_prints_one_message(tui, wowi):
    content = make_zip({'Foo/Foo.toc': 'x'}, symlinks=[('Foo/link', '/etc')])
    wowi(100, '1.0', {}, content=content)
    tui.c_install('wowi:100')
    text = output(tui)
    assert 'Refused: Addon100: Archive contains a symlink: Foo/link' in text
    assert 'Failed to parse addon data' not in text
    assert 'Traceback' not in text


def test_lock_mismatch_prints_one_message(tui, wowi):
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'})
    tui.core.add_addon('wowi:100', False)
    wowi(100, '1.0', {'Foo/Foo.toc': 'replaced'})
    tui.setup_table()
    tui.c_update('Addon100', force=True)
    text = output(tui)
    assert 'Refused: Addon100: The publisher replaced the file for version 1.0.' in text
    assert 'Failed to parse addon data' not in text
    assert 'Traceback' not in text


def test_corrupt_download_prints_parse_error_not_refusal(tui, wowi):
    wowi(100, '1.0', {}, content=b'<html>')
    tui.c_install('wowi:100')
    text = output(tui)
    assert 'Failed to parse addon data: Addon100.\nDownloaded file is not a valid ZIP archive.' in text
    assert 'Refused:' not in text
    assert 'Traceback' not in text


RAW_ARCHIVE = 'https://github.com/owner/Raw/archive/refs/heads/main.zip'


@pytest.fixture
def raw_branch(router):
    router.add('https://api.github.com/repos/owner/Raw/branches/main', {'commit': {'sha': 'abcdef0123'}})
    router.add(RAW_ARCHIVE, make_zip({'Raw-main/Raw/Raw.toc': 'x'}))


class Prompts(list):
    def __init__(self, monkeypatch):
        super().__init__()
        self.monkeypatch = monkeypatch

    def answer(self, value):
        def ask(message, **kwargs):
            self.append((message, kwargs))
            return value
        self.monkeypatch.setattr(Confirm, 'ask', ask)


@pytest.fixture
def prompts(monkeypatch):
    return Prompts(monkeypatch)


def set_tty(monkeypatch, value):
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: value)


def test_branch_install_yes_installs_and_names_the_source(tui, raw_branch, prompts, monkeypatch, addons):
    set_tty(monkeypatch, True)
    prompts.answer(True)
    tui.c_install('raw:dev')
    assert len(prompts) == 1
    message, kwargs = prompts[0]
    assert 'Raw installs the "main" branch of owner/Raw.' in message
    assert "can't be verified" in message
    assert kwargs == {'default': False, 'console': tui.console}
    assert 'Installed' in output(tui)
    assert tree(addons) == ['Raw', 'Raw/Raw.toc']


def test_branch_install_no_skips_without_network(tui, router, raw_branch, prompts, monkeypatch, addons):
    set_tty(monkeypatch, True)
    prompts.answer(False)
    tui.c_install('raw:dev')
    text = output(tui)
    assert 'Skipped' in text and 'unverifiable branch install' in text
    assert router.requests == []
    assert tree(addons) == []
    assert tui.core.config['Addons'] == []


def test_branch_install_without_tty_is_refused(tui, router, raw_branch, prompts, monkeypatch, addons):
    set_tty(monkeypatch, False)
    prompts.answer(True)
    tui.c_install('raw:dev')
    text = output(tui)
    assert 'Refused: Raw: branch install needs confirmation; rerun with -u to allow it.' in text
    assert 'Traceback' not in text
    assert router.requests == []
    assert prompts == []
    assert tree(addons) == []


def test_branch_install_flag_skips_the_question(tui, raw_branch, prompts, monkeypatch, addons):
    set_tty(monkeypatch, False)
    prompts.answer(False)
    tui.c_install('-u raw:dev')
    assert prompts == []
    assert tree(addons) == ['Raw', 'Raw/Raw.toc']


def test_branch_install_flag_skips_the_question_on_a_tty(tui, raw_branch, prompts, monkeypatch, addons):
    set_tty(monkeypatch, True)
    prompts.answer(False)
    tui.c_install('raw:dev -u')
    assert prompts == []
    assert tree(addons) == ['Raw', 'Raw/Raw.toc']


@pytest.mark.parametrize('tty', [True, False])
def test_declined_branch_install_still_installs_other_arguments(tui, raw_branch, wowi, prompts, monkeypatch, addons,
                                                                tty):
    set_tty(monkeypatch, tty)
    prompts.answer(False)
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'})
    tui.c_install('raw:dev wowi:100')
    assert tree(addons) == ['Foo', 'Foo/Foo.toc']
    assert [addon['Name'] for addon in tui.core.config['Addons']] == ['Addon100']
    assert ('Skipped' if tty else 'Refused: Raw') in output(tui)


def test_installed_branch_addon_is_not_prompted(tui, raw_branch, prompts, monkeypatch, router, addons):
    set_tty(monkeypatch, False)
    tui.c_install('-u raw:dev')
    requests = len(router.requests)
    set_tty(monkeypatch, True)
    prompts.answer(False)
    tui.setup_table()
    tui.c_install('raw:dev')
    assert prompts == []
    assert 'Already installed' in output(tui)
    assert len(router.requests) == requests


def test_branch_update_never_asks(tui, raw_branch, router, prompts, monkeypatch, addons):
    set_tty(monkeypatch, True)
    prompts.answer(True)
    tui.c_install('raw:dev')
    prompts.clear()
    router.add('https://api.github.com/repos/owner/Raw/branches/main', {'commit': {'sha': '1234567890'}})
    router.add(RAW_ARCHIVE, make_zip({'Raw-main/Raw/Raw.toc': 'new'}))
    tui.setup_table()
    tui.c_update('Raw')
    assert (addons / 'Raw' / 'Raw.toc').read_text() == 'new'
    router.add(RAW_ARCHIVE, make_zip({'Raw-main/Raw/Raw.toc': 'forced'}))
    tui.c_force_update('Raw')
    assert (addons / 'Raw' / 'Raw.toc').read_text() == 'forced'
    assert prompts == []


def test_release_install_never_asks(tui, github, prompts, monkeypatch):
    set_tty(monkeypatch, True)
    prompts.answer(False)
    github('owner/Repo', 'v1.0', {'Repo/Repo.toc': 'x'})
    tui.c_install('gh:owner/Repo')
    assert prompts == []
    assert 'Installed' in output(tui)


def archive_requests(router):
    return [request for request in router.requests if str(request.url) == RAW_ARCHIVE]


def test_branch_install_with_trailing_slash_is_refused_without_tty(tui, router, raw_branch, prompts, monkeypatch):
    set_tty(monkeypatch, False)
    prompts.answer(True)
    tui.c_install('raw:dev/')
    assert 'Refused: Raw: branch install needs confirmation' in output(tui)
    assert router.requests == []
    assert prompts == []


def test_addon_named_like_a_slashed_branch_does_not_confirm_it(tui, wowi, router, raw_branch, prompts, monkeypatch,
                                                                addons):
    wowi(100, '1.0', {'Foo/Foo.toc': 'x'})
    tui.c_install('wowi:100')
    tui.core.config['Addons'][0]['Name'] = 'raw:dev/'
    set_tty(monkeypatch, False)
    prompts.answer(True)
    tui.setup_table()
    tui.c_install('raw:dev/')
    assert 'Refused: Raw: branch install needs confirmation' in output(tui)
    assert archive_requests(router) == []
    assert not (addons / 'Raw').exists()
    assert prompts == []


def test_branch_install_with_trailing_slash_declined_on_tty(tui, router, raw_branch, prompts, monkeypatch):
    set_tty(monkeypatch, True)
    prompts.answer(False)
    tui.c_install('raw:dev/')
    assert len(prompts) == 1
    assert 'Skipped' in output(tui)
    assert router.requests == []


def test_unrecognised_branch_form_is_refused_by_core(tui, router, raw_branch, prompts, monkeypatch, addons):
    set_tty(monkeypatch, True)
    monkeypatch.setattr(tui, 'find_branch_source', lambda addon: None)
    prompts.answer(True)
    tui.c_install('raw:dev')
    assert 'Refused: Raw: branch install needs confirmation' in output(tui)
    assert archive_requests(router) == []
    assert tree(addons) == []
    assert prompts == []
