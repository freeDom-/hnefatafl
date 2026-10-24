"""Execute host scripts with isolated command fixtures; never touch real LXCs."""

import io
import gzip
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "scripts/proxmox/ct"
REQUIRED = (
    "scripts/proxmox/ct/hnefatafl.sh",
    "scripts/proxmox/install/hnefatafl-install.sh",
    "scripts/linux/install.sh",
    "scripts/linux/update.sh",
    "scripts/linux/backup.sh",
    "hnefatafl/deployment.py",
)

# Every Proxmox command, including all mutations, is a fixture executable. The
# restricted PATH also prevents accidental fallback to an actual host command.
DISPATCH = r"""
import json, os, pathlib, shutil, stat, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
mask = os.umask(0)
os.umask(mask)
with open(os.environ['MASKS'], 'a') as log:
    log.write(json.dumps([name, args, mask]) + '\n')
with open(os.environ['CALLS'], 'a') as log:
    log.write(json.dumps([name, args, os.getcwd()]) + '\n')
case = os.environ.get('CASE', '')
if name == 'pveversion':
    print('pve-manager/9.0')
    sys.exit(9 if case == 'host' else 0)
if name == 'curl':
    if case == 'download': sys.exit(22)
    shutil.copyfile(os.environ['ARCHIVE'], args[args.index('--output') + 1])
    print('302' if case == 'redirect' else '200', end='')
if name == 'pvesh':
    print('[{"vmid":185}]' if case == 'exists' else '[]') if '/cluster/resources' in args else print('185')
if name == 'pvesm':
    pool = args[args.index('--storage') + 1]
    print('Name Type Status Total Used Available %')
    print(pool, 'lvmthin', 'inactive' if case == 'storage' else 'active', '100000 95000 5000 95%' if case == 'full-storage' else '100000 50000 50000 50%')
if name == 'ip': sys.exit(1 if case == 'bridge' else 0)
if name == 'pveam':
    if args[0] == 'available' and case != 'template':
        print('system debian-13-standard_13.0-1_amd64.tar.zst')
    if args[0] == 'download' and case == 'template-download': sys.exit(8)
    if args[0] == 'list' and case == 'cached':
        print('local:vztmpl/debian-13-standard_13.0-1_amd64.tar.zst 123')
if name == 'pct':
    if args[0] == 'create' and case == 'umask':
        # Like PVE::LXC::mount_all / File::Path::make_path, use the
        # inherited umask. Model traversal by mapped root (host UID 100000).
        staging = pathlib.Path(os.environ['PROVISIONING']) / args[1]
        (staging / 'rootfs').mkdir(parents=True)
        if not staging.stat().st_mode & stat.S_IXOTH:
            print('tar: /var/lib/lxc/' + args[1] + '/rootfs: Cannot open: Permission denied')
            sys.exit(17)
    if args[0] == 'config' and case == 'missing': sys.exit(3)
    if args[0] == 'status':
        if case == 'status-error': sys.exit(7)
        print('status: stopped' if case == 'stopped' else 'status: running')
    if args[0] == 'create' and case in {'create', 'permission'}:
        print('tar: /var/lib/lxc/185/rootfs: Cannot open: Permission denied' if case == 'permission' else 'unable to create CT 185')
        sys.exit(17)
    if args[0] == 'start' and case == 'start': sys.exit(18)
    if args[0] == 'push' and case == 'push': sys.exit(19)
    if args[0] == 'push': shutil.copyfile(args[2], os.environ['TRANSFERRED'])
    if args[0] == 'exec':
        cmd = args[args.index('--') + 1:]
        if cmd[:2] == ['test', '-f']:
            if case == 'no-config' and cmd[-1].endswith('deployment.json'): sys.exit(1)
            if case == 'no-module' and cmd[-1].endswith('deployment.py'): sys.exit(1)
        if cmd[:2] == ['test', '-x'] and case in {'no-updater', 'no-backup'} and (case == 'no-updater' or cmd[-1].endswith('hnefatafl-backup')): sys.exit(1)
        if cmd[:2] == ['bash', '-lc'] and case == 'no-cli-path': sys.exit(24)
        if cmd[0] == '/usr/local/bin/hnefatafl-update': sys.exit(int(os.environ.get('UPDATE_STATUS', '0')))
        if cmd[0] == 'bash' and case == 'install': sys.exit(20)
        if cmd[0] == 'systemctl':
            if case == cmd[1]: sys.exit(21)
            print('enabled' if cmd[1] == 'is-enabled' else 'active')
        if cmd[0] == 'python3':
            if '.probe()' in cmd[-1]:
                if case == 'probe': sys.exit(23)
            else: print('Installed release: 0.22.1\nApplication port: 8000')
        if cmd[0] == 'hostname': print('192.0.2.185')
"""


@pytest.fixture
def host(tmp_path):
    checkout = tmp_path / "checkout"
    for name in (
        *REQUIRED,
        "scripts/proxmox/ct/hnefatafl-pve-install.sh",
        "scripts/proxmox/ct/hnefatafl-pve-update.sh",
    ):
        target = checkout / name
        target.parent.mkdir(parents=True, exist_ok=True)
        # CI runs as an ordinary user. Only substitute the root identity check
        # in fixture copies; all commands and the rest of each script run as-is.
        target.write_text((ROOT / name).read_text().replace("$EUID", "0"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for command in (
        "bash",
        "python3",
        "mktemp",
        "rm",
        "dirname",
        "tar",
        "gzip",
        "awk",
        "sort",
        "tail",
        "cat",
        "tee",
    ):
        source = sys.executable if command == "python3" else shutil.which(command)
        (bin_dir / command).symlink_to(source)
    for command in ("pct", "pveam", "pvesh", "pvesm", "pveversion", "ip", "curl"):
        executable = bin_dir / command
        executable.write_text(f"#!{sys.executable}\n" + DISPATCH)
        executable.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(bin_dir),
        "CALLS": str(tmp_path / "calls.jsonl"),
        "MASKS": str(tmp_path / "masks.jsonl"),
        "PROVISIONING": str(tmp_path / "provisioning"),
        "ARCHIVE": str(tmp_path / "source.tar.gz"),
        "DELEGATE": str(tmp_path / "delegate"),
        "TRANSFERRED": str(tmp_path / "transferred.tar.gz"),
        "TMPDIR": str(tmp_path),
    }
    # Do not inherit test controls or a caller's default target container.
    for key in ("CASE", "UPDATE_STATUS", "DELEGATE_STATUS", "HNEFATAFL_CTID"):
        env.pop(key, None)

    class Host:
        def run(
            self, script, *args, input="\n", process_substitution=False, caller_umask=-1, **changes
        ):
            command = [str(bin_dir / "bash"), str(checkout / "scripts/proxmox/ct" / script), *args]
            if process_substitution:
                command = [command[0], "-c", 'bash <(cat "$1") "${@:2}"', "bootstrap", *command[1:]]
            return subprocess.run(
                command,
                input=input,
                text=True,
                capture_output=True,
                env={**env, **changes},
                cwd=tmp_path,
                umask=caller_umask,
            )

        def calls(self, name=None):
            log = Path(env["CALLS"])
            calls = (
                [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            )
            return [c[1] for c in calls if name is None or c[0] == name]

        def archive(self, *, omit=None, extra=None, script=None):
            with tarfile.open(env["ARCHIVE"], "w:gz") as archive:
                for name in REQUIRED:
                    if name == omit:
                        continue
                    content = b"#!/bin/bash\nexit 0\n"
                    if name == REQUIRED[0]:
                        content = script or (
                            b'#!/bin/bash\nprintf "%s\\n" "$PWD" "$@" > "$DELEGATE"\n'
                            b'exit "${DELEGATE_STATUS:-0}"\n'
                        )
                    entry = tarfile.TarInfo("hnefatafl-main/" + name)
                    entry.size = len(content)
                    archive.addfile(entry, io.BytesIO(content))
                if extra:
                    entry, content = extra
                    archive.addfile(entry, io.BytesIO(content))

    fixture = Host()
    fixture.env = env
    fixture.directory = tmp_path
    fixture.bin = bin_dir
    fixture.checkout = checkout
    fixture.archive()
    return fixture


def test_bootstrap_from_unrelated_directory_forwards_arguments_and_cleans(host):
    result = host.run("hnefatafl-pve-install.sh", "two words", "$(touch should-not-exist)")
    assert result.returncode == 0, result.stderr
    delegated = Path(host.env["DELEGATE"]).read_text().splitlines()
    assert delegated[0].endswith("/repo")
    assert delegated[1:] == ["two words", "$(touch should-not-exist)"]
    assert not Path(delegated[0]).exists()
    curl = host.calls("curl")[0]
    assert curl[-1] == "https://codeload.github.com/freeDom-/hnefatafl/tar.gz/refs/heads/main"
    assert curl[curl.index("--proto") + 1] == "=https"
    assert "--location" not in curl
    assert not host.calls("pct")
    assert not list(host.directory.glob("hnefatafl-pve-bootstrap.*"))


@pytest.mark.parametrize("case,code", [("download", 22), ("redirect", 1), ("host", 9)])
def test_bootstrap_download_and_host_failures(host, case, code):
    result = host.run("hnefatafl-pve-install.sh", CASE=case)
    assert result.returncode == code
    assert not Path(host.env["DELEGATE"]).exists()
    assert not list(host.directory.glob("hnefatafl-pve-bootstrap.*"))


def test_bootstrap_missing_command(host):
    (host.bin / "curl").unlink()
    result = host.run("hnefatafl-pve-install.sh")
    assert result.returncode != 0
    assert "Missing required command: curl" in result.stderr


@pytest.mark.parametrize("bad", ["html", "missing", "syntax"])
def test_bootstrap_rejects_invalid_contents(host, bad):
    if bad == "html":
        Path(host.env["ARCHIVE"]).write_text("<html>not a tarball</html>")
    elif bad == "missing":
        host.archive(omit=REQUIRED[-1])
    else:
        host.archive(script=b"#!/bin/bash\nif then\n")
    result = host.run("hnefatafl-pve-install.sh")
    assert result.returncode != 0
    assert not Path(host.env["DELEGATE"]).exists()
    assert not list(host.directory.glob("hnefatafl-pve-bootstrap.*"))


@pytest.mark.parametrize(
    "name,kind",
    [
        ("hnefatafl-main/../../escape", tarfile.REGTYPE),
        ("/tmp/escape", tarfile.REGTYPE),
        ("other-repo/file", tarfile.REGTYPE),
        ("hnefatafl-main/./file", tarfile.REGTYPE),
        ("hnefatafl-main/a\\b", tarfile.REGTYPE),
        ("hnefatafl-main/link", tarfile.SYMTYPE),
        ("hnefatafl-main/hard", tarfile.LNKTYPE),
        ("hnefatafl-main/device", tarfile.CHRTYPE),
        ("hnefatafl-main/fifo", tarfile.FIFOTYPE),
        ("hnefatafl-main/scripts/linux/install.sh", tarfile.REGTYPE),
    ],
)
def test_bootstrap_rejects_unsafe_archive_entries(host, name, kind):
    entry = tarfile.TarInfo(name)
    entry.type = kind
    entry.linkname = "/etc/passwd" if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ""
    host.archive(extra=(entry, b""))
    result = host.run("hnefatafl-pve-install.sh")
    assert result.returncode != 0
    assert "unsafe archive entry" in result.stderr
    assert not Path(host.env["DELEGATE"]).exists()
    assert not list(host.directory.glob("hnefatafl-pve-bootstrap.*"))


def test_bootstrap_propagates_delegate_failure(host):
    result = host.run("hnefatafl-pve-install.sh", DELEGATE_STATUS="37")
    assert result.returncode == 37
    assert "Proxmox installation failed" in result.stderr
    assert not list(host.directory.glob("hnefatafl-pve-bootstrap.*"))


def test_bootstrap_process_substitution_preserves_interactive_stdin(host):
    host.archive(
        script=b'#!/bin/bash\nread -r -p "Setup value: " value\nprintf "%s" "$value" > "$DELEGATE"\n'
    )
    result = host.run(
        "hnefatafl-pve-install.sh", input="interactive input\n", process_substitution=True
    )
    assert result.returncode == 0, result.stderr
    assert Path(host.env["DELEGATE"]).read_text() == "interactive input"


def test_bootstrap_to_native_installer_transfers_expected_layout(host):
    host.archive(script=(host.checkout / REQUIRED[0]).read_bytes())
    result = host.run("hnefatafl-pve-install.sh", "--version", "0.22.1", process_substitution=True)
    assert result.returncode == 0, result.stderr
    assert "installation verified" in result.stdout
    with tarfile.open(host.env["TRANSFERRED"], "r:gz") as transferred:
        assert set(REQUIRED[1:]) <= set(transferred.getnames())
    assert any(c[-2:] == ["--version", "0.22.1"] for c in host.calls("pct"))
    assert not list(host.directory.glob("hnefatafl-pve-bootstrap.*"))
    assert not list(host.directory.glob("hnefatafl-pve-install.*"))
    assert_no_destructive_commands(host)


def test_bootstrap_signal_cleans_temporary_checkout(host):
    host.archive(script=b'#!/bin/bash\nprintf ready > "$DELEGATE"\nread -r value\n')
    with subprocess.Popen(
        [
            str(host.bin / "bash"),
            str(host.checkout / "scripts/proxmox/ct/hnefatafl-pve-install.sh"),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=host.env,
        start_new_session=True,
    ) as process:
        try:
            deadline = time.monotonic() + 10
            while not Path(host.env["DELEGATE"]).exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert Path(host.env["DELEGATE"]).exists()
            os.killpg(process.pid, signal.SIGTERM)
            process.communicate(timeout=10)
            assert process.returncode == 143
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
    assert not list(host.directory.glob("hnefatafl-pve-bootstrap.*"))


@pytest.mark.skipif(os.geteuid() == 0, reason="requires an ordinary OS user")
@pytest.mark.parametrize(
    "script", ["hnefatafl.sh", "hnefatafl-pve-install.sh", "hnefatafl-pve-update.sh"]
)
def test_real_scripts_refuse_non_root(script):
    result = subprocess.run(["bash", str(HOST / script)], text=True, capture_output=True)
    assert result.returncode != 0
    assert "as root" in result.stderr


def test_bootstrap_rejects_privileged_modes_and_file_directory_conflicts(host):
    for name, mode in [("hnefatafl-main/privileged", 0o4755), ("hnefatafl-main/scripts", 0o644)]:
        entry = tarfile.TarInfo(name)
        entry.mode = mode
        host.archive(extra=(entry, b""))
        result = host.run("hnefatafl-pve-install.sh")
        assert result.returncode != 0
        assert "Validating bootstrap archive failed" in result.stderr
        assert not Path(host.env["DELEGATE"]).exists()


def test_bootstrap_rejects_oversized_member_before_reading_its_contents(host):
    entry = tarfile.TarInfo("hnefatafl-main/oversized")
    entry.size = 512 * 1024 * 1024 + 1
    Path(host.env["ARCHIVE"]).write_bytes(gzip.compress(entry.tobuf() + b"\0" * 1024))
    result = host.run("hnefatafl-pve-install.sh")
    assert result.returncode != 0
    assert "archive exceeds extraction limits" in result.stderr
    assert not list(host.directory.glob("hnefatafl-pve-bootstrap.*"))


def assert_no_destructive_commands(host):
    calls = host.calls("pct")
    assert not any(c[0] in {"destroy", "stop", "set", "resize"} for c in calls)
    assert all(c[0] != "resize" for c in host.calls("pvesm"))


def test_native_default_success_and_verified_summary(host):
    result = host.run("hnefatafl.sh")
    assert result.returncode == 0, result.stderr
    create = next(c for c in host.calls("pct") if c[0] == "create")
    for flag, value in {
        "--unprivileged": "1",
        "--cores": "4",
        "--memory": "4096",
        "--rootfs": "local-lvm:20",
        "--net0": "name=eth0,bridge=vmbr0,ip=dhcp",
        "--onboot": "1",
        "--features": "nesting=0",
    }.items():
        assert create[create.index(flag) + 1] == value
    pct = host.calls("pct")
    assert any(c[-3:] == ["systemctl", "is-enabled", "hnefatafl"] for c in pct)
    assert any(c[-3:] == ["systemctl", "is-active", "hnefatafl"] for c in pct)
    assert any(".probe()" in c[-1] for c in pct)
    for name in ("hnefatafl-update", "hnefatafl-backup"):
        assert any(c[-3:] == ["test", "-x", f"/usr/local/bin/{name}"] for c in pct)
    assert any(c[-2] == "-lc" and "command -v" in c[-1] for c in pct if len(c) >= 2)
    assert "installation verified" in result.stdout
    assert "Installed release: 0.22.1" in result.stdout
    assert "Application port: 8000" in result.stdout
    assert "192.0.2.185" in result.stdout
    assert "virtual disk allocations" in result.stdout
    assert not list(host.directory.glob("hnefatafl-pve-install.*"))
    assert_no_destructive_commands(host)


@pytest.mark.parametrize("bootstrap", [False, True])
@pytest.mark.parametrize("caller_umask", [0o000, 0o022, 0o077])
def test_provisioning_umask_allows_mapped_root_without_exposing_bootstrap(
    host, bootstrap, caller_umask
):
    script = "hnefatafl.sh"
    if bootstrap:
        host.archive(script=(host.checkout / REQUIRED[0]).read_bytes())
        script = "hnefatafl-pve-install.sh"
    result = host.run(
        script, CASE="umask", caller_umask=caller_umask, process_substitution=bootstrap
    )
    assert result.returncode == 0, result.stderr
    staging = Path(host.env["PROVISIONING"]) / "185"
    assert staging.stat().st_mode & 0o777 == 0o755
    records = [json.loads(line) for line in Path(host.env["MASKS"]).read_text().splitlines()]
    for name, args, mask in records:
        if name == "pct" and args[0] in {"create", "start"}:
            assert mask == 0o022
        else:
            assert mask == 0o077
    assert Path(host.env["TRANSFERRED"]).stat().st_mode & 0o777 == 0o600
    assert not list(host.directory.glob("hnefatafl-pve-install.*"))
    assert not list(host.directory.glob("hnefatafl-pve-bootstrap.*"))
    assert_no_destructive_commands(host)


def advanced(
    *,
    ctid="186",
    cpu="6",
    ram="8192",
    disk="30",
    storage="fast",
    template="local",
    bridge="vmbr1",
    network="dhcp",
    gateway="",
):
    values = ["advanced", ctid, cpu, ram, disk, storage, template, bridge, network]
    if network != "dhcp":
        values.append(gateway)
    return "\n".join(values) + "\n"


@pytest.mark.parametrize("network,gateway", [("dhcp", ""), ("192.0.2.186/24", "192.0.2.1")])
def test_native_advanced_resources_and_network(host, network, gateway):
    result = host.run("hnefatafl.sh", input=advanced(network=network, gateway=gateway))
    assert result.returncode == 0, result.stderr
    create = next(c for c in host.calls("pct") if c[0] == "create")
    assert create[1] == "186"
    assert "fast:30" in create
    assert create[create.index("--cores") + 1] == "6"
    net = f"name=eth0,bridge=vmbr1,ip={network}"
    if gateway:
        net += f",gw={gateway}"
    assert net in create
    assert_no_destructive_commands(host)


@pytest.mark.parametrize(
    "changes",
    [
        {"ctid": "99"},
        {"ctid": "0185"},
        {"ctid": "$(id)"},
        {"ctid": "9999999999"},
        {"cpu": "0"},
        {"ram": "1024"},
        {"disk": "11"},
        {"storage": "--evil"},
        {"template": "../../local"},
        {"bridge": "vmbr0,ip=dhcp"},
        {"network": "999.1.1.1/24", "gateway": "192.0.2.1"},
        {"network": "192.0.2.2/99", "gateway": "192.0.2.1"},
        {"network": "192.0.2.2", "gateway": "192.0.2.1"},
        {"network": "192.0.2.2/255.255.255.0", "gateway": "192.0.2.1"},
        {"network": "192.0.2.2/24", "gateway": "999.1.1.1"},
    ],
)
def test_native_rejects_input_before_mutation(host, changes):
    result = host.run("hnefatafl.sh", input=advanced(**changes))
    assert result.returncode != 0
    assert not any(c[0] == "create" for c in host.calls("pct"))
    assert not host.calls("pveam")
    assert_no_destructive_commands(host)


@pytest.mark.parametrize(
    "case,stage,code",
    [
        ("exists", "already exists", 1),
        ("storage", "Validating host configuration", 1),
        ("bridge", "Validating host configuration", 1),
        ("template", "No Debian 13", 1),
        ("template-download", "Downloading Debian template", 8),
        ("create", "Creating LXC", 17),
        ("permission", "host provisioning", 17),
        ("start", "Starting LXC", 18),
        ("push", "Transferring bootstrap", 19),
        ("install", "Installing Hnefatafl", 20),
        ("is-enabled", "Verifying application service", 21),
        ("is-active", "Verifying application service", 21),
        ("probe", "Verifying application service", 23),
        ("no-backup", "Verifying maintenance commands", 1),
        ("no-cli-path", "Verifying maintenance commands", 24),
    ],
)
def test_native_stage_failures_retain_state_without_false_success(host, case, stage, code):
    result = host.run("hnefatafl.sh", CASE=case)
    assert result.returncode == code, result.stderr
    assert stage in result.stderr
    assert "installation verified" not in result.stdout
    pct = host.calls("pct")
    if case in {"exists", "storage", "bridge", "template", "template-download"}:
        assert not pct
    if case in {"create", "permission"}:
        assert not any(c[0] in {"start", "push", "exec"} for c in pct)
    if case == "permission":
        assert "namei -l /var/lib/lxc/185/rootfs" in result.stderr
        assert "findmnt -T /var/lib/lxc" in result.stderr
    if case == "create":
        assert "namei" not in result.stderr
    if case in {"install", "is-enabled", "is-active", "probe", "no-backup", "no-cli-path"}:
        assert "journalctl -u hnefatafl" in result.stderr
        assert not any(c[-2:] == ["-rf", "/root/hnefatafl-bootstrap"] for c in pct)
    assert not list(host.directory.glob("hnefatafl-pve-install.*"))
    assert_no_destructive_commands(host)


def test_native_reuses_cached_template(host):
    result = host.run("hnefatafl.sh", CASE="cached")
    assert result.returncode == 0, result.stderr
    assert not any(c[0] == "download" for c in host.calls("pveam"))


def test_native_warns_about_high_actual_storage_usage(host):
    result = host.run("hnefatafl.sh", CASE="full-storage")
    assert result.returncode == 0, result.stderr
    assert "WARNING: actual storage usage is at least 90%" in result.stderr
    assert_no_destructive_commands(host)


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--check"],
        ["--list"],
        ["--version", "0.22.1"],
        ["--backup"],
        ["--rollback"],
        ["--recover"],
        ["two words", "$(touch should-not-exist)", "a'b\"c;echo hi"],
    ],
)
def test_updater_forwards_arguments_without_evaluation(host, args):
    result = host.run("hnefatafl-pve-update.sh", "185", *args)
    assert result.returncode == 0, result.stderr
    assert host.calls("pct")[-1] == ["exec", "185", "--", "/usr/local/bin/hnefatafl-update", *args]
    assert not (host.directory / "should-not-exist").exists()
    assert_no_destructive_commands(host)


def test_updater_environment_target_and_explicit_override(host):
    result = host.run("hnefatafl-pve-update.sh", "--check", HNEFATAFL_CTID="186")
    assert result.returncode == 0
    assert host.calls("pct")[-1][1] == "186"
    result = host.run("hnefatafl-pve-update.sh", "187", HNEFATAFL_CTID="186")
    assert result.returncode == 0
    assert host.calls("pct")[-1][1] == "187"


@pytest.mark.parametrize("ctid", ["", "99", "0185", "-185", "185;id", "$(id)", "9999999999"])
def test_updater_invalid_target(host, ctid):
    result = host.run("hnefatafl-pve-update.sh", *([ctid] if ctid else []))
    assert result.returncode != 0
    assert "valid CT ID" in result.stderr
    assert not host.calls("pct")


@pytest.mark.parametrize(
    "case,message",
    [
        ("missing", "does not exist"),
        ("stopped", "stopped"),
        ("status-error", "Cannot read"),
        ("no-config", "configuration is missing"),
        ("no-module", "module is missing"),
        ("no-updater", "updater is missing"),
        ("host", "Cannot verify"),
    ],
)
def test_updater_preflight_failure(host, case, message):
    result = host.run("hnefatafl-pve-update.sh", "185", CASE=case)
    assert result.returncode != 0
    assert message in result.stderr
    assert not any(
        "/usr/local/bin/hnefatafl-update" in c and "test" not in c for c in host.calls("pct")
    )
    assert_no_destructive_commands(host)


def test_updater_propagates_failure(host):
    result = host.run("hnefatafl-pve-update.sh", "185", "--recover", UPDATE_STATUS="42")
    assert result.returncode == 42
    assert "exit 42" in result.stderr
    assert "transaction.json" in result.stderr


@pytest.mark.parametrize("installed", [False, True])
@pytest.mark.parametrize("failure", ["", "update", "is-enabled", "is-active"])
def test_debian_resume_enables_service_and_preserves_configuration(host, installed, failure):
    """Resume uses the existing updater, including after interruption before enable."""
    container = host.directory / "container"
    config = container / "etc/hnefatafl"
    config.mkdir(parents=True)
    retained = config / "deployment.json"
    retained.write_text('{"repository":"retained/repository","port":8123}')
    if installed:
        (config / "installed.json").write_text('{"version":"0.22.1"}')
    for name in ("usr/local/bin", "usr/local/lib/hnefatafl", "root"):
        (container / name).mkdir(parents=True)
    os_release = container / "os-release"
    os_release.write_text("ID=debian\nVERSION_ID=13\n")
    script = host.checkout / "scripts/linux/install.sh"
    source = script.read_text().replace("source /etc/os-release", f'source "{os_release}"')
    source = source.replace("/etc/hnefatafl", str(config)).replace(
        "/usr/local/", str(container / "usr/local") + "/"
    )
    source = source.replace("/etc/profile.d", str(container / "etc/profile.d"))
    source = source.replace("/root/.bashrc", str(container / "root/.bashrc"))
    # The isolated fixture cannot run a real root login shell against its rootfs.
    source = source.replace("  verify_cli_commands\n", "  : # probe covered separately\n")
    source = source.replace("\nverify_cli_commands\n", "\n: # probe covered separately\n")
    # Preserve the fixture PATH rather than the production path normalization.
    source = (
        "\n".join(line for line in source.splitlines() if not line.startswith("export PATH="))
        + "\n"
    )
    script.write_text(source)
    updater = host.checkout / "scripts/linux/update.sh"
    updater.write_text(
        '#!/bin/bash\nprintf "%s\\n" "$@" > "$DELEGATE"\nexit "${UPDATE_STATUS:-0}"\n'
    )
    (host.bin / "install").symlink_to(shutil.which("install"))
    (host.bin / "grep").symlink_to(shutil.which("grep"))
    dpkg = host.bin / "dpkg"
    dpkg.write_text("#!/bin/bash\necho amd64\n")
    dpkg.chmod(0o755)
    systemctl = host.bin / "systemctl"
    systemctl.write_text(
        f'#!{sys.executable}\nimport json,os,sys\nwith open(os.environ["CALLS"], "a") as log: log.write(json.dumps(["systemctl",sys.argv[1:],os.getcwd()])+"\\n")\nsys.exit(21 if os.environ.get("CASE") == sys.argv[1] else 0)\n'
    )
    systemctl.chmod(0o755)
    result = subprocess.run(
        [str(host.bin / "bash"), str(script)],
        env={**host.env, "CASE": failure, "UPDATE_STATUS": "41" if failure == "update" else "0"},
        text=True,
        capture_output=True,
    )
    assert retained.read_text() == '{"repository":"retained/repository","port":8123}'
    assert Path(host.env["DELEGATE"]).read_text().splitlines() == (
        [""] if installed else ["--initialize"]
    )
    calls = host.calls("systemctl")
    if failure == "update":
        assert result.returncode == 41
        assert not calls
    elif failure:
        assert result.returncode == 21
        assert "Enabling and verifying systemd service failed" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert calls == [
            ["enable", "hnefatafl.service"],
            ["is-enabled", "hnefatafl"],
            ["is-active", "hnefatafl"],
        ]


def test_debian_installer_configures_idempotent_root_shell_path(tmp_path):
    """The installer must keep root configuration and avoid duplicate PATH hooks."""
    import re

    source = (ROOT / "scripts/linux/install.sh").read_text()
    function = re.search(r"(?ms)^configure_root_cli_path\(\) \{\n.*?^\}\n", source)
    assert function
    profile = tmp_path / "etc/profile.d/hnefatafl-path.sh"
    bashrc = tmp_path / "root/.bashrc"
    bashrc.parent.mkdir()
    bashrc.write_text("# existing root preferences\n")
    local_bin = tmp_path / "usr/local/bin"
    local_bin.mkdir(parents=True)
    command = (
        function.group()
        .replace("/etc/profile.d", str(profile.parent))
        .replace("/root/.bashrc", str(bashrc))
        .replace("/usr/local/bin", str(local_bin))
    )
    result = subprocess.run(
        ["bash", "-c", command + "\nconfigure_root_cli_path\nconfigure_root_cli_path\n"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert bashrc.read_text().count(f". {profile}") == 1
    assert "# existing root preferences" in bashrc.read_text()
    assert profile.stat().st_mode & 0o777 == 0o644

    for initial in ("/usr/bin:/bin", f"{local_bin}:/usr/bin:/bin"):
        result = subprocess.run(
            ["bash", "-c", f'. "{profile}"; printf "%s" "$PATH"'],
            env={"PATH": initial}, text=True, capture_output=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.split(":").count(str(local_bin)) == 1
