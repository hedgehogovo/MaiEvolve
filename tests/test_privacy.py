from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.store import redact_sensitive_text


class PrivacyTests(unittest.TestCase):
    def test_common_pii_patterns_are_redacted(self) -> None:
        email = "person" + chr(64) + "example" + chr(46) + "com"
        phone = "138" + "00138000"
        secret = "secret" + "123"
        long_number = "1234" + "56789"
        source = f"{email} {phone} password={secret} {long_number}"
        redacted = redact_sensitive_text(source)
        for sensitive_value in (email, phone, secret, long_number):
            self.assertNotIn(sensitive_value, redacted)


if __name__ == "__main__":
    unittest.main()
