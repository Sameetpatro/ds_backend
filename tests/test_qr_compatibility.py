# tests/test_qr_compatibility.py

import os
import unittest
from app.utils import normalize_qr_value, format_qr_url, encode_base62, decode_base62, is_base62


class TestQrCompatibility(unittest.TestCase):

    def test_normalize_qr_value_plain_id(self):
        self.assertEqual(normalize_qr_value("NODE_1"), "NODE_1")
        self.assertEqual(normalize_qr_value("IIITS-0-KING"), "IIITS-0-KING")
        self.assertEqual(normalize_qr_value("IIIT-1-CANTEEN"), "IIIT-1-CANTEEN")
        self.assertEqual(normalize_qr_value("QTB-0-KING"), "QTB-0-KING")

    def test_normalize_qr_value_https_url(self):
        self.assertEqual(
            normalize_qr_value("https://my.domain.vercel.app/node/NODE_1"),
            "NODE_1"
        )
        self.assertEqual(
            normalize_qr_value("https://humsafar.vercel.app/node/IIITS-0-KING"),
            "IIITS-0-KING"
        )
        self.assertEqual(
            normalize_qr_value("http://localhost:3000/node/ABC_123"),
            "ABC_123"
        )

    def test_normalize_qr_value_trailing_slash(self):
        self.assertEqual(
            normalize_qr_value("https://my.domain.vercel.app/node/NODE_1/"),
            "NODE_1"
        )
        self.assertEqual(
            normalize_qr_value("https://humsafar.vercel.app/node/IIITS-0-KING/"),
            "IIITS-0-KING"
        )

    def test_normalize_qr_value_query_params_and_fragments(self):
        self.assertEqual(
            normalize_qr_value("https://my.domain.vercel.app/node/NODE_1?utm_source=qr&foo=bar"),
            "NODE_1"
        )
        self.assertEqual(
            normalize_qr_value("https://humsafar.vercel.app/node/IIITS-0-KING#details"),
            "IIITS-0-KING"
        )

    def test_normalize_qr_value_url_encoded(self):
        self.assertEqual(
            normalize_qr_value("https://humsafar.vercel.app/node/IIIT%2D1%2DCANTEEN"),
            "IIIT-1-CANTEEN"
        )
        self.assertEqual(
            normalize_qr_value("https://humsafar.vercel.app/node/NODE%201"),
            "NODE 1"
        )

    def test_normalize_qr_value_whitespace(self):
        self.assertEqual(
            normalize_qr_value("   https://humsafar.vercel.app/node/NODE_1   "),
            "NODE_1"
        )
        self.assertEqual(
            normalize_qr_value("   IIITS-0-KING   "),
            "IIITS-0-KING"
        )

    def test_normalize_qr_value_invalid_and_empty(self):
        self.assertIsNone(normalize_qr_value(None))
        self.assertIsNone(normalize_qr_value(""))
        self.assertIsNone(normalize_qr_value("   "))
        self.assertIsNone(normalize_qr_value("https://humsafar.vercel.app/node/"))

    def test_normalize_qr_value_path_traversal(self):
        self.assertIsNone(normalize_qr_value("https://humsafar.vercel.app/node/../../etc/passwd"))
        self.assertIsNone(normalize_qr_value("../secret"))

    def test_format_qr_url(self):
        url = format_qr_url("NODE_1", "https://my.domain.vercel.app/node")
        self.assertEqual(url, "https://my.domain.vercel.app/node/NODE_1")

        url_default = format_qr_url("IIITS-0-KING")
        self.assertTrue(url_default.endswith("/IIITS-0-KING"))



    def test_base62_encode_decode(self):
        self.assertEqual(encode_base62(100001), "Q0v")
        self.assertEqual(encode_base62(100002), "Q0w")
        self.assertEqual(encode_base62(100006), "Q10")
        self.assertEqual(decode_base62("Q0v"), 100001)
        self.assertEqual(decode_base62("Q0w"), 100002)
        self.assertEqual(decode_base62("Q10"), 100006)

    def test_base62_validation(self):
        self.assertTrue(is_base62("Q0v"))
        self.assertTrue(is_base62("Q10"))
        self.assertFalse(is_base62("IIITS-0-KING"))
        self.assertFalse(is_base62(None))

    def test_normalize_qr_value_base62_url(self):
        self.assertEqual(
            normalize_qr_value("https://dharohar-setu.onrender.com/node/Q0v"),
            "Q0v"
        )
        self.assertEqual(
            normalize_qr_value("https://dharohar-setu.onrender.com/node/Q0v/"),
            "Q0v"
        )
        self.assertEqual(
            normalize_qr_value("https://dharohar-setu.onrender.com/node/Q0v?utm_source=lens#spot"),
            "Q0v"
        )


if __name__ == "__main__":
    unittest.main()
