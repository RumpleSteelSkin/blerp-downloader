"""Security properties of the update path.

The updater is the one place the app downloads a binary and runs it, so each of
these is a regression guard for a way that could go wrong.
"""

from __future__ import annotations

import functools
import hashlib
import http.server
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blerp_downloader import updater as up  # noqa: E402
from blerp_downloader.errors import UpdateError  # noqa: E402


def _serve(directory: Path):
    """Serves a directory over loopback; returns (base_url, stop)."""
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):   # keep test output readable
            pass

    handler = functools.partial(Quiet, directory=str(directory))
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def stop():
        srv.shutdown()
        srv.server_close()

    return f"http://127.0.0.1:{srv.server_port}", stop


class _ServedRelease(unittest.TestCase):
    """A release directory with an installer and a SHA256SUMS.txt."""

    PAYLOAD = b"pretend installer" * 1000

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.served = self.root / "served"
        self.served.mkdir()
        self.downloads = self.root / "downloads"

        self.name = "BlerpDownloader-Setup-9.9.9.exe"
        (self.served / self.name).write_bytes(self.PAYLOAD)
        self.digest = hashlib.sha256(self.PAYLOAD).hexdigest()
        self._write_sums(self.digest)

        self.base, self._stop = _serve(self.served)
        # Keep downloads inside the temp dir instead of the real LOCALAPPDATA.
        self._patch = mock.patch.object(up, "updates_dir", lambda: self.downloads)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._stop()
        self._tmp.cleanup()

    def _write_sums(self, digest: str, filename: str | None = None):
        (self.served / "SHA256SUMS.txt").write_text(
            f"{digest}  {filename or self.name}\n", encoding="ascii")

    def _info(self, **over):
        defaults = dict(
            version="9.9.9", tag="v9.9.9", asset_name=self.name,
            asset_url=f"{self.base}/{self.name}", asset_size=len(self.PAYLOAD),
            html_url="", notes="",
            checksum_url=f"{self.base}/SHA256SUMS.txt",
        )
        defaults.update(over)
        return up.UpdateInfo(**defaults)


class TestChecksumVerification(_ServedRelease):
    def test_matching_checksum_downloads(self):
        path = up.download_installer(self._info())
        self.assertEqual(path.read_bytes(), self.PAYLOAD)
        self.assertEqual(path.name, "BlerpDownloader-Setup-9.9.9.exe")

    def test_wrong_checksum_is_refused_and_nothing_is_left_behind(self):
        self._write_sums("0" * 64)
        info = self._info()
        with self.assertRaises(UpdateError) as cm:
            up.download_installer(info)
        self.assertIn("checksum", str(cm.exception).lower())
        self.assertEqual(list(self.downloads.glob("*")), [])

    def test_missing_checksum_asset_refuses_rather_than_skipping(self):
        """No SHA256SUMS.txt must mean 'cannot verify', not 'verified'."""
        info = self._info(checksum_url="")
        with self.assertRaises(UpdateError) as cm:
            up.download_installer(info)
        self.assertIn("verified", str(cm.exception).lower())
        self.assertFalse(any(self.downloads.glob("*")) if self.downloads.exists() else False)

    def test_checksum_file_without_our_entry_is_refused(self):
        self._write_sums(self.digest, filename="SomethingElse.exe")
        info = self._info()
        with self.assertRaises(UpdateError):
            up.download_installer(info)

    def test_size_zero_does_not_disable_verification(self):
        """asset_size is 0 when the API omits it; the old falsy guard then
        skipped the only integrity check the updater had."""
        self._write_sums("0" * 64)
        info = self._info(asset_size=0)
        with self.assertRaises(UpdateError) as cm:
            up.download_installer(info)
        self.assertIn("checksum", str(cm.exception).lower())


class TestNoPathTraversal(_ServedRelease):
    def test_asset_name_cannot_escape_the_downloads_directory(self):
        """A hostile asset name is ignored entirely: both the saved filename and
        the checksum lookup come from the version we parsed."""
        evil = r"BlerpDownloader-Setup-\..\..\..\Startup\payload.exe"
        path = up.download_installer(self._info(asset_name=evil))
        self.assertEqual(path.parent.resolve(), self.downloads.resolve())
        self.assertEqual(path.name, "BlerpDownloader-Setup-9.9.9.exe")
        startup = self.root.parent / "Startup"
        self.assertFalse(startup.exists())

    def test_installer_pattern_rejects_separators(self):
        for name in (r"BlerpDownloader-Setup-\..\..\x.exe",
                     "BlerpDownloader-Setup-../x.exe",
                     "BlerpDownloader-Setup-a/b.exe"):
            self.assertIsNone(up.INSTALLER_RE.match(name), name)
        self.assertIsNotNone(up.INSTALLER_RE.match("BlerpDownloader-Setup-1.0.5.exe"))

    def test_traversing_asset_is_never_selected(self):
        assets = [{"name": r"BlerpDownloader-Setup-\..\..\evil.exe",
                   "state": "uploaded", "size": 1,
                   "browser_download_url": "http://x/y"}]
        self.assertIsNone(up.pick_installer_asset(assets, "9.9.9"))


class TestUpdateSourceOverride(unittest.TestCase):
    def test_env_override_ignored_in_a_packaged_build(self):
        """The variable is a test hook. In a shipped build the environment is
        writable by anything running as the user, and redirecting the feed is
        enough to get an arbitrary installer offered through a genuine prompt."""
        seen = {}

        def fake_fetch(url, current_version, timeout):
            seen["url"] = url
            return None, up.UpdateStatus(up.UpdateState.ERROR, current_version)

        with mock.patch.dict("os.environ", {"BLERP_UPDATE_API_URL": "http://evil.invalid/x"}), \
             mock.patch.object(up, "_fetch_latest", fake_fetch), \
             mock.patch.object(up, "is_frozen", return_value=True):
            up.check_for_update("1.0.0")
        self.assertEqual(seen["url"], up.API_LATEST_URL)

    def test_env_override_still_works_from_source(self):
        seen = {}

        def fake_fetch(url, current_version, timeout):
            seen["url"] = url
            return None, up.UpdateStatus(up.UpdateState.ERROR, current_version)

        with mock.patch.dict("os.environ", {"BLERP_UPDATE_API_URL": "http://fixture.invalid/x"}), \
             mock.patch.object(up, "_fetch_latest", fake_fetch), \
             mock.patch.object(up, "is_frozen", return_value=False):
            up.check_for_update("1.0.0")
        self.assertEqual(seen["url"], "http://fixture.invalid/x")

    def test_explicit_api_url_always_wins(self):
        seen = {}

        def fake_fetch(url, current_version, timeout):
            seen["url"] = url
            return None, up.UpdateStatus(up.UpdateState.ERROR, current_version)

        with mock.patch.dict("os.environ", {"BLERP_UPDATE_API_URL": "http://evil.invalid/x"}), \
             mock.patch.object(up, "_fetch_latest", fake_fetch), \
             mock.patch.object(up, "is_frozen", return_value=True):
            up.check_for_update("1.0.0", api_url="http://explicit.invalid/y")
        self.assertEqual(seen["url"], "http://explicit.invalid/y")


if __name__ == "__main__":
    unittest.main()
