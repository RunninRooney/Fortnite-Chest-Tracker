"""Update signing and installing. Set CHEST_NET_TEST=1 to also send a test update through the real relay."""
import base64
import json
import os
import secrets
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _isolate  # noqa: E402,F401  (first: never touch the real settings)
import launcher as L  # noqa: E402
_isolate.check(L)


class SignatureTests(unittest.TestCase):
    def test_rfc8032_test_vector_1(self):
        secret = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
        self.assertEqual(L.public_key(secret).hex(), "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
        sig = L.sign(secret, b"")
        self.assertEqual(sig.hex(), "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
        self.assertTrue(L.verify(L.public_key(secret), b"", sig))

    def test_matches_cryptography_library(self):
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        except ImportError:
            self.skipTest("cryptography not installed")
        for _ in range(3):
            secret, msg = os.urandom(32), os.urandom(200)
            k = Ed25519PrivateKey.from_private_bytes(secret)
            self.assertEqual(L.sign(secret, msg), k.sign(msg))
            self.assertTrue(L.verify(L.public_key(secret), msg, k.sign(msg)))

    def test_rejects_changes(self):
        secret, msg = os.urandom(32), b"hello"
        sig = L.sign(secret, msg)
        self.assertFalse(L.verify(L.public_key(secret), b"hellp", sig))
        self.assertFalse(L.verify(L.public_key(os.urandom(32)), msg, sig))
        self.assertFalse(L.verify(L.public_key(secret), msg, sig[:-1] + bytes([sig[-1] ^ 1])))


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.secret = os.urandom(32)
        self.pub = L.public_key(self.secret).hex()
        self.files = {"chest_tracker.py": b"print('v2')\n", "reader_src/Program.cs": b"// cs\n",
                      "Start Chest Tracker.bat": b"@echo new\n", "version.json": b'{"build": 5}'}

    def test_roundtrip_and_install(self):
        b = L.open_bundle(L.make_bundle(self.secret, 5, "2.0", self.files), self.pub)
        self.assertEqual((b["build"], b["files"]), (5, self.files))
        d = Path(tempfile.mkdtemp())
        (d / "Start Chest Tracker.bat").write_bytes(b"@echo old\n")
        changed = L.install_bundle(b, d)
        self.assertEqual(sorted(changed), sorted(self.files))
        self.assertEqual((d / "reader_src" / "Program.cs").read_bytes(), b"// cs\n")
        # the running .bat is not overwritten; the new one waits next to it
        self.assertEqual((d / "Start Chest Tracker.bat").read_bytes(), b"@echo old\n")
        self.assertEqual((d / "Start Chest Tracker.bat.new").read_bytes(), b"@echo new\n")
        self.assertEqual(L.local_build(d), 5)
        self.assertEqual(L.install_bundle(b, d), ["Start Chest Tracker.bat"])  # only the pending .bat differs

    def test_wrong_key_or_tampering_rejected(self):
        msg = L.make_bundle(self.secret, 5, "2.0", self.files)
        self.assertIsNone(L.open_bundle(msg, L.public_key(os.urandom(32)).hex()))
        m = json.loads(msg)
        data = json.loads(zlib.decompress(base64.b64decode(m["payload"])))
        data["files"]["chest_tracker.py"] = base64.b64encode(b"import evil").decode()
        m["payload"] = base64.b64encode(zlib.compress(json.dumps(data).encode())).decode()
        self.assertIsNone(L.open_bundle(json.dumps(m).encode(), self.pub))
        self.assertIsNone(L.open_bundle(b"not json", self.pub))

    def test_paths_outside_the_folder_rejected(self):
        for bad in ["../evil.py", "/abs.py", "C:/x.py", "a/../../b.py", "..\\x.py"]:
            msg = L.make_bundle(self.secret, 5, "2.0", {bad: b"x"})
            self.assertIsNone(L.open_bundle(msg, self.pub), bad)

    def test_redact(self):
        os.environ["USERNAME"] = "SomeKid"
        os.environ["USERPROFILE"] = r"C:\Users\SomeKid"
        self.assertEqual(L.redact(r"C:\Users\SomeKid\Docs and somekid's log"), r"%USERPROFILE%\Docs and <user>'s log")


class ReaderSetupTests(unittest.TestCase):
    """A PC with no .NET at all (the brother's PC): the old launcher crashed asking dotnet.exe for its
    version before it ever got to installing it."""

    def setUp(self):
        self.app, self.data, self.pf = (Path(tempfile.mkdtemp()) for _ in range(3))
        (self.app / "reader_src").mkdir()
        (self.app / "reader_src" / "Program.cs").write_text("// v1")
        self.saved = (L.APP_DIR, L.DATA_DIR, L.run_setup_reader, os.environ.get("ProgramFiles"),
                      os.environ.get("LOCALAPPDATA"))
        L.APP_DIR, L.DATA_DIR = self.app, self.data
        self.local = Path(tempfile.mkdtemp())
        os.environ["ProgramFiles"] = str(self.pf)  # no dotnet\dotnet.exe in here
        os.environ["LOCALAPPDATA"] = str(self.local)  # ...or in the user's own folder
        self.calls = []

        def fake_setup():  # stands in for setup_reader.bat (install .NET + build)
            self.calls.append("setup")
            (self.app / "reader").mkdir(exist_ok=True)
            (self.app / "reader" / "ChestReplayReader.exe").write_bytes(b"MZ")
            return 0

        L.run_setup_reader = fake_setup

    def tearDown(self):
        L.APP_DIR, L.DATA_DIR, L.run_setup_reader, pf, local = self.saved
        os.environ["ProgramFiles"] = pf or ""
        os.environ["LOCALAPPDATA"] = local or ""

    def test_no_dotnet_installs_and_builds(self):
        L.ensure_reader()  # must not raise
        self.assertEqual(len(self.calls), 1)
        self.assertTrue((self.app / "reader" / "ChestReplayReader.exe").exists())
        L.ensure_reader()  # built and stamped: nothing more to do
        self.assertEqual(len(self.calls), 1)

    def test_source_change_rebuilds_when_dotnet_is_there(self):
        L.ensure_reader()
        (self.app / "reader_src" / "Program.cs").write_text("// v2")
        L.ensure_reader()  # no .NET on this PC: keep the reader it has
        self.assertEqual(len(self.calls), 1)
        (self.pf / "dotnet").mkdir()
        (self.pf / "dotnet" / "dotnet.exe").write_bytes(b"MZ")
        saved_run = L.run
        L.run = lambda cmd, **kw: L.subprocess.CompletedProcess(cmd, 0, "10.0.404 [C:\\Program Files\\dotnet\\sdk]\n", "")
        try:
            L.ensure_reader()  # .NET installed: rebuild with the new source
        finally:
            L.run = saved_run
        self.assertEqual(len(self.calls), 2)
        L.ensure_reader()
        self.assertEqual(len(self.calls), 2)

    def test_dotnet_in_the_users_folder_counts(self):
        L.ensure_reader()
        (self.app / "reader_src" / "Program.cs").write_text("// v2")
        d = self.local / "Microsoft" / "dotnet"
        d.mkdir(parents=True)
        (d / "dotnet.exe").write_bytes(b"MZ")  # where setup_reader.bat installs it without admin rights
        saved_run = L.run
        L.run = lambda cmd, **kw: L.subprocess.CompletedProcess(cmd, 0, "10.0.404 [x]\n", "")
        try:
            L.ensure_reader()  # found: rebuilt with the new source
        finally:
            L.run = saved_run
        self.assertEqual(len(self.calls), 2)

    def test_declined_install_not_retried_every_start(self):
        L.run_setup_reader = lambda: self.calls.append("setup") or 1  # .NET install declined
        L.ensure_reader()
        L.ensure_reader()
        self.assertEqual(len(self.calls), 1)

    def test_report_has_replay_summary(self):
        s = L.replay_summary()
        self.assertIn("demos_folder", s)


@unittest.skipUnless(os.name == "nt", "Windows batch file")
class SetupReaderBatTests(unittest.TestCase):
    """The real setup_reader.bat on a PC with no .NET. Version 1.12's quit silently right after its first
    line (a missing dotnet.exe on the left of a pipe ends a batch file), so .NET was never installed."""

    def run_bat(self, edits):
        import subprocess
        d = Path(tempfile.mkdtemp())
        s = (Path(L.__file__).parent / "setup_reader.bat").read_bytes().decode()
        self.assertIn("\r\n", s)  # Windows line endings: cmd misreads labels in LF-only files
        for a, b in edits:
            self.assertIn(a, s)
            s = s.replace(a, b)
        (d / "setup_reader.bat").write_bytes(s.encode())
        env = dict(os.environ, CT_DOTNET_HOME=str(d / "userdotnet"), TEMP=str(d), TMP=str(d))
        r = subprocess.run(["cmd", "/c", str(d / "setup_reader.bat")], cwd=d, env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=60)
        return r.stdout

    def test_no_dotnet_goes_on_to_install_it(self):
        out = self.run_bat([("%ProgramFiles%\\dotnet", "C:\\no-such-dir\\dotnet"),
                            ("https://dot.net/v1/dotnet-install.ps1", "http://127.0.0.1:9/none.ps1"),
                            ("where winget", "where no-such-winget-here")])
        self.assertIn("Downloading Microsoft .NET 10 SDK", out)
        self.assertIn("Could not install .NET", out)  # (the download is faked to fail)


@unittest.skipUnless(os.environ.get("CHEST_NET_TEST"), "set CHEST_NET_TEST=1 to test the real relay")
class RelayUpdateTests(unittest.TestCase):
    def test_update_through_relay(self):
        secret = os.urandom(32)  # a throwaway test key and channel
        pub = L.public_key(secret).hex()
        files = {"chest_tracker.py": os.urandom(60000).hex().encode(), "version.json": b'{"build": 2}'}
        topic = f"chesttracker/u1/{L.channel(pub)}/latest"
        self.assertTrue(L.publish_to_relay(topic, L.make_bundle(secret, 2, "test", files)))
        d = Path(tempfile.mkdtemp())
        (d / "version.json").write_text('{"build": 1}')
        self.assertTrue(L.update(pub, d))
        self.assertEqual((d / "chest_tracker.py").read_bytes(), files["chest_tracker.py"])
        self.assertFalse(L.update(pub, d))  # already up to date
        L.publish_to_relay(topic, b"", retain=True)  # clean up the retained test message


if __name__ == "__main__":
    unittest.main()
