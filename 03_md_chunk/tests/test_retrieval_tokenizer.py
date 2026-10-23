import unittest

from retrieval.tokenizer import ChineseTokenizer, normalize


class TokenizerTests(unittest.TestCase):
    def test_normalization_keeps_numbers_and_regions(self):
        self.assertEqual(normalize("  本省　AAA 2025  "), "本省 aaa 2025")

    def test_custom_term_is_not_split(self):
        tokenizer = ChineseTokenizer(["行业协会"])
        self.assertIn("行业协会", tokenizer.tokenize("行业协会颁发证书"))
