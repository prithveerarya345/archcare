"""Tests for update / clean / pacnew / doctor / backup / explain logic.

Nothing here touches the real system: commands go through archcare.sh, which is faked.
"""

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from archcare import backup, clean, doctor, explain, notify, pacnew, sh, system
from archcare import config as cfgmod
from archcare import update as upd
from archcare.paths import Paths


class FakeSh:
    """Maps a command prefix to a canned Result; records everything that ran."""

    def __init__(self, responses: dict[tuple[str, ...], sh.Result] | None = None):
        self.responses = responses or {}
        self.ran: list[list[str]] = []

    def capture(self, cmd, timeout=None, env=None):
        self.ran.append(cmd)
        for prefix, result in self.responses.items():
            if tuple(cmd[: len(prefix)]) == prefix:
                return result
        return sh.Result(127, "", "not faked")

    def interactive(self, cmd):
        self.ran.append(cmd)
        return 0


@pytest.fixture
def fake(monkeypatch) -> FakeSh:
    f = FakeSh()
    monkeypatch.setattr(sh, "capture", f.capture)
    monkeypatch.setattr(sh, "interactive", f.interactive)
    return f


# ---------------------------------------------------------------- config


def test_config_created_with_defaults_and_partial_overrides(tmp_path: Path) -> None:
    p = Paths(tmp_path)
    cfg = cfgmod.load(p)
    assert cfgmod.config_path(p).exists()
    assert cfg.update.managers[0] == "pacman" and cfg.clean.trash_days == 30

    cfgmod.config_path(p).write_text('[clean]\ntrash_days = 7\nunknown_key = 1\n[backup]\nrepository = "b2:x"\n')
    cfg = cfgmod.load(p)
    assert cfg.clean.trash_days == 7 and cfg.clean.cache_days == 60  # untouched keys keep defaults
    assert cfg.backup.repository == "b2:x"


# ---------------------------------------------------------------- update

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Old news</title><link>https://a/1</link><pubDate>Mon, 01 Sep 2026 10:00:00 +0000</pubDate></item>
<item><title>Manual intervention needed</title><link>https://a/2</link>
<pubDate>Fri, 03 Oct 2026 10:00:00 +0000</pubDate></item>
<item><title>Broken date</title><link>https://a/3</link><pubDate>whenever</pubDate></item>
</channel></rss>"""


def test_news_only_since_last_upgrade() -> None:
    since = datetime(2026, 9, 30, tzinfo=UTC)
    assert [n.title for n in upd.parse_news(RSS, since)] == ["Manual intervention needed"]
    assert len(upd.parse_news(RSS, None)) == 2  # unknown last upgrade: show everything parseable


def test_preflight_blocks_on_low_disk_and_battery(monkeypatch) -> None:
    monkeypatch.setattr(system, "last_full_upgrade", lambda: datetime(2026, 9, 30, tzinfo=UTC))
    cfg = cfgmod.UpdateCfg()
    full = system.Disk(total=100, used=95, free=5)
    flat = system.Battery("BAT0", capacity=12, charging=False, health=90, cycles=1, charge_limit=None)
    pf = upd.preflight(cfg, full, flat, RSS)
    assert len(pf.blockers) == 2
    assert [n.title for n in pf.news] == ["Manual intervention needed"]

    plugged = system.Battery("BAT0", capacity=12, charging=True, health=90, cycles=1, charge_limit=None)
    roomy = system.Disk(total=100, used=50, free=50)
    assert upd.preflight(cfg, roomy, plugged, None).blockers == []
    assert upd.preflight(cfg, roomy, plugged, None).notes  # offline: tells you to check news yourself


def test_plan_respects_config_order_only_skip_and_installed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sh, "have", lambda b: b in {"pacman", "yay", "npm", "rustup"})
    monkeypatch.setattr(upd, "_npm_needs_sudo", lambda: True)
    ms = upd.managers(tmp_path)
    cfg = cfgmod.UpdateCfg(managers=["yay", "pacman", "snap", "npm", "rustup"])
    assert [m.name for m in upd.plan(cfg, ms, [], [])] == ["yay", "pacman", "npm", "rustup"]
    assert [m.name for m in upd.plan(cfg, ms, ["pacman", "npm"], [])] == ["pacman", "npm"]
    assert [m.name for m in upd.plan(cfg, ms, [], ["yay"])] == ["pacman", "npm", "rustup"]
    assert ms["npm"].update[0] == "sudo"  # global npm prefix is root-owned


def test_count_pending(fake: FakeSh, tmp_path: Path) -> None:
    fake.responses = {
        ("checkupdates",): sh.Result(0, "linux 1 -> 2\nmesa 3 -> 4\n"),
        ("snap", "refresh", "--list"): sh.Result(0, "Name  Version\nfirefox 1\n"),
    }
    ms = upd.managers(tmp_path)
    assert upd.count_pending(ms["pacman"]) == 2
    assert upd.count_pending(ms["snap"]) == 1
    fake.responses = {("checkupdates",): sh.Result(2, "")}
    assert upd.count_pending(ms["pacman"]) == 0


def test_run_manager_reports_failure(monkeypatch) -> None:
    monkeypatch.setattr(sh, "interactive", lambda cmd: 1)
    r = upd.run_manager(upd.Manager("pacman", "pacman", ["sudo", "pacman", "-Syu"]))
    assert r.status == "failed" and "exit code 1" in r.detail


# ---------------------------------------------------------------- system facts


def test_last_full_upgrade_from_pacman_log(tmp_path: Path) -> None:
    log = tmp_path / "pacman.log"
    log.write_text(
        "[2026-09-01T10:00:00+0530] [PACMAN] starting full system upgrade\n"
        "[2026-09-30T00:19:10+0530] [PACMAN] starting full system upgrade\n"
        "[2026-09-30T01:00:14+0530] [ALPM] transaction completed\n"
    )
    assert system.last_full_upgrade(log).isoformat() == "2026-09-30T00:19:10+05:30"
    assert system.last_full_upgrade(tmp_path / "missing") is None


def test_battery_health_and_ac(tmp_path: Path) -> None:
    bat, ac = tmp_path / "BAT0", tmp_path / "AC"
    bat.mkdir()
    ac.mkdir()
    for k, v in {
        "capacity": 80,
        "status": "Discharging",
        "energy_full": 42020000,
        "energy_full_design": 50450000,
        "cycle_count": 848,
        "charge_control_end_threshold": 100,
    }.items():
        (bat / k).write_text(f"{v}\n")
    (ac / "online").write_text("0\n")
    b = system.battery(tmp_path)
    assert (b.capacity, b.charging, b.health, b.cycles, b.charge_limit) == (80, False, 83.3, 848, 100)
    (ac / "online").write_text("1\n")
    assert system.battery(tmp_path).charging


def test_reboot_needed(tmp_path: Path) -> None:
    assert system.reboot_needed(tmp_path)
    (tmp_path / os.uname().release).mkdir()
    assert not system.reboot_needed(tmp_path)


# ---------------------------------------------------------------- clean


def test_parse_size() -> None:
    assert clean.parse_size("37.46 GiB") == int(37.46 * 1024**3)
    assert clean.parse_size("3.9G") == int(3.9 * 1024**3)
    assert clean.parse_size("500M") == 500 * 1024**2
    assert clean.parse_size("nothing") == 0


def test_trash_uses_deletion_date_not_file_age(tmp_path: Path) -> None:
    base = tmp_path / ".local/share/Trash"
    (base / "info").mkdir(parents=True)
    (base / "files").mkdir()
    now = datetime(2026, 10, 7).timestamp()
    for name, deleted in [("old.txt", "2026-08-01T10:00:00"), ("new.txt", "2026-10-05T10:00:00")]:
        (base / "files" / name).write_text("x" * 100)
        (base / "info" / f"{name}.trashinfo").write_text(f"[Trash Info]\nPath=/x/{name}\nDeletionDate={deleted}\n")
    task = clean.trash(tmp_path, cfgmod.CleanCfg(trash_days=30), now=now)
    assert task.reclaim == 100 and "1 items" in task.summary
    task.apply()
    assert not (base / "files/old.txt").exists() and not (base / "info/old.txt.trashinfo").exists()
    assert (base / "files/new.txt").exists()


def test_user_cache_only_removes_untouched_folders(tmp_path: Path) -> None:
    cache = tmp_path / ".cache"
    for name in ("stale", "fresh", "yay"):
        (cache / name).mkdir(parents=True)
        (cache / name / "f").write_text("data")
    old = time.time() - 90 * 86400
    for p in [cache / "stale", cache / "stale/f", cache / "yay", cache / "yay/f"]:
        os.utime(p, (old, old))
    task = clean.user_cache(tmp_path, cfgmod.CleanCfg(cache_days=60))
    assert "1 folders" in task.summary  # yay has its own task
    task.apply()
    assert not (cache / "stale").exists() and (cache / "fresh").exists() and (cache / "yay").exists()


def test_downloads_are_archived_not_deleted(tmp_path: Path) -> None:
    dl = tmp_path / "Downloads"
    dl.mkdir()
    (dl / "old.pdf").write_text("pdf")
    (dl / "new.pdf").write_text("pdf")
    old = datetime(2026, 1, 15).timestamp()
    os.utime(dl / "old.pdf", (old, old))
    task = clean.downloads(tmp_path, cfgmod.CleanCfg(downloads_days=90), now=datetime(2026, 10, 7).timestamp())
    task.apply()
    assert (dl / "archive/2026-01/old.pdf").read_text() == "pdf"
    assert (dl / "new.pdf").exists()


def test_pacman_cache_measures_with_dry_runs(fake: FakeSh) -> None:
    fake.responses = {
        ("paccache", "-d"): sh.Result(0, "==> finished dry run: 72 candidates (disk space saved: 1.50 GiB)"),
        ("paccache", "-du"): sh.Result(0, "==> finished dry run: 9 candidates (disk space saved: 512.00 MiB)"),
    }
    task = clean.pacman_cache(cfgmod.CleanCfg())
    assert task.reclaim == int(1.5 * 1024**3) + 512 * 1024**2
    assert not any(c[0] == "sudo" for c in fake.ran)  # measuring never needs root
    task.apply()
    assert ["sudo", "paccache", "-r", "-k2"] in fake.ran


def test_journal_task(fake: FakeSh) -> None:
    fake.responses = {
        ("journalctl", "--disk-usage"): sh.Result(0, "Archived and active journals take up 3.9G in the file system.")
    }
    task = clean.journal(cfgmod.CleanCfg(journal_max="500M"))
    assert task.reclaim == int(3.9 * 1024**3) - 500 * 1024**2


# ---------------------------------------------------------------- pacnew


def test_pacnew_pairs_and_sudoers_validation() -> None:
    items = pacnew.pending([Path("/etc/pacman.conf.pacnew"), Path("/etc/sudoers.pacnew"), Path("/etc/x.conf.pacsave")])
    assert [str(p.current) for p in items] == ["/etc/pacman.conf", "/etc/sudoers", "/etc/x.conf"]
    assert items[0].validator is None
    assert items[1].validator == ["visudo", "-c", "-f", "/etc/sudoers"]
    assert items[2].kind == "pacsave"
    assert pacnew.Pending(Path("/etc/sudoers.d/10.pacnew"), Path("/etc/sudoers.d/10")).validator


def test_pacnew_diffstat() -> None:
    assert pacnew.diffstat(["--- a", "+++ b", "@@", "-old", "+new", "+more", " same"]) == (2, 1)


def test_invalid_sudoers_is_rolled_back(monkeypatch) -> None:
    ran = []

    def interactive(cmd):
        ran.append(cmd)
        return 1 if "visudo" in cmd else 0  # validation fails

    monkeypatch.setattr(sh, "interactive", interactive)
    p = pacnew.Pending(Path("/etc/sudoers.pacnew"), Path("/etc/sudoers"))
    assert pacnew.use_new(p) is False
    assert ran[-1][:3] == ["sudo", "mv", "--"] and ran[-1][-1] == "/etc/sudoers"  # backup moved back


# ---------------------------------------------------------------- doctor


def test_doctor_survives_a_crashing_check(monkeypatch, tmp_path: Path) -> None:
    def boom():
        raise RuntimeError("nope")

    monkeypatch.setattr(doctor, "all_checks", lambda paths, cfg: [boom, lambda: doctor.Check("x", doctor.WARN, "y")])
    results = doctor.run(Paths(tmp_path), cfgmod.Config())
    assert results[0].level == doctor.INFO and "crashed" in results[0].detail
    assert doctor.worst(results) == doctor.WARN


def test_doctor_backups_check(tmp_path: Path) -> None:
    p = Paths(tmp_path)
    assert doctor.backups(p, cfgmod.Config()).level == doctor.FAIL  # not configured
    cfg = cfgmod.Config(backup=cfgmod.BackupCfg(repository="b2:x"))
    from archcare import state

    state.set_(p, last_backup=time.time() - 3600)
    assert doctor.backups(p, cfg).level == doctor.OK
    state.set_(p, last_backup=time.time() - 10 * 86400)
    assert doctor.backups(p, cfg).level == doctor.WARN


# ---------------------------------------------------------------- backup


def test_backup_args_expand_home_and_excludes(tmp_path: Path) -> None:
    p = Paths(tmp_path)
    cfg = cfgmod.BackupCfg(repository="b2:x", paths=["~"], exclude=["~/.cache", "**/node_modules"])
    args = backup.backup_args(cfg, p)
    assert args[:2] == ["restic", "backup"]
    assert ["--exclude", f"{tmp_path}/.cache"] == args[
        args.index(f"{tmp_path}/.cache") - 1 : args.index(f"{tmp_path}/.cache") + 1
    ]
    assert "**/node_modules" in args and args[-1] == str(tmp_path)
    assert "--keep-daily" in backup.forget_args(cfg)


def test_password_file_is_private_and_not_overwritten(tmp_path: Path) -> None:
    p = Paths(tmp_path)
    cfg = cfgmod.BackupCfg(repository="b2:x")
    pw, created = backup.ensure_password(cfg, p)
    assert created and oct(pw.stat().st_mode & 0o777) == "0o600"
    first = pw.read_text()
    assert backup.ensure_password(cfg, p) == (pw, False) and pw.read_text() == first


def test_backup_requires_repository(tmp_path: Path) -> None:
    with pytest.raises(backup.BackupError):
        backup.env(cfgmod.BackupCfg(), Paths(tmp_path))


def test_parse_restic_summary() -> None:
    summary = {
        "message_type": "summary",
        "files_new": 3,
        "files_changed": 1,
        "data_added": 2048,
        "total_bytes_processed": 9999,
        "snapshot_id": "abcdef1234",
    }
    out = '{"message_type":"status","percent_done":0.5}\n' + json.dumps(summary) + "\n"
    s = backup.parse_summary(out)
    assert (s.files_new, s.files_changed, s.data_added, s.snapshot) == (3, 1, 2048, "abcdef12")


# ---------------------------------------------------------------- explain


def test_dedupe_collapses_repeats() -> None:
    lines = [
        "2026-10-07T10:00:00+0530 host kernel: ACPI Error at 0x1f",
        "2026-10-07T10:00:05+0530 host kernel: ACPI Error at 0x2a",
        "2026-10-07T10:00:06+0530 host app[123]: crashed",
    ]
    assert explain.dedupe(lines) == ["host kernel: ACPI Error at 0x…  (x2)", "host app: crashed"]


def test_redact_hides_identity_but_keeps_unit_names() -> None:
    text = (
        "Oct 04 mybox systemd: user@965.service failed for alice; mail alice@example.com from 192.168.1.4 "
        "mac aa:bb:cc:dd:ee:ff key sk-ant-api03-" + "x" * 30
    )
    out = explain.redact(text, user="alice", host="mybox")
    assert "user@965.service" in out
    for leaked in ("alice", "mybox", "192.168.1.4", "aa:bb", "sk-ant-api03"):
        assert leaked not in out
    assert "<email>" in out and "<ip>" in out and "<mac>" in out and "<host>" in out


def test_last_transaction(tmp_path: Path) -> None:
    log = tmp_path / "pacman.log"
    log.write_text(
        "[t] [PACMAN] starting full system upgrade\n[t] [ALPM] upgraded old (1 -> 2)\n"
        "[t] [PACMAN] starting full system upgrade\n[t] [ALPM] upgraded linux (7.1 -> 7.2)\n"
        "[t] [ALPM] upgraded mesa (1 -> 2)\n[t] [ALPM] warning: /etc/x installed as /etc/x.pacnew\n"
    )
    out = explain.last_transaction(log)
    assert "... 2 packages upgraded" in out
    assert any("upgraded linux" in ln for ln in out) and any("pacnew" in ln for ln in out)


def test_ask_sends_redacted_context_and_handles_refusal(monkeypatch) -> None:
    import anthropic

    sent = {}

    class FakeMessages:
        def __init__(self, response):
            self.response = response

        def create(self, **kwargs):
            sent.update(kwargs)
            return self.response

    def client_with(response):
        return lambda: SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages(response)))

    ok = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="All good.")])
    monkeypatch.setattr(anthropic, "Anthropic", client_with(ok))
    assert explain.ask("ctx", "claude-opus-5-5") == "All good."
    assert sent["model"] == "claude-opus-5-5" and sent["fallbacks"] == "default"
    assert sent["messages"] == [{"role": "user", "content": "ctx"}]

    refused = SimpleNamespace(stop_reason="refusal", content=[])
    monkeypatch.setattr(anthropic, "Anthropic", client_with(refused))
    with pytest.raises(explain.ExplainError):
        explain.ask("ctx", "claude-opus-5-5")


# ---------------------------------------------------------------- notify


def test_ntfy_request() -> None:
    req = notify.ntfy_request(cfgmod.AlertsCfg(ntfy_topic="my-topic"), "Disk – full", "90% used", urgent=True)
    assert req.full_url == "https://ntfy.sh/my-topic"
    assert req.data == b"90% used" and req.get_header("Priority") == "high"
    assert req.get_header("Title").isascii()
