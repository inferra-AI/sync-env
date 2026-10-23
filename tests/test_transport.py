"""Transport regression tests for the real sync script; no network credentials."""

import json
import unittest

import test_sync


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.action = test_sync.SyncTests()
        self.action.setUp()
        self.addCleanup(self.action.doCleanups)
        # Record transport options only; never record credential-bearing args.
        stub = test_sync.CURL_STUB.replace(
            'args = sys.argv[1:]',
            '''args = sys.argv[1:]
with (root / "protocols").open("a") as log:
    log.write(json.dumps([
        args[i + 1] for i, arg in enumerate(args) if arg == "--proto"
    ]) + "\\n")''',
        )
        (self.action.root / "curl").write_text(stub)

    def test_non_https_api_is_rejected_before_requesting_credentials(self):
        secret = "sk_" + "test_" + "transport-sentinel"
        for api in (
            "http://api.example.invalid",
            "http://" + secret + "@api.example.invalid",
            "ftp://api.example.invalid",
            "api.example.invalid",
            "//api.example.invalid",
        ):
            with self.subTest(api=api):
                result = self.action.run_sync(json.dumps({"KEY": secret}), api=api)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("api-url must use HTTPS", result.stdout)
                self.assertNotIn(secret, result.stdout + result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertFalse((self.action.root / "requests").exists())

    def test_curl_allows_only_https_for_credentials_and_sync(self):
        result = self.action.run_sync("{}")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            (self.action.root / "requests").read_text(), "OIDC\nPUT\n"
        )
        protocols = [json.loads(line) for line in
                     (self.action.root / "protocols").read_text().splitlines()]
        self.assertEqual(protocols, [["=https"], ["=https"]])


if __name__ == "__main__":
    unittest.main()
