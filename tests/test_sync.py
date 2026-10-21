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
    (root / "url").write_text(args[args.index("PUT") + 1])
    (root / "payload.json").write_text(sys.stdin.read())
    Path(args[args.index("-o") + 1]).write_text(os.environ.get(
        "SYNC_TEST_BODY", '{"space":"prod","variables":1,"secrets":2,"restarted":0}'))
    print(os.environ.get("SYNC_TEST_CODE", "200"), end="")
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

    def run_sync(self, secrets, api="https://api.example.invalid", extra=None, shell=("bash",)):
        env = dict(os.environ)
        env.update(
            PATH=str(self.root) + os.pathsep + env["PATH"],
            SYNC_TEST_DIR=str(self.root),
            INFERRA_SPACE="prod",
            INFERRA_VARS='{"LOG_LEVEL":"info"}',
            INFERRA_SECRETS=secrets,
            INFERRA_API=api,
            ACTIONS_ID_TOKEN_REQUEST_URL="https://oidc.example.invalid?job=1",
            ACTIONS_ID_TOKEN_REQUEST_TOKEN="dummy-request-token",
        )
        env.update(extra or {})
        if api is None:
            env.pop("INFERRA_API")
        return subprocess.run(
            [*shell, str(SCRIPT)], env=env, capture_output=True, text=True
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

    def test_reserved_variable_names_are_not_sent(self):
        result = self.run_sync("{}", extra={"INFERRA_VARS": json.dumps({
            "LOG_LEVEL": "info", "INFERRA_API_URL": "https://api.dev.example.invalid",
            "PORT": "9000", "KUBERNETES_X": "y", "RUNNER_LABELS": '"self-hosted"',
        })})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads((self.root / "payload.json").read_text())
        self.assertEqual(payload["variables"], {"LOG_LEVEL": "info", "RUNNER_LABELS": '"self-hosted"'})
        self.assertIn("INFERRA_API_URL", result.stdout)
        self.assertNotIn("api.dev.example.invalid", result.stdout + result.stderr)

    def test_empty_mapping_is_sent_for_replacement(self):
        result = self.run_sync("{}")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            json.loads((self.root / "payload.json").read_text())["secrets"], {}
        )

    def test_empty_api_url_means_production(self):
        result = self.run_sync("{}", api="")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            (self.root / "url").read_text(), "https://api.inferralab.com/v1/github/env"
        )

    def test_explicit_api_url_is_used(self):
        self.run_sync("{}", api="https://api.dev.example.invalid/")
        self.assertEqual(
            (self.root / "url").read_text(), "https://api.dev.example.invalid/v1/github/env"
        )

    def test_unset_api_url_means_production_too(self):
        result = self.run_sync("{}", api=None)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            (self.root / "url").read_text(), "https://api.inferralab.com/v1/github/env"
        )

    def test_xtrace_never_prints_secrets(self):
        sentinel = "SENTINEL-secret-value-123"
        for shell in (("bash", "-x"), ("bash", "-o", "xtrace")):
            result = self.run_sync(
                json.dumps({"KEY": sentinel}),
                extra={"SHELLOPTS": "xtrace"} if shell == ("bash", "-x") else None,
                shell=shell,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn(sentinel, result.stdout + result.stderr)

    def test_conflicts_print_names_only(self):
        body = '{"space":"prod","variables":1,"secrets":1,"restarted":0,"conflicts":["OWNED","B_2"]}'
        result = self.run_sync("{}", extra={"SYNC_TEST_BODY": body})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("already set in Inferra: OWNED, B_2.", result.stdout)

    def test_conflicts_with_values_or_objects_print_nothing_beyond_names(self):
        body = json.dumps({"space": "prod", "variables": 1, "secrets": 1, "restarted": 0,
                           "conflicts": ["OK_NAME", "has space", "lower", "x=SENTINEL-value",
                                         {"name": "SENTINEL-obj"}, 5, None, "A\n::error::boom"]})
        result = self.run_sync("{}", extra={"SYNC_TEST_BODY": body})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("already set in Inferra: OK_NAME.", result.stdout)
        self.assertNotIn("SENTINEL", result.stdout + result.stderr)
        self.assertNotIn("boom", result.stdout + result.stderr)

    def test_conflict_names_are_capped(self):
        names = ["N%d" % i for i in range(50)]
        body = json.dumps({"space": "prod", "variables": 1, "secrets": 1, "restarted": 0, "conflicts": names})
        result = self.run_sync("{}", extra={"SYNC_TEST_BODY": body})
        self.assertIn("N19", result.stdout)
        self.assertNotIn("N20", result.stdout)

    def test_error_response_prints_fixed_text_only(self):
        for code, fragment in (("400", "invalid"), ("401", "authentication"), ("403", "authentication"),
                               ("409", "conflicts"), ("413", "too large"), ("503", "server error"), ("418", "unexpected")):
            body = json.dumps({"title": "SENTINEL-title", "detail": "SENTINEL-secret-123",
                               "secrets": {"K": "SENTINEL-value"}})
            result = self.run_sync("{}", extra={"SYNC_TEST_BODY": body, "SYNC_TEST_CODE": code})
            self.assertNotEqual(result.returncode, 0)
            text = result.stdout + result.stderr
            self.assertIn("(HTTP %s): " % code, text)
            self.assertIn(fragment, text)
            self.assertNotIn("SENTINEL", text)
            self.assertEqual(text.count("::error::"), 1)

    def test_non_json_error_body_is_not_echoed(self):
        result = self.run_sync("{}", extra={"SYNC_TEST_BODY": "SENTINEL-html-page", "SYNC_TEST_CODE": "502"})
        self.assertNotIn("SENTINEL", result.stdout + result.stderr)
        self.assertIn("(HTTP 502)", result.stdout)


if __name__ == "__main__":
    unittest.main()
