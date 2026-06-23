"""
grok_utils 단위 테스트
python -m pytest collect/test_grok_utils.py -v
"""
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
import collect.grok_utils as gu


def _proc(stdout="", returncode=0, stderr=""):
    m = MagicMock()
    m.stdout = stdout
    m.returncode = returncode
    m.stderr = stderr
    return m


class TestCallHermes(unittest.TestCase):
    @patch("collect.grok_utils.subprocess.run")
    def test_returns_stdout_on_success(self, mock_run):
        mock_run.return_value = _proc('{"ok": true}')
        result = gu.call_hermes("test prompt")
        self.assertEqual(result, '{"ok": true}')

    @patch("collect.grok_utils.subprocess.run")
    def test_returns_none_on_nonzero_exit(self, mock_run):
        mock_run.return_value = _proc("", returncode=1, stderr="auth error")
        result = gu.call_hermes("test prompt")
        self.assertIsNone(result)

    @patch("collect.grok_utils.subprocess.run")
    def test_retries_on_timeout_and_returns_none_when_exhausted(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="hermes", timeout=120)
        with patch.dict("os.environ", {"HERMES_RETRY": "2"}):
            import importlib
            importlib.reload(gu)
            result = gu.call_hermes("prompt")
        self.assertIsNone(result)
        self.assertEqual(mock_run.call_count, 3)  # 1 original + 2 retries

    @patch("collect.grok_utils.subprocess.run")
    def test_returns_none_on_file_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        result = gu.call_hermes("prompt")
        self.assertIsNone(result)

    @patch("collect.grok_utils.subprocess.run")
    def test_returns_empty_string_as_is(self, mock_run):
        """call_hermes itself does not retry empty — that's call_hermes_json's job."""
        mock_run.return_value = _proc("")
        result = gu.call_hermes("prompt")
        self.assertEqual(result, "")

    @patch("collect.grok_utils.subprocess.run")
    def test_custom_timeout_used(self, mock_run):
        mock_run.return_value = _proc('{}')
        gu.call_hermes("prompt", timeout=300)
        _, kwargs = mock_run.call_args
        self.assertEqual(kwargs["timeout"], 300)


class TestExtractJson(unittest.TestCase):
    def test_extracts_json_object(self):
        result = gu.extract_json('Some text {"key": "value"} more text')
        self.assertEqual(result, {"key": "value"})

    def test_returns_none_on_no_block(self):
        result = gu.extract_json("no json here")
        self.assertIsNone(result)

    def test_returns_none_on_decode_error(self):
        result = gu.extract_json("{bad json: }")
        self.assertIsNone(result)

    def test_returns_none_on_empty_string(self):
        result = gu.extract_json("")
        self.assertIsNone(result)

    def test_nested_json(self):
        result = gu.extract_json('{"a": {"b": 1}}')
        self.assertEqual(result, {"a": {"b": 1}})


class TestExtractJsonArray(unittest.TestCase):
    def test_extracts_json_array(self):
        result = gu.extract_json_array('[{"a": 1}, {"b": 2}]')
        self.assertEqual(result, [{"a": 1}, {"b": 2}])

    def test_returns_none_on_no_block(self):
        result = gu.extract_json_array("no array here")
        self.assertIsNone(result)

    def test_returns_none_on_decode_error(self):
        result = gu.extract_json_array("[bad]")
        self.assertIsNone(result)

    def test_returns_none_when_not_a_list(self):
        result = gu.extract_json_array('{"key": "value"}')
        self.assertIsNone(result)

    def test_empty_array_valid(self):
        result = gu.extract_json_array("[]")
        self.assertEqual(result, [])


class TestCallHermesJson(unittest.TestCase):
    @patch("collect.grok_utils.time.sleep")
    @patch("collect.grok_utils.subprocess.run")
    def test_success_on_first_attempt(self, mock_run, mock_sleep):
        mock_run.return_value = _proc('{"sentiment": "optimistic"}')
        raw, parsed = gu.call_hermes_json("prompt", json_retry=2, delay=0)
        self.assertEqual(parsed, {"sentiment": "optimistic"})
        mock_sleep.assert_not_called()
        self.assertEqual(mock_run.call_count, 1)

    @patch("collect.grok_utils.time.sleep")
    @patch("collect.grok_utils.subprocess.run")
    def test_retries_on_empty_response(self, mock_run, mock_sleep):
        mock_run.side_effect = [_proc(""), _proc('{"sentiment": "neutral"}')]
        raw, parsed = gu.call_hermes_json("prompt", json_retry=2, delay=0.0)
        self.assertEqual(parsed, {"sentiment": "neutral"})
        self.assertEqual(mock_run.call_count, 2)

    @patch("collect.grok_utils.time.sleep")
    @patch("collect.grok_utils.subprocess.run")
    def test_retries_on_json_parse_failure(self, mock_run, mock_sleep):
        mock_run.side_effect = [
            _proc("Grok says: here is your answer: { bad json"),
            _proc('{"sentiment": "fearful"}'),
        ]
        raw, parsed = gu.call_hermes_json("prompt", json_retry=2, delay=0.0)
        self.assertEqual(parsed, {"sentiment": "fearful"})
        self.assertEqual(mock_run.call_count, 2)

    @patch("collect.grok_utils.time.sleep")
    @patch("collect.grok_utils.subprocess.run")
    def test_returns_none_after_all_retries_exhausted(self, mock_run, mock_sleep):
        mock_run.return_value = _proc("")
        raw, parsed = gu.call_hermes_json("prompt", json_retry=2, delay=0.0)
        self.assertIsNone(parsed)
        self.assertEqual(mock_run.call_count, 3)  # 1 + 2 retries

    @patch("collect.grok_utils.time.sleep")
    @patch("collect.grok_utils.subprocess.run")
    def test_returns_none_none_when_hermes_fails(self, mock_run, mock_sleep):
        mock_run.return_value = _proc("", returncode=1)
        raw, parsed = gu.call_hermes_json("prompt", json_retry=2, delay=0.0)
        self.assertIsNone(raw)
        self.assertIsNone(parsed)
        self.assertEqual(mock_run.call_count, 1)  # no JSON retry for hermes failure

    @patch("collect.grok_utils.time.sleep")
    @patch("collect.grok_utils.subprocess.run")
    def test_validator_callback_triggers_retry(self, mock_run, mock_sleep):
        # First response: valid JSON but fails validator; second: passes
        mock_run.side_effect = [
            _proc('{"sentiment": "INVALID_ENUM"}'),
            _proc('{"sentiment": "optimistic"}'),
        ]
        validator = lambda d: d.get("sentiment") in {"optimistic", "fearful", "neutral"}
        raw, parsed = gu.call_hermes_json("prompt", json_retry=2, delay=0.0, validator=validator)
        self.assertEqual(parsed, {"sentiment": "optimistic"})
        self.assertEqual(mock_run.call_count, 2)

    @patch("collect.grok_utils.time.sleep")
    @patch("collect.grok_utils.subprocess.run")
    def test_validator_callback_returns_none_after_exhaustion(self, mock_run, mock_sleep):
        mock_run.return_value = _proc('{"sentiment": "WRONG"}')
        validator = lambda d: d.get("sentiment") in {"optimistic", "fearful", "neutral"}
        raw, parsed = gu.call_hermes_json("prompt", json_retry=2, delay=0.0, validator=validator)
        self.assertIsNone(parsed)
        self.assertEqual(mock_run.call_count, 3)

    @patch("collect.grok_utils.time.sleep")
    @patch("collect.grok_utils.subprocess.run")
    def test_sleep_called_between_retries(self, mock_run, mock_sleep):
        mock_run.side_effect = [_proc(""), _proc(""), _proc('{"ok": true}')]
        gu.call_hermes_json("prompt", json_retry=2, delay=1.5)
        mock_sleep.assert_called_with(1.5)
        self.assertEqual(mock_sleep.call_count, 2)


class TestCallHermesJsonArray(unittest.TestCase):
    @patch("collect.grok_utils.time.sleep")
    @patch("collect.grok_utils.subprocess.run")
    def test_success_on_first_attempt(self, mock_run, mock_sleep):
        mock_run.return_value = _proc('[{"symbol": "TSLA"}]')
        raw, parsed = gu.call_hermes_json_array("prompt", json_retry=2, delay=0.0)
        self.assertEqual(parsed, [{"symbol": "TSLA"}])

    @patch("collect.grok_utils.time.sleep")
    @patch("collect.grok_utils.subprocess.run")
    def test_retries_on_json_parse_failure(self, mock_run, mock_sleep):
        mock_run.side_effect = [
            _proc("[bad json"),
            _proc('[{"symbol": "NVDA"}]'),
        ]
        raw, parsed = gu.call_hermes_json_array("prompt", json_retry=2, delay=0.0)
        self.assertEqual(parsed, [{"symbol": "NVDA"}])
        self.assertEqual(mock_run.call_count, 2)

    @patch("collect.grok_utils.time.sleep")
    @patch("collect.grok_utils.subprocess.run")
    def test_returns_none_after_all_retries(self, mock_run, mock_sleep):
        mock_run.return_value = _proc("")
        raw, parsed = gu.call_hermes_json_array("prompt", json_retry=1, delay=0.0)
        self.assertIsNone(parsed)
        self.assertEqual(mock_run.call_count, 2)

    @patch("collect.grok_utils.time.sleep")
    @patch("collect.grok_utils.subprocess.run")
    def test_validator_callback_triggers_retry(self, mock_run, mock_sleep):
        mock_run.side_effect = [
            _proc('[{"symbol": "X"}]'),   # fails validator (unknown symbol)
            _proc('[{"symbol": "TSLA"}]'), # passes
        ]
        validator = lambda lst: all(d.get("symbol") in {"TSLA", "NVDA"} for d in lst)
        raw, parsed = gu.call_hermes_json_array("prompt", json_retry=2, delay=0.0, validator=validator)
        self.assertEqual(parsed, [{"symbol": "TSLA"}])


if __name__ == "__main__":
    unittest.main()
