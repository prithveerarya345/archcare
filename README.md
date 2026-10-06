# archcare

[![CI](https://github.com/prithveerarya345/archcare/actions/workflows/ci.yml/badge.svg)](https://github.com/prithveerarya345/archcare/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Platform](https://img.shields.io/badge/platform-Arch%20Linux-1793d1?logo=archlinux&logoColor=white)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Keeps an Arch Linux machine updated, backed up, clean and healthy, with one Python CLI and four systemd user timers.**

## Why

When I audited my own laptop after two years of daily-driving Arch, I found:

- **no backups at all**
- **69 GB of reclaimable space**: a 39 GB pacman cache, 23 GB in Trash, a 3.9 GB journal
- 8 unmerged `.pacnew` files, including `sudoers` and `pacman.conf`
- a failed system service nobody had noticed, 64-day-old mirrors, and an API key pasted into my shell config
- seven package managers (pacman, yay, snap, npm, rustup, uv, nvim plugins) that I updated by hand, when I remembered

archcare automates fixing all of that and runs it on timers, so I never have to remember to.

## What it does

| Command | What it does |
|---|---|
| `archcare update` | **Pre-flight**: Arch News posts since your last upgrade (needs your OK), free disk, battery. **Update**: pacman → yay (AUR) → snap → npm -g → rustup → uv tools → nvim plugins, plus a firmware check. **Post-flight**: new `.pacnew` files, failed units, reboot needed, Python packages left behind by a version bump. |
| `archcare clean` | Measures, then (with `--yes`) cleans: old package versions, AUR build cache, orphans, Trash older than 30 days, stale `~/.cache`, oversized journal, dangling docker images. Downloads older than 90 days are **archived**, not deleted. |
| `archcare backup` | Encrypted, deduplicated [restic](https://restic.net) snapshots of `~` to any cloud restic supports, with daily/weekly/monthly retention. `backup verify` checks the stored data **and does a real test restore**. |
| `archcare dots` | Puts every dotfile in a bucket (tracked, ignored, secret or undecided), keeps hourly git-versioned copies of the tracked ones plus `/etc` files you've modified, and scans each file for leaked credentials before committing it. |
| `archcare pacnew` | Walks through `.pacnew` files with coloured diffs: keep yours, take the new default, or merge in `nvim -d`. `sudoers` is validated with `visudo` and **rolled back automatically** if invalid. |
| `archcare doctor` | One health report: failed units, journal errors, disk, SSD SMART + wear, battery health, last upgrade, reboot needed, mirror age, `.pacnew`, orphans, backup age, timers. Every warning comes with the command that fixes it. |
| `archcare explain` | Collects failed units, de-duplicated journal errors and the last pacman transaction, **redacts** usernames, hostname, IPs, MACs, emails and keys, and asks Claude for a plain-English cause and fix. It only suggests commands and never runs them. |
| `archcare restore-packages` | Reinstalls every repo + AUR package from the saved lists on a fresh install. |

### Runs by itself

`archcare install-timers` enables four systemd **user** timers (no root):

| Timer | Jobs |
|---|---|
| hourly | dotfile sync |
| daily | dotfile sync · count pending updates (never installs them: unattended upgrades are how Arch breaks) · backup · health check |
| weekly | report if over 5 GB can be cleaned |
| monthly | backup verification with a test restore |

Problems surface as desktop notifications and, optionally, **phone push notifications** via [ntfy](https://ntfy.sh). Every job is independent: one failing (say, offline during a backup) never stops the others. Timers are `Persistent=`, so jobs missed while the laptop was asleep run when it wakes.

## Examples

```console
$ archcare doctor
✗  failed services    user@965.service
                      → archcare explain
✓  disk /             42% used, 240 GB free
✓  SSD health         PASSED, 6% of rated life used
i  battery            83.3% of design capacity, 848 cycles
                      → cap charging at 80% to slow wear: echo 80 | sudo tee /sys/class/power_supply/BAT0/…
!  mirrorlist         updated 64 days ago
                      → sudo systemctl enable --now reflector.timer
!  .pacnew files      8 unmerged (pacman.conf.pacnew, sudoers.pacnew…)
                      → archcare pacnew
✗  backups            not configured
                      → set [backup] repository in config.toml, then archcare backup init

$ archcare clean
task             reclaim  what
pacman cache     38.7 GB  old versions beyond the newest 2, plus uninstalled packages
yay build cache   3.1 GB  54 AUR build folders
orphan packages        ?  61: accounts-qml-module, adobe-source-code-pro-fonts, aribb24, … +55 more
trash            22.6 GB  480 items trashed over 30 days ago
~/.cache          1.5 GB  77 folders untouched 60+ days
systemd journal   3.4 GB  shrink from 3.9G to 500M
Downloads              -  sort 244 items older than 90 days into archive/

~69.3 GB reclaimable.
Dry run. Run archcare clean --yes to clean (you'll be asked for sudo).

$ archcare update --check
  pacman   103
  yay      11
  snap     2
  npm      5

121 update(s) waiting.

$ archcare pacnew --list
  pacnew  /etc/pacman.conf  +6 -11
  pacnew  /etc/pacman.d/mirrorlist  +579 -30
  pacnew  /etc/sudoers  +2 -2  (validated before saving)
  …
```

## Install

```sh
git clone https://github.com/prithveerarya345/archcare && cd archcare
uv tool install -e .
archcare doctor            # see where you stand
archcare install-timers    # turn on the automatic jobs
```

### Backups

```sh
sudo pacman -S restic rclone
rclone config              # e.g. add a Google Drive remote called "gdrive"
# in ~/.config/archcare/config.toml:  [backup] repository = "rclone:gdrive:archcare"
archcare backup init       # creates a random password file; save it in your password manager
archcare backup            # first snapshot
archcare backup verify     # prove you can restore
```

### Phone alerts (optional)

Install the ntfy app, subscribe to a hard-to-guess topic, and set `[alerts] ntfy_topic = "…"` in `config.toml`.

### `explain`

Needs an Anthropic API key (`export ANTHROPIC_API_KEY=…` or `ant auth login`). Use `archcare explain --show` to see exactly what would be sent before anything leaves your machine.

## Dotfiles

```sh
archcare dots status                    # what exists, what still needs a decision
archcare dots review                    # go through undecided entries: track / ignore / secret
archcare dots ignore '~/.config/k*rc'   # globs work; tracked paths still win over ignore
archcare dots sync -n                   # dry run: what would be copied or skipped, and why
archcare dots log ~/.bashrc             # history of one file
archcare dots restore editor            # dry run; add --yes to restore (overwritten files are saved first)
```

The repo at `~/.local/share/archcare/dotfiles` holds `home/` (your tracked files), `etc/` (pacman-modified system config), `packages/` (package lists) and a generated `INVENTORY.md`. To mirror it off-machine, add a **private** remote and set `push = true` in `dotfiles.toml`.

## Design decisions

- **Safe by default.** `clean`, `dots restore` and `restore-packages` are dry runs unless you pass `--yes`, and `update --dry-run` shows the full plan. Measuring never needs root. Only the step that changes something asks for `sudo`.
- **No unattended upgrades.** Timers only count updates. Upgrading Arch without reading the news is the classic way to break it.
- **Reversible changes.** `pacnew` backs up before replacing and rolls back an invalid `sudoers`. Dotfiles are copies, not symlinks, so archcare never rearranges your setup.
- **Secrets never leave the machine.** Known credential locations are excluded, and every remaining file is pattern-scanned before a git commit. `explain` redacts identity and keys, shows you its payload with `--show`, and asks before sending.
- **Facts from the source.** The last-upgrade time comes from `pacman.log`, the need to reboot from the running kernel's missing module folder, and pending updates from `checkupdates` (it uses a temporary database copy, so it never touches the real one).
- **Failures are isolated.** Each `doctor` check and each timer job runs independently, so one crash can't hide the rest.

## Layout

```
src/archcare/
├── cli.py              # typer app: dots commands, install-timers, command registration
├── cmd/                # one module per command (update, clean, backup, pacnew, doctor, explain, run)
├── update.py           # package managers, Arch News, pre/post-flight checks
├── clean.py            # measure-then-apply cleanup tasks
├── backup.py           # restic wrapper, test restores
├── packages.py         # package list export/reinstall
├── pacnew.py           # diff, replace, merge, validate + rollback
├── doctor.py           # independent health checks
├── explain.py          # log collection, redaction, Claude call
├── system.py           # read-only machine facts (battery, disk, pacman.log, kernel…)
├── notify.py           # desktop + ntfy notifications
├── dots/               # manifest, scanner, secret detection, git-backed store
└── systemd/            # .service/.timer units
```

## Development

```sh
uv run pytest          # 45 tests; system commands are faked, $HOME is a temp dir
uvx ruff check . && uvx ruff format --check .
```

CI runs lint plus tests on Python 3.11 to 3.13, and also inside an `archlinux:latest` container.

## License

[MIT](LICENSE)
