"""Public vs personal builds: where updates come from, problem reports, and what a release carries."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bundle_files  # noqa: E402
import edition  # noqa: E402
import launcher  # noqa: E402
import teamcrypt  # noqa: E402


class Editions(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def test_public_by_default(self):
        ed = edition.load(self.d)
        self.assertEqual(ed["name"], "public")
        self.assertEqual(ed["updates"], "github")
        self.assertFalse(ed["reports"])

    def test_personal_folder_overrides(self):
        (self.d / "personal").mkdir()
        (self.d / "personal" / "edition.json").write_text(json.dumps(
            {"name": "squad", "updates": "relay", "reports": True, "report_key": "ab" * 32, "junk": 1}))
        ed = edition.load(self.d)
        self.assertEqual((ed["name"], ed["updates"], ed["reports"]), ("squad", "relay", True))
        self.assertNotIn("junk", ed)

    def test_reports(self):
        key = {"report_key": "ab" * 32, "reports": False}
        self.assertFalse(launcher.reports_on({}, key))                          # public default: off
        self.assertTrue(launcher.reports_on({"send_reports": True}, key))       # turned on
        self.assertFalse(launcher.reports_on({"send_reports": True, "offline": True}, key))
        self.assertFalse(launcher.reports_on({"send_reports": True}, {"report_key": ""}))  # nowhere to seal them
        self.assertTrue(launcher.reports_on({}, dict(key, reports=True)))      # a squad build's default
        self.assertFalse(launcher.reports_on({"send_reports": False}, dict(key, reports=True)))

    def test_offline_never_checks_for_updates(self):
        with mock.patch.object(launcher, "read_config", return_value={"offline": True}), \
                mock.patch.object(launcher, "fetch_from_github") as gh, \
                mock.patch.object(launcher, "fetch_from_relay") as relay:
            self.assertFalse(launcher.update(app_dir=self.d))
        gh.assert_not_called()
        relay.assert_not_called()

    def test_public_build_updates_from_github_only(self):
        with mock.patch.object(launcher, "read_config", return_value={}), \
                mock.patch.object(edition, "load", return_value=dict(edition.DEFAULTS, github_repo="someone/chest-tracker")), \
                mock.patch.object(launcher, "fetch_from_github", return_value=None) as gh, \
                mock.patch.object(launcher, "fetch_from_relay") as relay:
            launcher.update(app_dir=self.d)
        gh.assert_called_once_with("someone/chest-tracker")
        relay.assert_not_called()

    def test_github_repo_name_checked(self):
        self.assertIsNone(launcher.fetch_from_github("not a repo/../x"))
        self.assertIsNone(launcher.fetch_from_github(""))


class ReleaseFiles(unittest.TestCase):
    def test_public_release_has_no_personal_files_or_tests(self):
        files = bundle_files.public_files()
        self.assertFalse([n for n in files if n.startswith(("personal/", "tests/"))])
        for n in ("chest_tracker.py", "launcher.py", "edition.py", "teamcrypt.py", "setup_wizard.py",
                  "share_card.py", "assets/icon.png", "LICENSE"):
            self.assertIn(n, files)
        self.assertNotIn("assets/logo_source.png", files)

    def test_sealed_report_round_trip(self):
        import os
        secret = os.urandom(32)
        blob = teamcrypt.seal_to(teamcrypt.x25519_public(secret), b'{"stage": "started"}')
        self.assertNotIn(b"started", blob)
        self.assertEqual(teamcrypt.open_sealed(secret, blob), b'{"stage": "started"}')


if __name__ == "__main__":
    unittest.main()
