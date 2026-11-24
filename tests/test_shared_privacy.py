import hashlib
import unittest

from cli.shared.errors import SafeError
from cli.shared.privacy import fingerprint, normalize_path, normalize_path_list, normalize_proxy
from cli.shared.scope import Scope


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.scope = Scope(hashlib.sha256(b"windoctor-public-fixture/1").digest())

    def test_frozen_scope_and_unicode_token(self):
        self.assertEqual(self.scope.scope_id, "192dc49861428938df4113f0e490a15fe999700eedcfc5b43b2dc3029e784f61")
        self.assertEqual(fingerprint(self.scope, "cwd", "C:\\Synthetic\\样例"), "cf4dd9f561bc8cab8cf7dc7f3b71bb1236d0bdf84592763771da82876a1749d5")

    def test_scope_and_subject_domain_separation(self):
        value = "C:\\Synthetic\\Same"
        original = fingerprint(self.scope, "cwd", value)
        self.assertEqual(original, fingerprint(self.scope, "cwd", value))
        self.assertNotEqual(original, fingerprint(self.scope, "exec.node", value))
        other = Scope(hashlib.sha256(b"another-public-fixture").digest())
        self.assertNotEqual(self.scope.scope_id, other.scope_id)
        self.assertNotEqual(original, fingerprint(other, "cwd", value))
        self.assertNotIn(self.scope._key.hex(), repr(self.scope))

    def test_lexical_paths_preserve_everything_except_drive_and_slashes(self):
        self.assertEqual(normalize_path("c:/Mixed//Case/../样例"), "C:\\Mixed\\\\Case\\..\\样例")
        first = normalize_path_list("c:/A;;C:/A;C:/b")
        self.assertEqual(first, ["C:\\A", "", "C:\\A", "C:\\b"])
        self.assertNotEqual(fingerprint(self.scope, "env.PATH", first), fingerprint(self.scope, "env.PATH", list(reversed(first))))

    def test_path_limits_and_invalid_unicode(self):
        for value in (";" * 256, "A" * 4097, ";".join(["A" * 4096] * 8), "\ud800"):
            with self.subTest(length=len(value)), self.assertRaises(SafeError):
                normalize_path_list(value)

    def test_proxy_credentials_do_not_enter_endpoint_token(self):
        values = [normalize_proxy("http://fixture:password-one@proxy.synthetic.invalid:8080"),
                  normalize_proxy("HTTP://fixture:password-two@PROXY.SYNTHETIC.INVALID:8080/")]
        self.assertEqual(values[0], values[1])
        self.assertEqual(values[0], (["http", "proxy.synthetic.invalid", 8080], True))
        plain, present = normalize_proxy("http://proxy.synthetic.invalid:8080")
        self.assertFalse(present)
        self.assertEqual(fingerprint(self.scope, "env.HTTP_PROXY", plain), fingerprint(self.scope, "env.HTTP_PROXY", values[0][0]))

    def test_idna_ipv6_defaults(self):
        for value, expected in (("https://样例.invalid", ["https", "xn--fsq541d.invalid", 443]),
                                ("socks5://[2001:0db8:0:0::1]", ["socks5", "2001:db8::1", 1080]),
                                ("http://u:p%40word@host.invalid", ["http", "host.invalid", 80])):
            with self.subTest(value=value):
                self.assertEqual(normalize_proxy(value)[0], expected)

    def test_invalid_proxy_values_are_never_reflected(self):
        invalid = ("http://proxy.synthetic.invalid:", "http://proxy.synthetic.invalid/?secret=fixture",
                   "http://proxy.synthetic.invalid/#fixture", "http://bad%20host:8080", "http://",
                   "http://host.invalid:65536", "http://host.invalid:0", "http://host.invalid:bad",
                   " http://host.invalid", "http://bad host", "http://[fe80::1%25scope]", "ftp://host.invalid",
                   "http://host.invalid/other", "http://[::1]garbage", "http://a@b@host.invalid")
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(SafeError) as caught:
                normalize_proxy(value)
            self.assertNotIn(value, str(caught.exception))

    def test_invalid_fingerprint_arguments(self):
        for scope, subject, value in ((None, "cwd", "value"), (self.scope, "arbitrary", "value"), (self.scope, "cwd", "\ud800")):
            with self.assertRaises(SafeError):
                fingerprint(scope, subject, value)
