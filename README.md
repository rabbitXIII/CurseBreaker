# About this fork

A fork of [AcidWeb/CurseBreaker](https://github.com/AcidWeb/CurseBreaker) that checks what it downloads and where it writes.

- **Verified downloads.** WoWInterface files are checked against their published MD5 and GitHub release assets against their SHA-256 digest. A mismatch aborts the install. Tukui, Wago Addons and `:Dev` branch archives publish no hash; installs from them are reported as unverified.
- **Trust on first use.** Each install records the version and SHA-256 of the file. If the same version is later served with different bytes, the update is refused.
- **Archive limits.** 100 MiB download, 20000 members, 1 GiB uncompressed. Symlinks, absolute paths and `..` traversal are rejected, and loose top-level files are never written into `AddOns`.
- **Folder ownership.** An addon may only write its own folders. A folder owned by another managed addon is refused, and so is an existing folder CurseBreaker doesn't manage (for example a personal addon you are developing) unless you install with `-a` to adopt it. `protect DIR [DIR...]` marks folders that are never written or deleted, even by `force_update`. `protect` lists them and `protect -r DIR` removes the protection.
- **No silent downgrades.** If the new version is older than the installed one, `update` skips it and says so. Use `force_update` to install it anyway.
- **Wago aura sync is opt-in.** Off by default; `toggle wago` turns it on. What is sent to data.wago.io is the IDs of installed WeakAuras and Plater profiles plus auras pushed from Wago links, with the Wago API key if one is set. The CurseBreakerCompanion addon is downloaded from cursebreaker.acidweb.dev (no published hash, reported as unverified) through the archive limits and extraction filter, and is refused if the folder is protected or belongs to a managed addon.
- **No self-update.** Upstream binaries would replace this fork with an unverified build, so the fork runs from source.

Setting `CURSEBREAKER_INSECURE_SKIP_VERIFY=1` turns hash and lock mismatches into warnings. Use it only to recover from a known-good republish.

Expected errors print as a single message. Set `CURSEBREAKER_DEBUG=1` to see full tracebacks.

### Quickstart

```sh
git clone https://github.com/rabbitXIII/CurseBreaker
cd CurseBreaker
./cb.sh              # interactive; also ./cb.sh status, ./cb.sh update, ./cb.sh headless
```

`cb.sh` runs inside `/Applications/World of Warcraft/_forever_`. For another client, set `CURSEBREAKER_WOW_DIR` (e.g. `.../_retail_`). It uses `uv` if it is installed. Otherwise it creates `.venv` with Python 3.14+ and installs the hash-pinned `requirements.lock`.

Addon folders you installed by hand before are adopted with `install -a`. GitHub-hosted addons without a Forever build (WeakAuras, for example) fail with "Failed to find release for your client version", same as upstream.

### Tracking upstream

```sh
git fetch upstream && git merge upstream/master
```

The fork's logic lives in `CB/Verify.py` with small hooks at the call sites, so merge conflicts stay small. Tests: `uv run pytest`.

# CurseBreaker

[<img src="https://img.shields.io/github/release/AcidWeb/CurseBreaker">](https://github.com/AcidWeb/CurseBreaker/releases/latest) [<img src="https://img.shields.io/github/downloads/AcidWeb/CurseBreaker/latest/total">](https://github.com/AcidWeb/CurseBreaker/releases/latest) [<img src="https://img.shields.io/github/downloads/AcidWeb/CurseBreaker/total">](https://github.com/AcidWeb/CurseBreaker/releases/latest) [<img src="https://img.shields.io/github/actions/workflow/status/AcidWeb/CurseBreaker/build.yml">](https://github.com/AcidWeb/CurseBreaker/actions) [<img src="https://img.shields.io/discord/362155557488164874?logo=discord">](https://discord.gg/G2SXFGb)

TUI/CLI addon updater for World of Warcraft.

![Screenshot](https://i.imgur.com/XI7vORk.png)

## DOWNLOAD
The latest release can be found [here](https://github.com/AcidWeb/CurseBreaker/releases/latest).\
Please be aware that Linux (.gz) and macOS (.zip) versions are not thoroughly tested.\
Windows 10+, Ubuntu 22.04+, Debian 12+ and macOS 15+ are supported.

## USAGE
Place **CurseBreaker** binary inside the directory containing `Wow.exe`, `WowClassic.exe` or `World of Warcraft.app`.\
Read the instructions at the top of the screen.

Already installed addons will not be recognized by **CurseBreaker**, and they need to be reinstalled.\
This process can be partially automated by using the `import` command.

_Retail_, _Forever_, _Mists of Pandaria Classic_, _Classic_ and _Anniversary_ clients are supported. The client version is detected automatically.\
By default **CurseBreaker** will create backups of the entire `WTF` directory.

## TIPS & TRICKS
- TUI will look a lot better if the application is started by something else than the default Windows command prompt. [Windows Terminal](https://aka.ms/terminal) is recommended.
- Many of the fields are links if used terminal emulator supports them. 
- On Windows command `uri_integration` can be used to enable integration with the Wago Addons and Wago page.
- Most of the commands support the space-separated list of addons.
- `install` command has an optional `-i` flag that can be used to disable the client version check.
- Environment variable `CURSEBREAKER_PATH` can be used to set the custom location of WoW client.
- The application can be run in non-interactive mode by providing commands directly as a parameter.
- When the application is started with a `headless` parameter entire addon and Wago upgrade process plus WTF backup should be executed in the background. Log file _CurseBreaker.html_ will be created in the same directory as the application.

## SUPPORTED URL
- Wago Addons: `https://addons.wago.io/addons/[addon_name]`, `wa:[addon_name]`
- WoWInterface: `https://www.wowinterface.com/downloads/[addon_name]`, `wowi:[addon_id]`
- Tukui: `ElvUI`, `Tukui`
- GitHub: Development versions of multiple addons. Slugs are suffixed with `:Dev`.
- GitHub Releases: `https://github.com/[username]/[repository_name]`, `gh:[username]/[repository_name]`
- Wago: **CurseBreaker** can update auras and Plater profiles/scripts like WeakAuras Companion.

## WAGO ADDONS SUPPORT
To use Wago Addons as addon source user needs to provide a personal API key. It is a paid feature.\
The key can be obtained [here](https://addons.wago.io/patreon) and needs to be added to the application configuration by using the `set wago_addons_api` command.

## GITHUB SUPPORT
Providing [personal access token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token) **greatly** increase speed of bulk version check and solve possible issues with rate limiting.\
Both classic and fine-grained tokens are supported. No additional permissions are required.\
Token can be added to application by using the `set gh_api` command.

## WEAKAURAS SUPPORT
Once enabled with `toggle wago`, **CurseBreaker** will try to update all detected WeakAuras and Plater profiles/scripts. The process works the same as WeakAuras Companion.\
All updates will still need to be applied in-game in the WeakAuras/Plater option menu.\
Command `toggle wago` can be used to set a single author name that will be ignored during the update.\
Additionally Wago API key can be set with the `set wa_api` command so non-public entries will also be upgradeable.

## KNOWN ISSUES
- Using WoWInterface projects that provide multiple addon releases ([example](https://www.wowinterface.com/downloads/info5086-BigWigsBossmods)) will always install a retail version of the addon. It can't be fixed as WoWInterface API doesn't support this type of project.
- Some WoWInterface addon categories (e.g. Compilations, Optional) are not handled by their API. Addons in these categories can't be installed.
- WoWInterface projects that need to install files outside the `Interface\AddOns` directory are not supported.

## COPYRIGHT
**CurseBreaker** is a free software/open source and is distributed under the GNU General Public License v3.

Icon made by [Nikita Golubev](https://www.flaticon.com/authors/nikita-golubev) is licensed by [CC 3.0 BY](http://creativecommons.org/licenses/by/3.0/).
