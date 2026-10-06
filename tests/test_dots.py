import subprocess
from pathlib import Path

import pytest

from archcare.dots import manifest as mf
from archcare.dots import secrets, store
from archcare.dots.scan import Status, app_name, scan
from archcare.paths import Paths

MANIFEST = """
[settings]
max_file_kb = 1
backup_modified_etc = false
exclude = [".git", "*.log"]

[groups.shell]
paths = ["~/.bashrc", "~/.config/gone"]

[groups.editor]
paths = ["~/.config/nvim"]

[groups.svc]
paths = ["~/.config/systemd/user"]

[ignore]
paths = ["~/.config/chromium"]

[secret]
paths = ["~/.ssh"]
"""


@pytest.fixture
def home(tmp_path: Path) -> Paths:
    h = tmp_path / "home"
    (h / ".config" / "nvim" / "lua").mkdir(parents=True)
    (h / ".config" / "nvim" / "init.lua").write_text("require('core')\n")
    (h / ".config" / "nvim" / "lua" / "core.lua").write_text("vim.o.number = true\n")
    (h / ".config" / "nvim" / "debug.log").write_text("noise\n")
    (h / ".config" / "nvim" / ".git").mkdir()
    (h / ".config" / "nvim" / ".git" / "HEAD").write_text("ref\n")
    (h / ".config" / "systemd" / "user").mkdir(parents=True)
    (h / ".config" / "systemd" / "user" / "job.timer").write_text("[Timer]\n")
    (h / ".config" / "chromium").mkdir()
    (h / ".config" / "newapprc").write_text("x=1\n")
    (h / ".ssh").mkdir()
    (h / ".ssh" / "id_ed25519").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n")
    (h / ".bashrc").write_text("alias ll='ls -l'\n")
    p = Paths(h)
    p.manifest.parent.mkdir(parents=True)
    p.manifest.write_text(MANIFEST)
    return p


def statuses(p: Paths) -> dict[str, Status]:
    return {e.path: e.status for e in scan(p, mf.load(p.manifest), sizes=False)}


def test_classifies_every_entry(home: Paths) -> None:
    s = statuses(home)
    assert s["~/.bashrc"] is Status.TRACKED
    assert s["~/.config/nvim"] is Status.TRACKED
    assert s["~/.config/systemd"] is Status.PARTIAL
    assert s["~/.config/chromium"] is Status.IGNORED
    assert s["~/.ssh"] is Status.SECRET
    assert s["~/.config/newapprc"] is Status.UNTRACKED
    assert s["~/.config/gone"] is Status.MISSING
    # .config itself is a container, not an entry
    assert "~/.config" not in s


def test_secret_wins_over_tracked(home: Paths) -> None:
    mf.add(home.manifest, "groups", "~/.ssh", "shell")
    assert statuses(home)["~/.ssh"] is Status.SECRET


def test_track_and_ignore_edit_manifest_keeping_comments(home: Paths) -> None:
    home.manifest.write_text("# keep me\n" + MANIFEST)
    mf.add(home.manifest, "groups", "~/.config/newapprc", "misc")
    mf.add(home.manifest, "ignore", "~/.config/kgame*")
    text = home.manifest.read_text()
    assert "# keep me" in text
    m = mf.load(home.manifest)
    assert m.group_of("~/.config/newapprc") == "misc"
    assert m.is_ignored("~/.config/kgamerc")


def test_sync_copies_commits_and_is_idempotent(home: Paths) -> None:
    m = mf.load(home.manifest)
    res = store.sync(home, m)
    assert sorted(res.changed) == [
        "home/.bashrc",
        "home/.config/nvim/init.lua",
        "home/.config/nvim/lua/core.lua",
        "home/.config/systemd/user/job.timer",
    ]
    assert res.commit and "4 files changed" in res.commit
    repo = home.dots_repo
    assert not (repo / "home/.config/nvim/debug.log").exists()  # excluded glob
    assert not (repo / "home/.config/nvim/.git").exists()  # nested git skipped
    assert not (repo / "home/.ssh").exists()
    assert "## editor" in (repo / "INVENTORY.md").read_text()

    again = store.sync(home, m)
    assert again.changed == [] and again.commit is None


def test_sync_records_edits_and_deletions(home: Paths) -> None:
    m = mf.load(home.manifest)
    store.sync(home, m)
    (home.home / ".bashrc").write_text("alias ll='ls -la'\n")
    (home.home / ".config/nvim/lua/core.lua").unlink()
    res = store.sync(home, m)
    assert res.changed == ["home/.bashrc"]
    assert res.removed == ["home/.config/nvim/lua/core.lua"]
    log = subprocess.run(
        ["git", "-C", str(home.dots_repo), "log", "--oneline"], capture_output=True, text=True, check=True
    ).stdout
    assert len(log.splitlines()) == 2


def test_sync_skips_secrets_big_and_binary_files(home: Paths) -> None:
    (home.home / ".config/nvim/keys.lua").write_text('api_key = "abcdefghijklmnop1234"\n')
    (home.home / ".config/nvim/big.lua").write_text("x" * 2048)
    (home.home / ".config/nvim/font.bin").write_bytes(b"\0\1\2")
    res = store.sync(home, mf.load(home.manifest))
    reasons = dict(res.skipped)
    assert "secret" in reasons["home/.config/nvim/keys.lua"]
    assert "bigger than" in reasons["home/.config/nvim/big.lua"]
    assert reasons["home/.config/nvim/font.bin"] == "binary file"
    assert not (home.dots_repo / "home/.config/nvim/keys.lua").exists()


def test_dry_run_touches_nothing(home: Paths) -> None:
    res = store.sync(home, mf.load(home.manifest), dry_run=True)
    assert res.changed
    assert not home.dots_repo.exists()


def test_restore_backs_up_what_it_overwrites(home: Paths) -> None:
    m = mf.load(home.manifest)
    store.sync(home, m)
    (home.home / ".bashrc").write_text("broken\n")
    (home.home / ".config/nvim/init.lua").unlink()

    assert store.restore(home, m, None, dry_run=True) == [
        ("~/.bashrc", "overwrite"),
        ("~/.config/nvim/init.lua", "create"),
    ]
    assert (home.home / ".bashrc").read_text() == "broken\n"  # dry run

    store.restore(home, m, "editor", dry_run=False)  # group filter
    assert (home.home / ".config/nvim/init.lua").exists()
    assert (home.home / ".bashrc").read_text() == "broken\n"

    store.restore(home, m, None, dry_run=False)
    assert (home.home / ".bashrc").read_text() == "alias ll='ls -l'\n"
    saved = list((home.state_dir / "restore-backups").rglob(".bashrc"))
    assert saved and saved[0].read_text() == "broken\n"


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "export ANTHROPIC_API_KEY=sk-ant-api03-aaaaaaaaaaaaaaaaaaaaaaaa",
            ["Anthropic key", "OpenAI-style key", "assigned secret"],
        ),
        ("token: ghp_" + "a" * 36, ["GitHub token", "assigned secret"]),
        ("set number\nset tabstop=4\n", []),
        ("password_prompt = true", []),
    ],
)
def test_secret_patterns(tmp_path: Path, text: str, expected: list[str]) -> None:
    f = tmp_path / "f"
    f.write_text(text)
    assert secrets.scan(f) == expected


@pytest.mark.parametrize(
    "name,app", [(".vimrc", "vim"), ("kwinrc", "kwin"), ("starship.toml", "starship"), ("nvim", "nvim")]
)
def test_app_name(name: str, app: str) -> None:
    assert app_name(name) == app
