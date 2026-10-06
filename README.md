# archcare

[![CI](https://github.com/prithveerarya345/archcare/actions/workflows/ci.yml/badge.svg)](https://github.com/prithveerarya345/archcare/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Platform](https://img.shields.io/badge/platform-Arch%20Linux-1793d1?logo=archlinux&logoColor=white)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Keeps an Arch Linux machine updated, backed up, clean and healthy, with one Python CLI and a few systemd user timers.**

## Why

When I audited my own laptop after two years of daily-driving Arch, I found:

- **no backups at all**
- a **47 GB** pacman cache and **23 GB** in Trash
- 8 unmerged `.pacnew` files, including `sudoers` and `pacman.conf`
- an **API key pasted into my shell config**
- six package managers (pacman, yay, snap, npm, rustup, uv) that I updated by hand, when I remembered

archcare automates fixing all of that and runs it on systemd timers, so I never have to remember to.

## Features

| Command | What it does | Status |
|---|---|---|
| `archcare dots` | Inventory every dotfile, back up tracked ones to a git repo hourly, scan for secrets, restore on a new machine | ✅ done |
| `archcare install-timers` | Install and enable the systemd user timers | ✅ done |
| `archcare update` | pacman → yay (AUR) → snap → npm -g → rustup → uv tools → nvim plugins, plus a firmware check. Pre-flight: Arch News, disk space, battery. Post-flight: failed units, `.pacnew`, reboot needed | 🚧 next |
| `archcare clean` | paccache, orphans, Trash, `~/.cache`, journal, docker, Downloads. Dry run by default | planned |
| `archcare backup` | Encrypted, deduplicated restic backups of `~` and `/etc` to cloud storage, with a monthly test restore | planned |
| `archcare pacnew` | Diff and merge `.pacnew` files. `sudoers` validated with `visudo -c` before saving | planned |
| `archcare doctor` | One report: failed units, journal errors, disk, SSD SMART, battery health, mirrors, backup age | planned |
| `archcare explain` | Sends recent journal errors to an LLM and gets a plain-English cause and fix back. Read-only: it never runs anything | planned |

## Dotfiles: `archcare dots`

Every entry directly in `~` and `~/.config` is put in **exactly one** bucket, so nothing gets quietly forgotten:

| Bucket | Meaning |
|---|---|
| **tracked** | Copied into the dotfiles repo on every sync, organised in groups (shell, editor, desktop…) |
| **ignored** | App data and caches: browsers, toolchains, shell history |
| **secret** | Credentials (`~/.ssh`, `~/.gnupg`, CLI tokens). Never copied, even if also tracked |
| **untracked** | New and undecided. Listed by `dots status` until you choose |

```console
$ archcare dots status
tracked: 40  partial: 1  untracked: 124  ignored: 61  secret: 17
status     path                       group  size     note
untracked  ~/.config/calligrarc              5.9 KB
untracked  ~/.config/crewai                           no matching package: leftover?
untracked  ~/.config/go                      89.1 KB
untracked  ~/.config/mlflow                  158 B    no matching package: leftover?
...
Decide on untracked entries with archcare dots review.

$ archcare dots sync --dry-run
  + home/.config/nvim/init.lua
  + home/.config/hypr/hyprland.conf
  + etc/pacman.conf
  skip home/.bashrc (looks like it contains a secret: OpenAI-style key)
  skip etc/sudoers (needs root to check)

Would copy 105, removed 0, skipped 8.
```

### Commands

```sh
archcare dots status                    # what exists, what still needs a decision
archcare dots review                    # go through untracked entries: track / ignore / secret
archcare dots track ~/.config/foo -g tools
archcare dots ignore '~/.config/k*rc'   # globs work; tracked paths still win over ignore
archcare dots sync -n                   # dry run: what would be copied or skipped, and why
archcare dots sync                      # copy + git commit (runs hourly via timer)
archcare dots log ~/.bashrc             # history of one file
archcare dots restore editor            # dry run; add --yes to restore
```

### Design decisions

- **Copies, not symlinks.** Unlike GNU Stow or bare-repo setups, archcare never changes your setup. Delete the repo and nothing breaks.
- **Git as the history.** Each sync that changes something is one commit listing the files, so `archcare dots log ~/.bashrc` shows how a file evolved.
- **Defence in depth for secrets.** Known credential locations are excluded by the manifest. Every remaining file is also scanned for private keys and tokens (Anthropic, OpenAI, GitHub, AWS, Slack, Google, `*_API_KEY=`) before it's committed. A match skips the file and sends a desktop notification. The secret itself is never printed.
- **Captures system config too.** `/etc` files that pacman reports as `[modified]` from package defaults are backed up alongside your dotfiles.
- **Safe restores.** Restore is a dry run by default, and any file it overwrites is saved first.
- **Self-documenting.** An `INVENTORY.md` in the repo lists every group, path, file count and size, plus what was skipped and why.
- **The manifest stays yours.** `track`/`ignore` edit `~/.config/archcare/dotfiles.toml` with tomlkit, so your comments and ordering are kept.

## Install

```sh
git clone https://github.com/prithveerarya345/archcare && cd archcare
uv tool install -e .
archcare dots review       # one-time: decide on untracked entries
archcare install-timers    # hourly dotfile backups via systemd
```

The dotfiles repo lives at `~/.local/share/archcare/dotfiles`. To mirror it off-machine, add a **private** remote and set `push = true` in the manifest.

## Layout

```
src/archcare/
├── cli.py              # typer CLI
├── paths.py            # XDG locations, ~ expansion (injectable for tests)
├── notify.py           # desktop notifications (phone via ntfy: planned)
├── dots/
│   ├── manifest.py     # TOML manifest: groups / ignore / secret
│   ├── scan.py         # inventory + classification, pacman-modified /etc
│   ├── secrets.py      # credential pattern scanner
│   └── store.py        # git-backed sync, restore, INVENTORY.md
└── systemd/            # user .service/.timer units
```

## Development

```sh
uv run pytest          # tests run against a throwaway $HOME in tmp
uvx ruff check . && uvx ruff format --check .
```

CI runs lint plus tests on Python 3.11 to 3.13, and also inside an `archlinux:latest` container.

## License

[MIT](LICENSE)
