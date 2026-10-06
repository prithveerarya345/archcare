"""The dotfiles manifest: which config is tracked, ignored, or secret.

Lives at ~/.config/archcare/dotfiles.toml. Edited with tomlkit so your own
comments and ordering survive `archcare dots track/ignore`.
"""

from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

import tomlkit

DEFAULT_MANIFEST = """\
# archcare dotfiles manifest
#
# Every entry directly inside ~ (dotfiles) and ~/.config ends up in exactly one bucket:
#   groups.*  -> tracked: copied into the dotfiles git repo on every sync
#   ignore    -> app data / caches you never want backed up
#   secret    -> credentials: never copied, never committed
# Anything else shows up in `archcare dots status` as UNTRACKED until you decide.
# Patterns use shell globs and the ~/ form, e.g. "~/.config/akonadi*".

[settings]
max_file_kb = 512            # skip single files bigger than this
backup_modified_etc = true   # also back up /etc files you changed from package defaults
push = false                 # git push the dotfiles repo after each sync (set a private remote first)
package_lists = true         # save pacman/AUR/snap package lists into the repo (for restore-packages)
exclude = [".git", "__pycache__", "node_modules", "*.log", "*.swp", "*.bak", "*.backup"]

[groups.shell]
paths = ["~/.bashrc", "~/.bash_profile", "~/.bash_logout", "~/.profile", "~/.zshenv", "~/.config/starship.toml"]

[groups.editor]
paths = ["~/.config/nvim", "~/.vimrc", "~/.vim"]
exclude = ["plugged"]   # vim-plug re-downloads these from .vimrc

[groups.terminal]
paths = ["~/.config/kitty", "~/.config/konsolerc", "~/.config/yakuakerc"]

[groups.git]
paths = ["~/.gitconfig", "~/.config/git"]

[groups.desktop]
paths = [
  "~/.config/hypr",
  "~/.config/kdeglobals",
  "~/.config/kwinrc",
  "~/.config/kwinrulesrc",
  "~/.config/kglobalshortcutsrc",
  "~/.config/plasma-org.kde.plasma.desktop-appletsrc",
  "~/.config/plasmarc",
  "~/.config/plasmashellrc",
  "~/.config/kxkbrc",
  "~/.config/kcminputrc",
  "~/.config/touchpadxlibinputrc",
  "~/.config/powerdevilrc",
  "~/.config/kscreenlockerrc",
  "~/.config/dolphinrc",
  "~/.config/mimeapps.list",
  "~/.config/user-dirs.dirs",
  "~/.gtkrc-2.0",
  "~/.config/gtk-3.0",
  "~/.config/gtk-4.0",
]

[groups.tools]
paths = [
  "~/.config/btop",
  "~/.config/htop",
  "~/.config/mpv",
  "~/.config/cava",
  "~/.config/neofetch",
  "~/.config/yay",
  "~/.config/systemd/user",
  "~/.config/archcare",
]

[ignore]
paths = [
  # browsers and electron apps: huge, and they sync themselves
  "~/.mozilla", "~/.config/BraveSoftware", "~/.config/chromium", "~/.config/google-chrome",
  "~/.config/microsoft-edge", "~/.config/opera", "~/.config/vivaldi", "~/.config/falkon",
  "~/.config/angelfish*", "~/.config/Google", "~/.config/Signal", "~/.config/obsidian",
  "~/.config/Postman", "~/.config/StarUML", "~/.config/Code", "~/.config/Code - OSS",
  "~/.config/Cursor", "~/.config/Antigravity", "~/.config/Codex", "~/.config/codex-*",
  # toolchains, caches, history, runtime state
  "~/.cache", "~/.local", "~/.cargo", "~/.rustup", "~/.npm", "~/.yarn", "~/.dotnet", "~/.java",
  "~/.android", "~/.electron-gyp", "~/.dspy_cache", "~/.var", "~/.vscode*", "~/.cursor",
  "~/.antigravity", "~/.codex*", "~/.pi", "~/.th-client",
  "~/.bash_history*", "~/.python_history", "~/.viminfo",
  "~/.config/akonadi*", "~/.config/session", "~/.config/pulse", "~/.config/dconf",
  "~/.config/configstore", "~/.config/Unknown Organization", "~/.config/*.backup",
]

[secret]
paths = [
  "~/.ssh", "~/.gnupg", "~/.pki", "~/.cert", "~/.anydesk",
  "~/.claude*", "~/.gemini", "~/.copilot",
  "~/.config/gh", "~/.config/ngrok", "~/.config/kdeconnect", "~/.config/kwalletrc",
]
"""


@dataclass
class Group:
    name: str
    paths: list[str]
    exclude: list[str] = field(default_factory=list)


@dataclass
class Manifest:
    groups: dict[str, Group]
    ignore: list[str]
    secret: list[str]
    max_file_kb: int = 512
    backup_modified_etc: bool = True
    push: bool = False
    package_lists: bool = True
    exclude: list[str] = field(default_factory=list)

    def group_of(self, tilde_path: str) -> str | None:
        """Group that tracks this exact path or one of its parents."""
        for g in self.groups.values():
            for p in g.paths:
                if tilde_path == p or tilde_path.startswith(p.rstrip("/") + "/"):
                    return g.name
        return None

    def partly_tracked(self, tilde_path: str) -> bool:
        """True if something *inside* this path is tracked (e.g. ~/.config/systemd)."""
        prefix = tilde_path.rstrip("/") + "/"
        return any(p.startswith(prefix) for g in self.groups.values() for p in g.paths)

    def is_secret(self, tilde_path: str) -> bool:
        return _matches(tilde_path, self.secret)

    def is_ignored(self, tilde_path: str) -> bool:
        return _matches(tilde_path, self.ignore)


def _matches(tilde_path: str, patterns: list[str]) -> bool:
    return any(fnmatch(tilde_path, pat) or tilde_path.startswith(pat.rstrip("/") + "/") for pat in patterns)


def init(path: Path) -> bool:
    """Write the default manifest. Returns False if one already exists."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_MANIFEST)
    return True


def load(path: Path) -> Manifest:
    doc = tomlkit.parse(path.read_text()).unwrap()
    settings = doc.get("settings", {})
    groups = {
        name: Group(name, list(g.get("paths", [])), list(g.get("exclude", [])))
        for name, g in doc.get("groups", {}).items()
    }
    return Manifest(
        groups=groups,
        ignore=list(doc.get("ignore", {}).get("paths", [])),
        secret=list(doc.get("secret", {}).get("paths", [])),
        max_file_kb=int(settings.get("max_file_kb", 512)),
        backup_modified_etc=bool(settings.get("backup_modified_etc", True)),
        push=bool(settings.get("push", False)),
        package_lists=bool(settings.get("package_lists", True)),
        exclude=list(settings.get("exclude", [])),
    )


def add(path: Path, section: str, entry: str, group: str | None = None) -> None:
    """Append `entry` to [groups.<group>], [ignore] or [secret], keeping comments."""
    doc = tomlkit.parse(path.read_text())
    if section == "groups":
        assert group, "group name required"
        groups = doc.setdefault("groups", tomlkit.table(is_super_table=True))
        if group not in groups:
            groups[group] = tomlkit.table()
            groups[group]["paths"] = tomlkit.array()
        arr = groups[group]["paths"]
    else:
        arr = doc.setdefault(section, tomlkit.table()).setdefault("paths", tomlkit.array())
    if entry not in arr:
        arr.append(entry)
    path.write_text(tomlkit.dumps(doc))
