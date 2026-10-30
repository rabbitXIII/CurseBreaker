import io
import os
import re
import stat
import hashlib
import zipfile
from pathlib import Path
from dataclasses import dataclass
from packaging.version import Version, InvalidVersion
from . import is_addon_directory, Refusal

MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_MEMBERS = 20000
MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_RATIO = 200
RATIO_MIN_BYTES = 1024 * 1024
INSECURE_ENV = 'CURSEBREAKER_INSECURE_SKIP_VERIFY'
BRANCH_CONFIRMATION = 'branch install needs confirmation; rerun with -u to allow it.'


@dataclass
class Download:
    content: bytes
    sha256: str
    verified: str | None
    warning: str | None = None


def insecure():
    return os.environ.get(INSECURE_ENV) == '1'


def branch_source(custom_repository, url):
    return custom_repository.get(url.lower().removesuffix('/'))


def parse_digest(digest):
    if digest and digest.lower().startswith('sha256:'):
        return 'sha256', digest[7:]
    return None


def asset_digest(assets, url):
    for asset in assets:
        if asset.get('url') == url:
            return parse_digest(asset.get('digest'))
    return None


def download(http, url, expected=None, name=None, **kwargs):
    label = name or url
    sha256 = hashlib.sha256()
    md5 = hashlib.md5() if expected and expected[0] == 'md5' else None
    content = bytearray()
    with http.stream('GET', url, **kwargs) as response:
        response.raise_for_status()
        if int(response.headers.get('Content-Length') or 0) > MAX_ARCHIVE_BYTES:
            raise Refusal(f'Archive exceeds the {MAX_ARCHIVE_BYTES // 1024 // 1024} MiB size limit.', label)
        for chunk in response.iter_bytes():
            content.extend(chunk)
            if len(content) > MAX_ARCHIVE_BYTES:
                raise Refusal(f'Archive exceeds the {MAX_ARCHIVE_BYTES // 1024 // 1024} MiB size limit.', label)
            sha256.update(chunk)
            if md5:
                md5.update(chunk)
    result = Download(bytes(content), sha256.hexdigest(), None)
    if not expected:
        return result
    algorithm, digest = expected
    actual = md5.hexdigest() if algorithm == 'md5' else result.sha256
    if actual == digest.strip().lower():
        result.verified = algorithm
        return result
    reason = f'Downloaded file failed {algorithm} verification.\nURL: {url}\nExpected: {digest}\nActual: {actual}'
    if not insecure():
        raise Refusal(reason, label)
    result.warning = f'WARNING: {label}.\n{reason}\nInstalled anyway because {INSECURE_ENV}=1.'
    return result


def fetch(addon, url, **kwargs):
    result = download(addon.http, url, addon.expectedHash, addon.name, **kwargs)
    addon.sha256 = result.sha256
    addon.verified = result.verified
    addon.verifyWarning = result.warning
    return open_archive(result.content, addon.name)


def check_member_name(name, addon=None):
    normalized = name.replace('\\', '/')
    if normalized.startswith('/') or re.match(r'^[A-Za-z]:', normalized):
        raise Refusal(f'Archive contains an absolute path: {name}', addon)
    if '..' in normalized.split('/'):
        raise Refusal(f'Archive contains a path traversal: {name}', addon)


def open_archive(content, name=None):
    label = f'{name}.\n' if name else ''
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as e:
        raise RuntimeError(f'{label}Downloaded file is not a valid ZIP archive.') from e
    members = archive.infolist()
    if len(members) > MAX_MEMBERS:
        raise Refusal(f'Archive has more than {MAX_MEMBERS} members.', name)
    total = 0
    for member in members:
        check_member_name(member.filename, name)
        if stat.S_ISLNK(member.external_attr >> 16):
            raise Refusal(f'Archive contains a symlink: {member.filename}', name)
        if member.file_size > RATIO_MIN_BYTES and member.file_size > MAX_RATIO * max(member.compress_size, 1):
            raise Refusal(f'Archive member has a suspicious compression ratio: {member.filename}', name)
        # INVARIANT: zipfile never yields more than a member's declared file_size, so this sum bounds extraction.
        total += member.file_size
        if total > MAX_UNCOMPRESSED_BYTES:
            raise Refusal(f'Archive exceeds the {MAX_UNCOMPRESSED_BYTES // 1024 ** 3} GiB uncompressed size limit.',
                          name)
    return archive


def safe_extract(archive, path, directories, addon=None):
    root = Path(path).resolve()
    allowed = {directory for directory in directories if is_addon_directory(directory)}
    for member in archive.infolist():
        if not member.filename:
            continue
        check_member_name(member.filename, addon)
        top, separator, _ = member.filename.partition('/')
        if not separator or top not in allowed:
            continue
        base = (root / top).resolve()
        target = (root / member.filename).resolve()
        if target != base and base not in target.parents:
            raise Refusal(f'Archive member escapes its addon directory: {member.filename}', addon)
        archive.extract(member, root)


def is_protected(directory, protected):
    return directory.casefold() in {entry.casefold() for entry in protected}


def ownership_conflicts(directories, protected, owners, previous, existing, adopt):
    owners = {directory.casefold(): owner for directory, owner in owners.items()}
    previous = {directory.casefold() for directory in previous}
    existing = {directory.casefold() for directory in existing}
    conflicts = []
    for directory in directories:
        key = directory.casefold()
        if is_protected(directory, protected):
            conflicts.append(f'{directory} is a protected directory.')
        elif key in owners:
            conflicts.append(f'{directory} belongs to {owners[key]}.')
        elif key in existing and key not in previous and not adopt:
            conflicts.append(f'{directory} already exists and is not managed by CurseBreaker. '
                             f'Install with -a to adopt it.')
    return conflicts


def release_version(tag):
    return re.sub(r'^[vV]', '', str(tag).rsplit('/', 1)[-1])


def comparable_version(tag):
    try:
        return Version(release_version(tag))
    except (InvalidVersion, TypeError):
        return None


def incomparable_versions(*tags):
    return [tag for tag in tags if comparable_version(tag) is None]


def is_downgrade(old, new):
    old_version, new_version = comparable_version(old), comparable_version(new)
    return old_version is not None and new_version is not None and new_version < old_version


def lock_mismatch(lock, version, sha256):
    return bool(lock and lock.get('Version') == version and lock.get('SHA256') and lock['SHA256'] != sha256)


def summarize(records):
    if not records:
        return None, None
    verified = [algorithm for _, algorithm, _ in records if algorithm]
    unverified = sorted(name for name, algorithm, expected in records if not algorithm and not expected)
    missing = sorted(name for name, algorithm, expected in records if not algorithm and expected)
    counts = ', '.join(f'{algorithm}: {verified.count(algorithm)}' for algorithm in ('sha256', 'md5')
                       if algorithm in verified)
    summary = f'Verification: {len(verified)} verified{f" ({counts})" if counts else ""}'
    summary += f', {len(unverified) + len(missing)} unverified'
    if unverified:
        summary += f'; source publishes no hash: {", ".join(unverified)}'
    alert = f'No hash available although the source normally publishes one: {", ".join(missing)}' if missing else None
    return summary, alert
