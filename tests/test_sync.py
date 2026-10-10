"""Exercise the real shell action with a local curl stub, without credentials."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "sync.sh"
CURL_STUB = '''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

root = Path(os.environ["SYNC_TEST_DIR"])
args = sys.argv[1:]
with (root / "requests").open("a") as log:
    log.write("PUT\\n" if "PUT" in args else "OIDC\\n")
if "PUT" in args:
    (root / "payload.json").write_text(sys.stdin.read())
    Path(args[args.index("-o") + 1]).write_text(
        '{"space":"prod","variables":1,"secrets":2,"restarted":0}')
    print("200", end="")
else:
    print('{"value":"dummy-oidc-token"}')
'''


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        curl = self.root / "curl"
        curl.write_text(CURL_STUB)
        curl.chmod(0o755)

    def run_sync(self, secrets):
        env = dict(os.environ)
        env.update(
            PATH=str(self.root) + os.pathsep + env["PATH"],
            SYNC_TEST_DIR=str(self.root),
            INFERRA_SPACE="prod",
            INFERRA_VARS='{"LOG_LEVEL":"info"}',
            INFERRA_SECRETS=secrets,
            INFERRA_API="https://api.example.invalid",
            ACTIONS_ID_TOKEN_REQUEST_URL="https://oidc.example.invalid?job=1",
            ACTIONS_ID_TOKEN_REQUEST_TOKEN="dummy-request-token",
        )
        return subprocess.run(
            ["bash", str(SCRIPT)], env=env, capture_output=True, text=True
        )

    def assert_rejected(self, secrets, message):
        result = self.run_sync(secrets)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), message)
        self.assertEqual(result.stderr, "")
        self.assertFalse((self.root / "requests").exists())

    def test_empty_selected_secrets_are_skipped_with_names_only(self):
        result = self.run_sync('{"MISSING_B":"","VALID":"must-not-appear","MISSING_A":""}')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            '::warning::Not set in this GitHub Environment, skipped: ["MISSING_B","MISSING_A"]',
            result.stdout,
        )
        self.assertNotIn("must-not-appear", result.stdout + result.stderr)
        payload = json.loads((self.root / "payload.json").read_text())
        self.assertEqual(payload["secrets"], {"VALID": "must-not-appear"})

    def test_non_string_selected_secrets_report_only_names(self):
        self.assert_rejected(
            '{"NUMBER_KEY":42,"OBJECT_KEY":{"hidden":"value"}}',
            '::error::Selected secrets must have string values: '
            '["NUMBER_KEY","OBJECT_KEY"]',
        )

    def test_unset_selected_secrets_arrive_as_null_and_are_skipped(self):
        # toJSON(secrets.X) is null when X isn't set in this Environment.
        result = self.run_sync('{"UNSET":null,"VALID":"must-not-appear"}')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('::warning::Not set in this GitHub Environment, skipped: ["UNSET"]', result.stdout)
        self.assertNotIn("must-not-appear", result.stdout + result.stderr)
        payload = json.loads((self.root / "payload.json").read_text())
        self.assertEqual(payload["secrets"], {"VALID": "must-not-appear"})

    def test_invalid_json_is_not_printed(self):
        result = self.run_sync('{"KEY":"do-not-print"')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("secrets must be a JSON object of selected names", result.stdout)
        self.assertNotIn("do-not-print", result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertFalse((self.root / "requests").exists())

    def test_selected_values_round_trip_and_github_tokens_are_filtered(self):
        selected = {"KEY": 'quotes" and \\backslash\nnewline', "OTHER": "value"}
        result = self.run_sync(json.dumps(dict(
            selected, github_token="never-send-lower", GITHUB_TOKEN="never-send-upper"
        )))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads((self.root / "payload.json").read_text())
        self.assertEqual(payload, {
            "space": "prod", "variables": {"LOG_LEVEL": "info"}, "secrets": selected
        })
        self.assertEqual((self.root / "requests").read_text(), "OIDC\nPUT\n")
        for value in [*selected.values(), "never-send-lower", "never-send-upper"]:
            self.assertNotIn(value, result.stdout + result.stderr)

    def test_empty_mapping_is_sent_for_replacement(self):
        result = self.run_sync("{}")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            json.loads((self.root / "payload.json").read_text())["secrets"], {}
        )


if __name__ == "__main__":
    unittest.main()
