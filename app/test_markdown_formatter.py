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


class TestCyrillicScript(unittest.TestCase):
    """Приложение реально выводит упрощённый текст в кириллице —
    эти тесты проверяют, что вся логика (термины, суффиксы, границы
    слов, списки) работает не только на латинице, но и на кириллице."""

    def test_cyrillic_term_bolded_with_suffix(self):
        # 'қонунга' -- узбекская кириллическая словоформа с падежным
        # суффиксом -- должна выделяться целиком, а не только стем.
        result = bold_legal_terms("Бу иш қонунга мувофиқ ҳал қилинади.")
        self.assertIn("**қонунга**", result)

    def test_cyrillic_longer_term_preferred_over_shorter_prefix(self):
        result = bold_legal_terms("Қонунчилик ҳужжатлари янгиланди.")
        self.assertIn("**Қонунчилик**", result)
        self.assertNotIn("**Қонун**чилик", result)

    def test_cyrillic_sentence_paragraph_split(self):
        text = (
            "Давлат органлари қонунга мувофиқ иш юритади. "
            "Ходим ҳуқуқи ҳимояланади. Шахс шартномага асосан жавобгар."
        )
        result = to_markdown(text)
        self.assertIn("**Давлат**", result)
        self.assertIn("**Ходим**", result)
        self.assertIn("**Шахс**", result)

    def test_cyrillic_enum_markers_converted_to_bullets(self):
        text = (
            "Ходим қуйидаги ҳуқуқларга эга: биринчидан, меҳнатга ҳақ олиш, "
            "иккинчидан, дам олиш, шунингдек, ижтимоий суғурта."
        )
        result = to_markdown(text)
        self.assertIn("- биринчидан", result.lower())
        self.assertIn("- иккинчидан", result.lower())
        self.assertIn("- шунингдек", result.lower())
        self.assertEqual(result.count("- "), 3)

    def test_cyrillic_no_terms_no_bold(self):
        result = bold_legal_terms("Бугун ҳаво иссиқ эди.")
        self.assertNotIn("**", result)


class TestHeaders(unittest.TestCase):
    def test_chapter_header(self):
        text = "Глава 2. Фуқаролик шартномалари\n\nШартнома икки томон ўртасида тузилади."
        result = to_markdown(text)
        self.assertIn("## Глава 2. Фуқаролик шартномалари", result)
        self.assertNotIn("1. Глава", result)

    def test_article_as_section_header(self):
        text = (
            "Статья 5. Давлат органларининг вазифалари\n\n"
            "Давлат органлари қонунга мувофиқ иш юритади. Улар фуқаролар "
            "ҳуқуқларини ҳимоя қилади. Шунингдек назорат амалга оширади."
        )
        result = to_markdown(text)
        self.assertIn("### Статья 5", result)


class TestBlockquoteDefinitions(unittest.TestCase):
    def test_definition_becomes_blockquote(self):
        text = "Шартнома — бу икки томон ўртасидаги келишув деб тушунилади."
        result = to_markdown(text)
        self.assertTrue(any(l.startswith("> ") for l in result.split("\n")))


class TestArticleReferences(unittest.TestCase):
    def test_article_reference_inline_code(self):
        text = "Ушбу масала статья 10 асосида ҳал қилинади."
        result = to_markdown(text)
        self.assertIn("`статья 10`", result)


class TestTableConversion(unittest.TestCase):
    def test_regular_violation_sanction_table(self):
        text = (
            "Кечикиш — 1 кун жарима\n\n"
            "Бузилиш — 2 кун жарима\n\n"
            "Йўқотиш — 3 кун жарима"
        )
        result = to_markdown(text)
        self.assertIn("| Нарушение | Санкция |", result)
        self.assertEqual(result.count("\n| "), 4)  # 3 data rows + separator row

    def test_irregular_text_not_forced_into_table(self):
        text = "Сегодня хорошая погода. Судья принял решение по делу."
        result = to_markdown(text)
        self.assertNotIn("|", result)


class TestIdempotency(unittest.TestCase):
    def test_double_call_is_stable(self):
        text = (
            "Xodim quyidagi huquqlarga ega: birinchidan, mehnatga haq olish, "
            "ikkinchidan, dam olish, shuningdek, ijtimoiy sugʻurta."
        )
        once = to_markdown(text)
        twice = to_markdown(once)
        self.assertEqual(once, twice)
        self.assertNotIn("****", twice)


class TestCallouts(unittest.TestCase):
    def test_muhim_becomes_warning_blockquote(self):
        text = "Муҳим: ушбу шартнома нотариал тасдиқланиши шарт."
        result = to_markdown(text)
        self.assertTrue(result.startswith("> ⚠️"))

    def test_eslatma_becomes_warning_blockquote(self):
        text = "Eslatma: ariza 30 kun ichida topshirilishi kerak."
        result = to_markdown(text)
        self.assertTrue(result.startswith("> ⚠️"))


class TestNumberFormatting(unittest.TestCase):
    def test_percentage_wrapped_in_code(self):
        result = to_markdown("Жарима қиймати 15% ни ташкил этади.")
        self.assertIn("`15%`", result)

    def test_date_wrapped_in_code(self):
        result = to_markdown("Шартнома 12.05.2024 санасида тузилган.")
        self.assertIn("`12.05.2024`", result)

    def test_sum_wrapped_in_code(self):
        result = to_markdown("Жарима миқдори 500000 сўм ташкил этади.")
        self.assertIn("`500000 сўм`", result)


class TestOrAlternativesList(unittest.TestCase):
    def test_yoki_repeated_becomes_list(self):
        text = "Fuqaro pasport yoki guvohnoma yoki boshqa hujjat taqdim etishi mumkin."
        result = to_markdown(text)
        self.assertIn("- yoki", result)


class TestScriptParity(unittest.TestCase):
    """Проверка, что латиница и кириллица узбекского обрабатываются
    симметрично, включая узбекский порядок 'номер-слово' в заголовках
    глав/разделов ('2-bob', '3-bo'lim', '2-боб', '3-бўлим')."""

    def test_chapter_number_first_latin(self):
        text = "2-bob. Fuqarolik shartnomalari\n\nShartnoma ikki tomon orasida tuziladi."
        result = to_markdown(text)
        self.assertIn("## 2-Bob. Fuqarolik shartnomalari", result)

    def test_chapter_number_first_cyrillic(self):
        text = "2-боб. Фуқаролик шартномалари\n\nШартнома икки томон ўртасида тузилади."
        result = to_markdown(text)
        self.assertIn("## 2-Боб. Фуқаролик шартномалари", result)

    def test_section_number_first_latin_bolim(self):
        text = "3-bo'lim. Umumiy qoidalar\n\nUshbu bo'lim umumiy qoidalarni belgilaydi."
        result = to_markdown(text)
        self.assertIn("## 3-Bo", result)

    def test_word_first_still_works_both_scripts(self):
        self.assertIn("## Глава 2", to_markdown("Глава 2. Шартнома\n\nМатн давоми."))
        self.assertIn("## Bob 2", to_markdown("Bob 2. Shartnoma\n\nMatn davomi."))

    def test_abbreviation_expanded_latin(self):
        result = to_markdown("Ushbu masala FK asosida hal qilinadi.")
        self.assertIn("**FK** (Fuqarolik kodeksi)", result)

    def test_abbreviation_expanded_cyrillic(self):
        result = to_markdown("Ушбу масала ФК асосида ҳал қилинади.")
        self.assertIn("**ФК** (Фуқаролик кодекси)", result)

    def test_article_ref_latin_modda(self):
        result = to_markdown("Bu masala 10-modda asosida hal qilinadi.")
        self.assertIn("`10-modda`", result)

    def test_article_ref_cyrillic_modda(self):
        result = to_markdown("Бу масала 10-модда асосида ҳал қилинади.")
        self.assertIn("`10-модда`", result)


if __name__ == "__main__":
    unittest.main()
