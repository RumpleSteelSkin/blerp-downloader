"""Unit tests for the update logic. Run: python -m unittest discover tests"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blerp_downloader.updater import (  # noqa: E402
    UpdateState, check_for_update, is_newer, parse_version, pick_installer_asset,
)


class TestParseVersion(unittest.TestCase):
    def test_plain_and_v_prefixed_are_equal(self):
        self.assertEqual(parse_version("1.2.0"), parse_version("v1.2.0"))
        self.assertEqual(parse_version("1.2.0"), parse_version("V1.2.0"))

    def test_short_forms_pad_to_the_same_value(self):
        self.assertEqual(parse_version("1.2"), parse_version("1.2.0"))
        self.assertEqual(parse_version("1.2.0"), parse_version("1.2.0.0"))

    def test_prerelease_sorts_below_final(self):
        self.assertLess(parse_version("1.2.0-rc.1"), parse_version("1.2.0"))
        self.assertLess(parse_version("1.2.0-alpha"), parse_version("1.2.0"))

    def test_four_components(self):
        self.assertEqual(parse_version("1.0.0.3")[:4], (1, 0, 0, 3))

    def test_unparseable_returns_none(self):
        for bad in ("", "latest", "release-2024", "abc", None):
            self.assertIsNone(parse_version(bad), bad)

    def test_absurdly_long_input_is_rejected_not_evaluated(self):
        self.assertIsNone(parse_version("9" * 200))


class TestIsNewer(unittest.TestCase):
    def test_ordering(self):
        self.assertTrue(is_newer("1.1.0", "1.0.0"))
        self.assertTrue(is_newer("v2.0.0", "1.0.0"))
        self.assertTrue(is_newer("1.0.1", "1.0.0"))
        self.assertFalse(is_newer("1.0.0", "1.0.0"))
        self.assertFalse(is_newer("0.9.0", "1.0.0"))

    def test_unparseable_side_is_never_newer(self):
        self.assertFalse(is_newer("latest", "1.0.0"))
        self.assertFalse(is_newer("1.0.0", "garbage"))


def _asset(name, state="uploaded", size=123):
    return {"name": name, "state": state, "size": size,
            "browser_download_url": f"https://example.invalid/{name}"}


class TestPickInstallerAsset(unittest.TestCase):
    def test_exact_name_wins_over_pattern_match(self):
        assets = [_asset("BlerpDownloader-Setup-9.9.9.exe"),
                  _asset("BlerpDownloader-Setup-1.2.0.exe")]
        self.assertEqual(pick_installer_asset(assets, "1.2.0")["name"],
                         "BlerpDownloader-Setup-1.2.0.exe")

    def test_pattern_match_when_no_exact(self):
        assets = [_asset("BlerpDownloader-Setup-1.2.0-rc1.exe")]
        self.assertIsNotNone(pick_installer_asset(assets, "1.2.0"))

    def test_still_uploading_assets_are_ignored(self):
        assets = [_asset("BlerpDownloader-Setup-1.2.0.exe", state="starting")]
        self.assertIsNone(pick_installer_asset(assets, "1.2.0"))

    def test_bare_exe_is_never_selected(self):
        # Would otherwise be launched with installer switches - must not match.
        assets = [_asset("BlerpDownloader.exe"), _asset("blerp.exe")]
        self.assertIsNone(pick_installer_asset(assets, "1.2.0"))

    def test_empty(self):
        self.assertIsNone(pick_installer_asset([], "1.2.0"))


class TestCheckForUpdateAgainstFixtures(unittest.TestCase):
    """check_for_update's classification, driven through the api_url override."""

    def _serve(self, payload):
        """Serves one JSON payload over loopback and returns its URL + a stopper."""
        import http.server
        import json
        import threading

        body = json.dumps(payload).encode()

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()

        def stop():
            srv.shutdown()
            srv.server_close()

        return f"http://127.0.0.1:{srv.server_port}/latest.json", stop

    def _check(self, payload, current):
        url, stop = self._serve(payload)
        try:
            return check_for_update(current, api_url=url)
        finally:
            stop()

    def test_available(self):
        st = self._check({"tag_name": "v1.2.0", "html_url": "https://example.invalid",
                          "body": "notes", "assets": [_asset("BlerpDownloader-Setup-1.2.0.exe")]},
                         "1.0.0")
        self.assertEqual(st.state, UpdateState.AVAILABLE)
        self.assertEqual(st.info.version, "1.2.0")

    def test_up_to_date(self):
        st = self._check({"tag_name": "v1.0.0", "assets": []}, "1.0.0")
        self.assertEqual(st.state, UpdateState.UP_TO_DATE)

    def test_ahead_refuses_to_downgrade(self):
        st = self._check({"tag_name": "v1.0.0", "assets": []}, "9.9.9")
        self.assertEqual(st.state, UpdateState.AHEAD)

    def test_malformed_tag_is_surfaced_not_swallowed(self):
        st = self._check({"tag_name": "latest", "assets": []}, "1.0.0")
        self.assertEqual(st.state, UpdateState.UNCOMPARABLE)

    def test_newer_release_without_installer_asset_is_an_error(self):
        st = self._check({"tag_name": "v2.0.0", "assets": [_asset("BlerpDownloader.exe")]},
                         "1.0.0")
        self.assertEqual(st.state, UpdateState.ERROR)


if __name__ == "__main__":
    unittest.main()
