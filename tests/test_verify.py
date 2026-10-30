import io
import os
import zipfile
import pytest
from CB import Verify
from conftest import make_zip, md5, sha256, tree

URL = 'https://example.invalid/addon.zip'


def test_download_md5_match(router, http):
    content = make_zip({'Foo/Foo.toc': 'x'})
    router.add(URL, content)
    result = Verify.download(http, URL, ('md5', md5(content).upper()))
    assert result.content == content
    assert result.verified == 'md5'
    assert result.sha256 == sha256(content)


def test_download_md5_mismatch_names_url_and_hashes(router, http):
    content = make_zip({'Foo/Foo.toc': 'x'})
    router.add(URL, content)
    with pytest.raises(RuntimeError) as error:
        Verify.download(http, URL, ('md5', '0' * 32), name='Foo')
    message = str(error.value)
    assert 'Foo' in message and URL in message and '0' * 32 in message and md5(content) in message


def test_download_sha256_match_and_mismatch(router, http):
    content = make_zip({'Foo/Foo.toc': 'x'})
    router.add(URL, content)
    assert Verify.download(http, URL, ('sha256', sha256(content))).verified == 'sha256'
    with pytest.raises(RuntimeError, match=sha256(content)):
        Verify.download(http, URL, ('sha256', 'f' * 64))


def test_download_without_hash_is_unverified(router, http):
    router.add(URL, b'data')
    result = Verify.download(http, URL, None)
    assert result.verified is None
    assert result.sha256 == sha256(b'data')


def test_download_http_error(router, http):
    with pytest.raises(Exception, match='404'):
        Verify.download(http, URL, None)


def test_download_size_cap_streamed(router, http, monkeypatch):
    monkeypatch.setattr(Verify, 'MAX_ARCHIVE_BYTES', 10)
    router.add(URL, b'x' * 11)
    with pytest.raises(RuntimeError, match='size limit'):
        Verify.download(http, URL, None)


def test_download_size_cap_content_length(router, http):
    router.add(URL, b'x', headers={'Content-Length': str(Verify.MAX_ARCHIVE_BYTES + 1)})
    with pytest.raises(RuntimeError, match='size limit'):
        Verify.download(http, URL, None)


def test_insecure_env_downgrades_mismatch_to_warning(router, http, monkeypatch):
    monkeypatch.setenv(Verify.INSECURE_ENV, '1')
    router.add(URL, b'data')
    result = Verify.download(http, URL, ('md5', '0' * 32))
    assert result.verified is None
    assert 'failed md5 verification' in result.warning and Verify.INSECURE_ENV in result.warning


def test_parse_digest():
    assert Verify.parse_digest('sha256:abc') == ('sha256', 'abc')
    assert Verify.parse_digest(None) is None
    assert Verify.parse_digest('sha1:abc') is None


def test_open_archive_member_count_cap(monkeypatch):
    monkeypatch.setattr(Verify, 'MAX_MEMBERS', 3)
    Verify.open_archive(make_zip({f'Foo/{i}.lua': '' for i in range(3)}))
    with pytest.raises(RuntimeError, match='more than 3 members'):
        Verify.open_archive(make_zip({f'Foo/{i}.lua': '' for i in range(4)}))


def test_open_archive_total_size_cap(monkeypatch):
    monkeypatch.setattr(Verify, 'MAX_UNCOMPRESSED_BYTES', 100)
    with pytest.raises(RuntimeError, match='uncompressed size limit'):
        Verify.open_archive(make_zip({'Foo/a.lua': 'a' * 60, 'Foo/b.lua': 'b' * 60}))


def test_open_archive_rejects_compression_bomb():
    with pytest.raises(RuntimeError, match='compression ratio'):
        Verify.open_archive(make_zip({'Foo/zeros.bin': b'\0' * (2 * 1024 * 1024)}))


def test_open_archive_allows_small_highly_compressible_member():
    Verify.open_archive(make_zip({'Foo/zeros.bin': b'\0' * (512 * 1024)}))


def test_open_archive_rejects_symlink():
    with pytest.raises(RuntimeError, match='symlink'):
        Verify.open_archive(make_zip({'Foo/Foo.toc': 'x'}, symlinks=[('Foo/link', '/etc')]))


@pytest.mark.parametrize('name', ['../evil.lua', 'Foo/../../evil.lua', '/abs/evil.lua', 'C:/evil.lua',
                                  '..\\evil.lua', 'Foo\\..\\..\\evil.lua'])
def test_open_archive_rejects_traversal(name):
    with pytest.raises(RuntimeError, match=r'absolute path|path traversal'):
        Verify.open_archive(make_zip({'Foo/Foo.toc': 'x', name: 'x'}))


def test_open_archive_rejects_garbage():
    with pytest.raises(RuntimeError, match='not a valid ZIP'):
        Verify.open_archive(b'<html>')


def test_safe_extract_only_writes_listed_directories(tmp_path):
    archive = Verify.open_archive(make_zip({'Foo/Foo.toc': 'x', 'Foo/sub/a.lua': 'a', 'Bar/Bar.toc': 'x',
                                            'README.txt': 'loose', 'Foo.toc': 'loose'}))
    Verify.safe_extract(archive, tmp_path, ['Foo'])
    assert tree(tmp_path) == ['Foo', 'Foo/Foo.toc', 'Foo/sub', 'Foo/sub/a.lua']


def test_safe_extract_skips_loose_files_even_when_named_like_a_directory(tmp_path):
    archive = Verify.open_archive(make_zip({'Foo': 'loose', 'Bar/Bar.toc': 'x'}))
    Verify.safe_extract(archive, tmp_path, ['Foo', 'Bar'])
    assert tree(tmp_path) == ['Bar', 'Bar/Bar.toc']


def test_safe_extract_refuses_to_follow_existing_symlink_out_of_directory(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    root = tmp_path / 'AddOns'
    (root / 'Foo').mkdir(parents=True)
    os.symlink(outside, root / 'Foo' / 'link')
    archive = Verify.open_archive(make_zip({'Foo/link/payload.lua': 'x'}))
    with pytest.raises(RuntimeError, match='escapes'):
        Verify.safe_extract(archive, root, ['Foo'])
    assert list(outside.iterdir()) == []


def test_safe_extract_rechecks_rewritten_names(tmp_path):
    archive = Verify.open_archive(make_zip({'Repo-main/Foo/a.lua': 'x'}))
    for member in archive.infolist():
        member.filename = member.filename.replace('Repo-main/', '../')
    with pytest.raises(RuntimeError, match='path traversal'):
        Verify.safe_extract(archive, tmp_path, ['Foo'])


def test_ownership_conflicts():
    protected = ['MyStuff']
    owners = {'Other': 'OtherAddon'}
    existing = ['MyStuff', 'Other', 'Local', 'Mine']

    def check(directories, previous=(), adopt=False):
        return Verify.ownership_conflicts(directories, protected, owners, previous, existing, adopt)

    assert check(['Fresh']) == []
    assert check(['Mine'], previous=['Mine']) == []
    assert 'protected' in check(['mystuff'], adopt=True)[0]
    assert 'belongs to OtherAddon' in check(['OTHER'], adopt=True)[0]
    assert 'not managed' in check(['local'])[0]
    assert check(['Local'], adopt=True) == []


def test_is_downgrade():
    assert Verify.is_downgrade('2.0.0', '1.9.9')
    assert Verify.is_downgrade('v2.0', 'v1.0')
    assert not Verify.is_downgrade('1.0', '1.1')
    assert not Verify.is_downgrade('1.0', '1.0')
    assert not Verify.is_downgrade('r123', 'r122')
    assert not Verify.is_downgrade('abc1234', 'def5678')


def test_source_key():
    assert Verify.source_key('https://www.wowinterface.com/downloads/info100-Foo.html') == 'wowi:100'
    assert Verify.source_key('https://www.wowinterface.com/downloads/info100.html') == 'wowi:100'
    assert Verify.source_key('https://www.wowinterface.com/downloads/nodigits.html') == \
        'https://www.wowinterface.com/downloads/nodigits.html'
    assert Verify.source_key('https://github.com/Owner/Repo') == 'gh:owner/repo'
    assert Verify.source_key('https://addons.wago.io/addons/Some-Addon') == 'wa:some-addon'
    assert Verify.source_key('ElvUI') == 'elvui'
    assert Verify.source_key('Raw:Dev') == 'raw:dev'


def test_locked_sha256():
    history = {'wowi:100': [['1.0', 'aa'], ['1.1', 'bb']]}
    assert Verify.locked_sha256(history, 'https://www.wowinterface.com/downloads/info100.html', '1.1') == 'bb'
    assert Verify.locked_sha256(history, 'https://www.wowinterface.com/downloads/info100.html', '2.0') is None
    assert Verify.locked_sha256(history, 'https://www.wowinterface.com/downloads/info200.html', '1.0') is None
    assert Verify.locked_sha256({}, 'elvui', '1.0') is None


def test_remember_lock_replaces_a_re_recorded_version():
    history = {}
    Verify.remember_lock(history, 'ElvUI', '1.0', 'aa')
    Verify.remember_lock(history, 'elvui', '1.1', 'bb')
    Verify.remember_lock(history, 'elvui', '1.0', 'cc')
    assert history == {'elvui': [['1.1', 'bb'], ['1.0', 'cc']]}


def test_remember_lock_keeps_the_newest_versions():
    history = {}
    for number in range(Verify.MAX_LOCKED_VERSIONS + 5):
        Verify.remember_lock(history, 'elvui', str(number), f'sha{number}')
    pairs = history['elvui']
    assert len(pairs) == Verify.MAX_LOCKED_VERSIONS
    assert pairs[0] == ['5', 'sha5']
    assert pairs[-1] == [str(Verify.MAX_LOCKED_VERSIONS + 4), f'sha{Verify.MAX_LOCKED_VERSIONS + 4}']


def test_seed_lock_history_skips_entries_without_a_locked_hash():
    addons = [{'URL': 'https://github.com/Owner/Repo', 'Lock': {'Version': '1.0', 'SHA256': 'aa'}},
              {'URL': 'elvui'},
              {'URL': 'tukui', 'Lock': {'Version': '2.0', 'SHA256': None}},
              {'URL': 'raw:dev', 'Lock': {'Version': '3.0'}}]
    assert Verify.seed_lock_history(addons) == {'gh:owner/repo': [['1.0', 'aa']]}


def test_summarize():
    assert Verify.summarize([]) == (None, None)
    summary, alert = Verify.summarize([('A', 'sha256', True), ('B', 'md5', True), ('C', None, False),
                                       ('D', None, True)])
    assert summary == 'Verification: 2 verified (sha256: 1, md5: 1), 2 unverified; source publishes no hash: C'
    assert alert == 'No hash available although the source normally publishes one: D'


def test_zipfile_caps_output_at_declared_size():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('Foo/a.lua', b'a' * 1000)
    archive = zipfile.ZipFile(io.BytesIO(buffer.getvalue()))
    info = archive.getinfo('Foo/a.lua')
    info.file_size = 10
    produced = 0
    with pytest.raises(zipfile.BadZipFile), archive.open(info) as member:
        while chunk := member.read(4):
            produced += len(chunk)
    assert produced <= 10
