"""Check the real script's credential-bearing curl invocation boundaries."""

import json
import unittest

import test_sync


class CurlSafetyTests(unittest.TestCase):
    def setUp(self):
        self.action = test_sync.SyncTests()
        self.action.setUp()
        self.addCleanup(self.action.doCleanups)
        stub = test_sync.CURL_STUB.replace(
            'args = sys.argv[1:]',
            '''args = sys.argv[1:]
with (root / "options").open("a") as log:
    log.write(json.dumps({
        "first": args[0],
        "connect": args[args.index("--connect-timeout") + 1] if "--connect-timeout" in args else None,
        "total": args[args.index("--max-time") + 1] if "--max-time" in args else None,
    }) + "\\n")''',
        )
        (self.action.root / "curl").write_text(stub)

    def options(self):
        return [json.loads(line) for line in
                (self.action.root / "options").read_text().splitlines()]

    def test_both_requests_have_bounded_timeouts_by_default(self):
        result = self.action.run_sync("{}")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.options()), 2)
        for options in self.options():
            self.assertIsNotNone(options["connect"])
            self.assertIsNotNone(options["total"])
            self.assertGreater(float(options["connect"]), 0)
            self.assertGreaterEqual(float(options["total"]), float(options["connect"]))

    def test_timeout_settings_apply_to_both_requests(self):
        result = self.action.run_sync("{}", extra={
            "INFERRA_CONNECT_TIMEOUT": "2", "INFERRA_MAX_TIME": "7",
        })
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([(o["connect"], o["total"]) for o in self.options()],
                         [("2", "7"), ("2", "7")])

    def test_invalid_timeout_settings_fail_before_credentials(self):
        for name in ("INFERRA_CONNECT_TIMEOUT", "INFERRA_MAX_TIME"):
            for value in ("0", "-1", "no-timeout", "1.5"):
                with self.subTest(name=name, value=value):
                    result = self.action.run_sync("{}", extra={name: value})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("positive integer", result.stdout)
                    self.assertFalse((self.action.root / "options").exists())

    def test_both_requests_disable_curlrc_as_first_argument(self):
        secret = "sk_" + "test_" + 'quoted"and\\backslash'
        result = self.action.run_sync(json.dumps({"KEY": secret}))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([o["first"] for o in self.options()], ["-q", "-q"])
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertNotIn(json.dumps(secret)[1:-1], result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
