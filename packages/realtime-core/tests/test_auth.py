import base64
import hashlib
import hmac
import json
import unittest

from auth import verify_ticket


SECRET = "test-secret-that-is-at-least-32-bytes-long"


def make_ticket(claims, secret=SECRET):
    payload = base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(secret.encode(), payload.encode("ascii"), hashlib.sha256).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return payload + "." + encoded_signature


class TicketTests(unittest.TestCase):
    def claims(self, **overrides):
        value = {"sub": "user-123", "aud": "paivoice", "iat": 1000, "exp": 1120, "jti": "unique-ticket-id-0001"}
        value.update(overrides)
        return value

    def test_valid_ticket(self):
        claims = verify_ticket(make_ticket(self.claims()), SECRET, now=1050, used_jti={})
        self.assertEqual(claims["sub"], "user-123")

    def test_rejects_expired_ticket(self):
        with self.assertRaises(ValueError):
            verify_ticket(make_ticket(self.claims(exp=1000)), SECRET, now=1000, used_jti={})

    def test_rejects_wrong_audience(self):
        with self.assertRaises(ValueError):
            verify_ticket(make_ticket(self.claims(aud="other")), SECRET, now=1050, used_jti={})

    def test_rejects_bad_signature(self):
        ticket = make_ticket(self.claims())
        with self.assertRaises(ValueError):
            verify_ticket(ticket, SECRET + "wrong", now=1050, used_jti={})

    def test_ticket_is_single_use(self):
        used = {}
        ticket = make_ticket(self.claims())
        verify_ticket(ticket, SECRET, now=1050, used_jti=used)
        with self.assertRaises(ValueError):
            verify_ticket(ticket, SECRET, now=1050, used_jti=used)

    def test_rejects_long_lifetime(self):
        with self.assertRaises(ValueError):
            verify_ticket(make_ticket(self.claims(exp=1400)), SECRET, now=1050, used_jti={})


if __name__ == "__main__":
    unittest.main()
