"""Tests for collect/grok_health.py — real Grok/hermes output samples, no network, no desktop alerts."""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
import collect.grok_health as gh

# Captured from a real `hermes chat -q` run on 2026-10-03 when credits were exhausted
REAL_403 = (
    "     Error: Error code: 403 - {'code':\n"
    "     'personal-team-blocked:spending-limit', 'error': 'You have run out of\n"
    "     credits or need a Grok subscription. Add credits at\n"
    "     https://grok.com/?_s=usage or upgrade at https://grok.com/supergrok.'}\n"
)


class TestClassify(unittest.TestCase):
    def test_credits(self):
        self.assertEqual(gh.classify(REAL_403), "credits_exhausted")

    def test_auth(self):
        self.assertEqual(gh.classify("Error: Error code: 401 - Unauthorized"), "auth")
        self.assertEqual(gh.classify("OAuth token expired, please re-authenticate"), "auth")

    def test_rate_limit(self):
        self.assertEqual(gh.classify("Error code: 429 - Too many requests"), "rate_limited")

    def test_network_and_unknown(self):
        self.assertEqual(gh.classify("Connection error: name or service not known"), "network")
        self.assertEqual(gh.classify("something odd"), "unknown")

    def test_credits_wins_over_auth_words(self):
        self.assertEqual(gh.classify("401 ... need a Grok subscription"), "credits_exhausted")


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.patches = [
            patch.object(gh, "STATUS_PATH", d / "grok_status.json"),
            patch.object(gh, "ALERT_LOG", d / "grok_alerts.log"),
            patch.dict(os.environ, {"GROK_HEALTH": "1"}),
        ]
        for p in self.patches:
            p.start()
        self.alerts = []
        self.alert_patch = patch.object(gh, "alert", lambda t, m: self.alerts.append((t, m)))
        self.alert_patch.start()

    def tearDown(self):
        self.alert_patch.stop()
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def fake_hermes(self, body: str) -> str:
        path = Path(self.tmp.name) / "fake_hermes"
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return str(path)


class TestDiagnose(_Base):
    def test_rejected_provider(self):
        h = self.fake_hermes(f"cat <<'EOF'\n{REAL_403}EOF\nexit 0\n")
        ok, cause, detail = gh.diagnose(h)
        self.assertFalse(ok)
        self.assertEqual(cause, "credits_exhausted")
        self.assertIn("403", detail)

    def test_healthy(self):
        h = self.fake_hermes("echo 'OK'\nexit 0\n")
        self.assertEqual(gh.diagnose(h), (True, "", ""))

    def test_missing_binary(self):
        ok, cause, _ = gh.diagnose("/nonexistent/hermes")
        self.assertEqual((ok, cause), (False, "hermes_missing"))


class TestTransitions(_Base):
    def down(self):
        return self.fake_hermes(f"cat <<'EOF'\n{REAL_403}EOF\nexit 0\n")

    def test_first_failure_alerts_once(self):
        gh.report_failure(self.down(), "NVDA")
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("크레딧", self.alerts[0][1])
        self.assertEqual(gh.load_status()["state"], "down")
        # a flood of failing collectors within the diagnosis window must not re-alert
        for _ in range(20):
            gh.report_failure(self.down(), "x")
        self.assertEqual(len(self.alerts), 1)

    def test_reminder_after_interval_and_cause_change(self):
        gh.report_failure(self.down(), "a")
        st = gh.load_status()
        st["last_check"] -= gh.DIAG_MIN_INTERVAL + 1
        st["last_alert"] -= gh.REALERT_INTERVAL + 1
        gh._save_status(st)
        gh.report_failure(self.down(), "b")
        self.assertEqual(len(self.alerts), 2)
        # cause change alerts immediately
        st = gh.load_status(); st["last_check"] -= gh.DIAG_MIN_INTERVAL + 1; gh._save_status(st)
        gh.report_failure(self.fake_hermes("echo 'Error: Error code: 401 - Unauthorized'\n"), "c")
        self.assertEqual(len(self.alerts), 3)
        self.assertEqual(gh.load_status()["cause"], "auth")

    def test_transient_empty_response_does_not_alert(self):
        gh.report_failure(self.fake_hermes("echo OK\n"), "a")
        self.assertEqual(self.alerts, [])
        self.assertNotEqual(gh.load_status().get("state"), "down")

    def test_recovery_alert_via_success(self):
        gh.report_failure(self.down(), "a")
        gh.report_success()
        self.assertEqual(len(self.alerts), 2)
        self.assertIn("복구", self.alerts[1][0])
        self.assertEqual(gh.load_status()["state"], "ok")
        gh.report_success()  # no-op when already ok
        self.assertEqual(len(self.alerts), 2)

    def test_success_is_noop_without_status_file(self):
        gh.report_success()
        self.assertFalse(gh.STATUS_PATH.exists())

    def test_disabled(self):
        with patch.dict(os.environ, {"GROK_HEALTH": "0"}):
            gh.report_failure(self.down(), "a")
        self.assertEqual(self.alerts, [])
        self.assertFalse(gh.STATUS_PATH.exists())


class TestAlertChannels(unittest.TestCase):
    def test_log_and_external_command(self):
        real = gh.subprocess.run  # capture before patching

        def fake(cmd, *a, **k):
            if isinstance(cmd, list) and cmd and cmd[0] == "osascript":  # no desktop popups in tests
                class R:
                    returncode = 0; stdout = ""; stderr = ""
                return R()
            return real(cmd, *a, **k)

        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "got.txt"
            with patch.object(gh, "ALERT_LOG", Path(t) / "a.log"), \
                 patch.object(gh.subprocess, "run", side_effect=fake), \
                 patch.dict(os.environ, {"GROK_ALERT_CMD": f"cat > {out}"}):
                gh.alert("T", "msg")
            self.assertIn("T — msg", (Path(t) / "a.log").read_text())
            self.assertEqual(out.read_text(), "T\nmsg\n")


if __name__ == "__main__":
    unittest.main()
