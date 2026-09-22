import unittest
from bot.utils.formatter import format_telegram_html, strip_html_tags, split_message_text


class TestTelegramFormatter(unittest.TestCase):
    """Тестирование модуля форматирования текста для Telegram HTML."""

    def test_markdown_bold_conversion(self):
        """Проверка преобразования **жирный** и __жирный__ в <b>...</b>."""
        text = "Это **жирный текст** и еще один __жирный блок__."
        res = format_telegram_html(text)
        self.assertNotIn("**", res)
        self.assertNotIn("__", res)
        self.assertIn("<b>жирный текст</b>", res)
        self.assertIn("<b>жирный блок</b>", res)

    def test_markdown_italic_and_snake_case(self):
        """Проверка *курсива* и _курсива_ с сохранением snake_case идентификаторов."""
        text = "Это *курсив* и _второй курсив_, а вот переменная my_variable_name не должна ломаться."
        res = format_telegram_html(text)
        self.assertIn("<i>курсив</i>", res)
        self.assertIn("<i>второй курсив</i>", res)
        self.assertIn("my_variable_name", res)

    def test_headers_and_lists(self):
        """Проверка преобразования заголовков и списков."""
        text = (
            "### Заголовок раздела\n"
            "- Первый пункт\n"
            "* Второй пункт\n"
            "+ Третий пункт"
        )
        res = format_telegram_html(text)
        self.assertIn("<b>Заголовок раздела</b>", res)
        self.assertIn("• Первый пункт", res)
        self.assertIn("• Второй пункт", res)
        self.assertIn("• Третий пункт", res)
        self.assertNotIn("- Первый пункт", res)
        self.assertNotIn("* Второй пункт", res)

    def test_user_screenshot_case(self):
        """Проверка текста, идентичного скриншоту пользователя: отсутствие ** и правильное форматирование."""
        raw_text = (
            "1. **Титул**: Анализ рынка онлайн-образования в России (2024-2025)\n"
            "2. **Рыночные доли и сегменты**:\n"
            "- Таблица: основные игроки рынка (Skillbox > GeekBrains & Яндекс.Практикум)\n"
            "- Сегмент детского образования: рост на 25%\n"
            "3. **Выводы и рекомендации**:\n"
            "- Перспективы внедрения ИИ: **высокие**"
        )
        res = format_telegram_html(raw_text)

        # Главный инвариант: никаких двойных звездочек в итоговом сообщении
        self.assertNotIn("**", res)
        self.assertIn("1. <b>Титул</b>: Анализ рынка", res)
        self.assertIn("2. <b>Рыночные доли и сегменты</b>:", res)
        self.assertIn("• Таблица: основные игроки рынка (Skillbox &gt; GeekBrains &amp; Яндекс.Практикум)", res)
        self.assertIn("3. <b>Выводы и рекомендации</b>:", res)
        self.assertIn("• Перспективы внедрения ИИ: <b>высокие</b>", res)

    def test_existing_html_tags_preservation(self):
        """Проверка сохранения уже существующих корректных HTML-тегов."""
        text = "Текст с <b>уже жирным</b> и <i>уже курсивом</i>, а также x < 10 & y > 5."
        res = format_telegram_html(text)
        self.assertIn("<b>уже жирным</b>", res)
        self.assertIn("<i>уже курсивом</i>", res)
        self.assertIn("x &lt; 10 &amp; y &gt; 5", res)

    def test_code_blocks_protection(self):
        """Проверка защиты блоков кода от искажения разметки."""
        text = (
            "Посмотрите на код:\n"
            "```python\n"
            "def check(x, y):\n"
            "    # **комментарий со звездочками**\n"
            "    return x < 5 and y > 10\n"
            "```\n"
            "Также используйте команду `print(x < 5)`."
        )
        res = format_telegram_html(text)
        self.assertIn('<pre><code class="language-python">', res)
        self.assertIn("return x &lt; 5 and y &gt; 10", res)
        self.assertIn("<code>print(x &lt; 5)</code>", res)

    def test_markdown_links(self):
        """Проверка преобразования ссылок [text](url)."""
        text = "Подробнее на [официальном сайте](https://example.com/docs?a=1&b=2)."
        res = format_telegram_html(text)
        self.assertIn('<a href="https://example.com/docs?a=1&amp;b=2">официальном сайте</a>', res)

    def test_stray_and_unclosed_asterisks(self):
        """Проверка очистки незакрытых или случайных звездочек."""
        text = "Непарная **звездочка и еще одна *** здесь."
        res = format_telegram_html(text)
        self.assertNotIn("**", res)
        self.assertNotIn("***", res)

    def test_strip_html_tags(self):
        """Проверка очистки всех HTML-тегов в аварийном режиме."""
        html_text = "<b>Жирный</b> <i>курсив</i> и ссылка <a href='http://test.com'>Тест</a> &amp; символ."
        clean = strip_html_tags(html_text)
        self.assertEqual(clean, "Жирный курсив и ссылка Тест & символ.")

    def test_split_message_text(self):
        """Проверка разбиения сообщений на части."""
        # Короткое сообщение не разбивается
        short_text = "Короткое сообщение"
        self.assertEqual(split_message_text(short_text, max_length=100), [short_text])

        # Длинное сообщение с переносами строк разбивается по строкам
        lines = [f"Строка {i}" for i in range(50)]
        long_text = "\n".join(lines)
        chunks = split_message_text(long_text, max_length=100)
        self.assertTrue(len(chunks) > 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 100)
        # Все строки сохранены
        reconstructed = "\n".join(chunks)
        self.assertEqual(reconstructed, long_text)

        # Одиночная строка, превышающая max_length, жестко нарезается
        huge_line = "a" * 250
        chunks = split_message_text(huge_line, max_length=100)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0], "a" * 100)
        self.assertEqual(chunks[1], "a" * 100)
        self.assertEqual(chunks[2], "a" * 50)


if __name__ == "__main__":
    unittest.main()
