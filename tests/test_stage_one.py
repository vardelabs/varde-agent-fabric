import fcntl
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "install" / "stage-one.sh"
TOKEN = "ops_test-token-value"
DEPLOYER_FILES = ("box_deployer.py", "dispatcher_deployer.py", "gateway_deployer.py")
UNITS = ("varde-box-deploy.service", "varde-box-deploy.timer", "varde-box-deployer-rollback.service")

# Each stub logs its argv, so a test can prove the token never reached one.
STUBS = {
    "id": 'echo 0',
    "apt-get": 'echo "apt-get $*" >>"$STUB_DIR/calls.log"',
    "curl": 'echo "curl $*" >>"$STUB_DIR/calls.log"; out=; while [ $# -gt 0 ]; do [ "$1" = -o ] && out=$2; shift; done; '
            'if [ -n "$out" ]; then echo key >"$out"; else echo key; fi',
    # Reads its input as gpg --dearmor does, so curl never writes into a closed pipe.
    "gpg": 'cat >/dev/null; echo "gpg $*" >>"$STUB_DIR/calls.log"; while [ $# -gt 0 ]; do [ "$1" = -o ] && echo key >"$2"; shift; done',
    "dpkg": 'echo amd64',
    "systemctl": 'echo "systemctl $*" >>"$STUB_DIR/calls.log"',
    "tailscale": textwrap.dedent('''\
        echo "tailscale $*" >>"$STUB_DIR/calls.log"
        case "$1" in
          status) if [ -e "$STUB_DIR/joined" ]; then echo '{"BackendState": "Running"}'; else echo '{"BackendState": "NeedsLogin"}'; fi ;;
          up) cat "${2#--auth-key=file:}" >"$STUB_DIR/authkey-used"; touch "$STUB_DIR/joined" ;;
        esac'''),
    "systemd-creds": textwrap.dedent('''\
        echo "systemd-creds $*" >>"$STUB_DIR/calls.log"
        case "$1" in
          encrypt) { printf 'sealed:'; cat; } >"$4" ;;
          decrypt) sed 's/^sealed://' "$3" ;;
        esac'''),
    "op": textwrap.dedent('''\
        echo "op $*" >>"$STUB_DIR/calls.log"
        [ "$OP_SERVICE_ACCOUNT_TOKEN" = "$STUB_TOKEN" ] || exit 1
        case "$1" in
          whoami) exit 0 ;;
          vault) cat "$STUB_DIR/vaults.json" ;;
          read) field=${2##*/}; stat -L -c %a "/proc/$$/fd/1" >"$STUB_DIR/mode-while-writing-$field"; cat "$STUB_DIR/fields/$field"; [ "$field" != "${STUB_FAIL_FIELD:-}" ] ;;
        esac'''),
}


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"})


def output(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def documented_digest(folder):
    digest = hashlib.sha256()
    for name in DEPLOYER_FILES:
        data = (folder / name).read_bytes()
        digest.update(f"{name}\0{len(data)}\0".encode())
        digest.update(data)
    return digest.hexdigest()


class StageOne(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root, self.stubs = self.tmp / "root", self.tmp / "bin"
        (self.root / "etc").mkdir(parents=True)
        (self.root / "etc/os-release").write_text("ID=ubuntu\nVERSION_CODENAME=noble\n")
        for sub in ("etc/systemd/system",):
            (self.root / sub).mkdir(parents=True)
        self.stubs.mkdir()
        for name, body in STUBS.items():
            path = self.stubs / name
            path.write_text("#!/usr/bin/env bash\n" + body + "\n")
            path.chmod(0o755)
        self.repo = self.make_deployment_repo()
        self.anchor = output("rev-parse", "HEAD", cwd=self.repo)
        fields = self.tmp / "fields"
        fields.mkdir()
        for field, value in {"box": "example-box", "tailscale-authkey": "tskey-test", "git-credentials": "https://x:y@example.invalid",
                             "merge-approvers": '{"approvers": []}', "deployment-repo": str(self.repo)}.items():
            (fields / field).write_text(value)
        self.set_vaults(["vault-1"])

    def make_deployment_repo(self):
        repo = self.tmp / "deployment"
        (repo / "deploy").mkdir(parents=True)
        git("init", "-q", "-b", "main", cwd=repo)
        for name in (*DEPLOYER_FILES, "box_deployer_rollback.py", *UNITS):
            (repo / "deploy" / name).write_text(f"# {name}\n")
        git("add", ".", cwd=repo)
        git("commit", "-q", "-m", "deployer", cwd=repo)
        return repo

    def set_vaults(self, ids):
        (self.tmp / "vaults.json").write_text(json.dumps([{"id": vault} for vault in ids]))

    def run_stage_one(self, *args, token=TOKEN, timeout=None, umask=0o022, **extra_env):
        env = {"PATH": f"{self.stubs}:{os.environ['PATH']}", "STAGE_ONE_ROOT": str(self.root), "STUB_DIR": str(self.tmp),
               "STUB_TOKEN": TOKEN, "HOME": str(self.tmp), "GIT_CONFIG_GLOBAL": "/dev/null", "TMPDIR": str(self.tmp), **extra_env}
        # Root's usual umask, so a file written before its chmod shows as readable.
        return subprocess.run(["bash", str(SCRIPT), *args], input=token + "\n", env=env, capture_output=True, text=True,
                              umask=umask, timeout=timeout)

    def calls(self):
        log = self.tmp / "calls.log"
        return log.read_text() if log.exists() else ""

    def test_a_first_run_installs_the_box_deployer_at_the_anchor(self):
        done = self.run_stage_one("--anchor", self.anchor)
        self.assertEqual(done.returncode, 0, done.stderr)
        box = json.loads((self.root / "etc/varde-box/box.json").read_text())
        self.assertEqual(box, {"schema": "varde-box.v1", "box": "example-box", "anchor_commit": self.anchor})
        current = (self.root / "usr/local/libexec/varde-box-deployer/current").resolve()
        self.assertEqual(current.name, documented_digest(current))
        self.assertTrue((self.root / "usr/local/libexec/varde-box-deployer/box_deployer_rollback.py").is_file())
        for unit in UNITS:
            self.assertTrue((self.root / "etc/systemd/system" / unit).is_file(), unit)
        self.assertIn("systemctl enable --now varde-box-deploy.timer", self.calls())
        self.assertEqual((self.tmp / "authkey-used").read_text(), "tskey-test")

    def test_secrets_land_owner_only_and_the_token_stays_out_of_every_argv(self):
        self.assertEqual(self.run_stage_one("--anchor", self.anchor).returncode, 0)
        for path in ("etc/varde-agent-deploy/git-credentials", "etc/varde-agent-deploy/merge-approvers.json",
                     "etc/credstore.encrypted/varde-box.op-token"):
            self.assertEqual(stat.S_IMODE((self.root / path).stat().st_mode), 0o600, path)
        for field in ("git-credentials", "merge-approvers"):
            self.assertEqual((self.tmp / f"mode-while-writing-{field}").read_text().strip(), "600", field)
        self.assertEqual((self.root / "etc/credstore.encrypted/varde-box.op-token").read_text(), "sealed:" + TOKEN)
        self.assertNotIn(TOKEN, self.calls())

    def test_a_refused_token_seals_nothing_and_installs_nothing(self):
        done = self.run_stage_one("--anchor", self.anchor, token="wrong")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("1Password refused that token", done.stderr)
        self.assertFalse((self.root / "etc/credstore.encrypted/varde-box.op-token").exists())
        self.assertFalse((self.root / "etc/varde-box/box.json").exists())

    def test_an_anchor_merged_in_from_a_side_branch_is_refused(self):
        git("checkout", "-q", "-b", "side", cwd=self.repo)
        (self.repo / "deploy/box_deployer.py").write_text("# side\n")
        git("commit", "-qam", "side", cwd=self.repo)
        side = output("rev-parse", "HEAD", cwd=self.repo)
        git("checkout", "-q", "main", cwd=self.repo)
        git("merge", "-q", "--no-ff", "-m", "merge side", "side", cwd=self.repo)
        done = self.run_stage_one("--anchor", side)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("first-parent history", done.stderr)
        self.assertFalse((self.root / "etc/varde-box/box.json").exists())
        self.assertFalse((self.root / "etc/varde-agent-deploy").exists())

    def test_a_run_waits_while_the_deployer_holds_its_lock(self):
        state = self.root / "var/lib/varde-box-deploy"
        state.mkdir(parents=True)
        with open(state / "deploy.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.assertRaises(subprocess.TimeoutExpired):
                self.run_stage_one("--anchor", self.anchor, timeout=3)
            self.assertFalse((self.root / "etc/varde-box/box.json").exists())
            self.assertFalse((self.root / "usr/local/libexec/varde-box-deployer/current").exists())
            self.assertFalse((self.root / "etc/varde-agent-deploy").exists())
            self.assertFalse((self.root / "etc/credstore.encrypted/varde-box.op-token").exists())
        self.assertEqual(self.run_stage_one("--anchor", self.anchor).returncode, 0)

    def test_the_apt_keyrings_stay_readable_to_apt_under_a_strict_umask(self):
        self.assertEqual(self.run_stage_one("--anchor", self.anchor, umask=0o077).returncode, 0)
        for name in ("1password-archive-keyring.gpg", "tailscale-archive-keyring.gpg"):
            self.assertEqual(stat.S_IMODE((self.root / "usr/share/keyrings" / name).stat().st_mode), 0o644, name)

    def test_a_failed_join_key_read_leaves_no_key_on_disk(self):
        done = self.run_stage_one("--anchor", self.anchor, STUB_FAIL_FIELD="tailscale-authkey")
        self.assertNotEqual(done.returncode, 0)
        left = [path for path in self.tmp.glob("tmp.*") if path.is_file() and "tskey-test" in path.read_text()]
        self.assertEqual(left, [])

    def test_a_rerun_keeps_the_sealed_token_and_asks_for_nothing(self):
        self.assertEqual(self.run_stage_one("--anchor", self.anchor).returncode, 0)
        again = self.run_stage_one("--anchor", self.anchor, token="")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("using the sealed 1Password token", again.stdout)
        self.assertIn("already on the tailnet", again.stdout)

    def test_a_secret_file_left_readable_by_a_killed_run_is_rewritten_owner_only(self):
        leftover = self.root / "etc/varde-agent-deploy/git-credentials.new"
        leftover.parent.mkdir(parents=True)
        leftover.write_text("partial")
        leftover.chmod(0o644)
        self.assertEqual(self.run_stage_one("--anchor", self.anchor).returncode, 0)
        self.assertEqual((self.tmp / "mode-while-writing-git-credentials").read_text().strip(), "600")

    def test_a_token_that_sees_two_vaults_is_refused(self):
        self.set_vaults(["vault-1", "vault-2"])
        done = self.run_stage_one("--anchor", self.anchor)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("exactly one vault", done.stderr)
        self.assertFalse((self.root / "etc/varde-box/box.json").exists())

    def test_a_short_anchor_is_refused_before_anything_runs(self):
        done = self.run_stage_one("--anchor", self.anchor[:12])
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("full 40-character", done.stderr)
        self.assertEqual(self.calls(), "")


if __name__ == "__main__":
    unittest.main()
