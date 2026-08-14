"""
Unit-тесты для app/markdown_formatter.py.

Запуск:
    python -m unittest app/test_markdown_formatter.py -v
или
    pytest app/test_markdown_formatter.py -v
"""

import unittest

from markdown_formatter import to_markdown, bold_legal_terms


class TestToMarkdownDeterminism(unittest.TestCase):
    def test_same_input_same_output(self):
        text = "Davlat organlari qonunga muvofiq ish yuritadi. Xodim huquqi himoyalanadi."
        r1 = to_markdown(text)
        r2 = to_markdown(text)
        self.assertEqual(r1, r2)


class TestEdgeCases(unittest.TestCase):
    def test_empty_string(self):
        self.assertEqual(to_markdown(""), "")

    def test_whitespace_only(self):
        self.assertEqual(to_markdown("   \n\n  "), "")

    def test_none_input(self):
        self.assertEqual(to_markdown(None), "")

    def test_text_without_enumeration(self):
        text = "Sudya qarori qonunga muvofiq chiqariladi."
        result = to_markdown(text)
        self.assertNotIn("- ", result)
        self.assertNotIn("1. ", result)
        self.assertIn("**", result)  # 'qonun' va 'sudya' bold bo'lishi kerak


class TestNumberedList(unittest.TestCase):
    def test_numbered_points_converted(self):
        text = (
            "Shartnoma quyidagi holatlarda bekor qilinadi: "
            "1. tomonlar kelishsa; 2. muddat tugasa; 3. sud qarori bilan."
        )
        result = to_markdown(text)
        self.assertIn("1. tomonlar kelishsa", result)
        self.assertIn("2. muddat tugasa", result)
        self.assertIn("3. sud qarori bilan", result)
        self.assertEqual(result.count("\n"), 3)  # вводная строка + 3 пункта списка

    def test_numbered_with_parenthesis_marker(self):
        text = "Talablar: 1) hujjat topshirish; 2) ariza yozish."
        result = to_markdown(text)
        # 'hujjat' — юридический термин из словаря, поэтому будет **bold**
        self.assertIn("1. **hujjat** topshirish", result)
        self.assertIn("2. ariza yozish", result)


class TestBulletEnumeration(unittest.TestCase):
    def test_enum_markers_converted_to_bullets(self):
        text = (
            "Xodim quyidagi huquqlarga ega: birinchidan, mehnatga haq olish, "
            "ikkinchidan, dam olish, shuningdek, ijtimoiy sugʻurta."
        )
        result = to_markdown(text)
        self.assertIn("- birinchidan", result.lower())
        self.assertIn("- ikkinchidan", result.lower())
        self.assertIn("- shuningdek", result.lower())
        self.assertEqual(result.count("- "), 3)

    def test_comma_separated_homogeneous_items(self):
        text = "sudya, prokuror, advokat, guvoh"
        result = to_markdown(text)
        lines = [l for l in result.split("\n") if l.strip()]
        self.assertTrue(all(l.startswith("- ") for l in lines))
        self.assertEqual(len(lines), 4)

    def test_comma_list_with_intro_before_colon(self):
        # Regression: intro-фраза + ":" перед перечислением через запятую
        # раньше не распознавалась как список (см. bugfix).
        text = "Bu huquqlarga quyidagilar kiradi: yashash, mehnat, taʼlim, sogʻliqni saqlash."
        result = to_markdown(text)
        lines = [l for l in result.split("\n") if l.strip()]
        self.assertTrue(lines[0].startswith("Bu"))
        self.assertTrue(lines[0].endswith(":"))
        item_lines = lines[1:]
        self.assertEqual(len(item_lines), 4)
        self.assertTrue(all(l.startswith("- ") for l in item_lines))

    def test_colon_without_real_list_not_split(self):
        # Двоеточие само по себе не должно провоцировать ложный список,
        # если после него нет 3+ элементов через запятую.
        text = "Bu qonun quyidagicha: fuqarolar huquqlarini himoya qiladi."
        result = to_markdown(text)
        self.assertNotIn("- ", result)


class TestBoldLegalTerms(unittest.TestCase):
    def test_term_bolded_with_suffix_preserved(self):
        # 'qonunga' -- word form of 'qonun' with case suffix -- must be
        # bolded as a whole word, not just the stem.
        result = bold_legal_terms("Bu ish qonunga muvofiq hal qilinadi.")
        self.assertIn("**qonunga**", result)

    def test_longer_term_preferred_over_shorter_prefix(self):
        # 'qonunchilik' should not be cut down to '**qonun**chilik'.
        result = bold_legal_terms("Qonunchilik hujjatlari yangilandi.")
        self.assertIn("**Qonunchilik**", result)
        self.assertNotIn("**Qonun**chilik", result)

    def test_no_terms_no_bold(self):
        result = bold_legal_terms("Bugun havo issiq edi.")
        self.assertNotIn("**", result)


class TestNestedStructure(unittest.TestCase):
    def test_paragraphs_with_nested_list_inside(self):
        text = (
            "Umumiy qoida shundan iboratki, davlat organlari qonunga muvofiq "
            "ish yuritadi.\n\n"
            "Xodimning huquqlari: 1. mehnatga haq olish; 2. dam olish huquqi; "
            "3. ijtimoiy sugʻurta."
        )
        result = to_markdown(text)
        blocks = result.split("\n\n")
        self.assertEqual(len(blocks), 2)
        # 'mehnat' — термин из словаря, будет выделен жирным
        self.assertIn("1. **mehnatga** haq olish", blocks[1])
        self.assertIn("**davlat**", blocks[0])


if __name__ == "__main__":
    unittest.main()
