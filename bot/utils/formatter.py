import html
import re
from typing import List

# Валидные теги разметки Telegram HTML
TG_TAG_PATTERN = re.compile(
    r'</?(?:b|strong|i|em|u|ins|s|strike|del|code|pre|tg-spoiler)\b[^>]*>|<a\s+href=["\'][^"\']+["\']>|</a>',
    re.IGNORECASE
)


def format_telegram_html(text: str) -> str:
    """
    Преобразует Markdown-разметку от LLM в валидный и чистый Telegram HTML:
    - Защищает блоки кода (<pre><code> и <code>) от искажений и экранирует их содержимое.
    - Сохраняет существующие корректные теги Telegram HTML (<b>, <i>, <code>, <pre>, <a> и т.д.).
    - Экранирует сырые спецсимволы (<, >, &) вне тегов и блоков кода.
    - Преобразует **жирный** и __жирный__ в <b>...</b>.
    - Преобразует ***жирный курсив*** в <b><i>...</i></b>.
    - Преобразует *курсив* и _курсив_ в <i>...</i>.
    - Преобразует # Заголовки в <b>...</b>.
    - Преобразует маркированные списки (- , * , + ) в аккуратные маркеры (• ).
    - Преобразует ссылки [текст](url) в <a href="url">текст</a>.
    - Полностью устраняет любые оставшиеся артефакты Markdown (одиночные или незакрытые **, ***, ##).
    """
    if not text:
        return ""

    # 1. Извлечение и защита блоков кода с уникальными маркерами
    code_blocks: List[str] = []

    def save_code_block(match: re.Match) -> str:
        lang = match.group(1) or ""
        code = match.group(2) if match.group(2) is not None else ""
        escaped_code = html.escape(code.strip())
        if lang:
            replacement = f'<pre><code class="language-{lang}">{escaped_code}</code></pre>'
        else:
            replacement = f'<pre><code>{escaped_code}</code></pre>'
        idx = len(code_blocks)
        code_blocks.append(replacement)
        return f"%%TGCODEBLOCK{idx}%%"

    text = re.sub(r'```([a-zA-Z0-9_\-]+)?\n?(.*?)```', save_code_block, text, flags=re.DOTALL)

    inline_codes: List[str] = []

    def save_inline_code(match: re.Match) -> str:
        code = match.group(1)
        escaped = html.escape(code)
        idx = len(inline_codes)
        inline_codes.append(f'<code>{escaped}</code>')
        return f"%%TGINLINECODE{idx}%%"

    text = re.sub(r'`([^`]+)`', save_inline_code, text)

    # 2. Сохранение уже существующих валидных HTML-тегов Telegram
    tg_tags: List[str] = []

    def save_valid_tag(match: re.Match) -> str:
        idx = len(tg_tags)
        tg_tags.append(match.group(0))
        return f"%%TGTAG{idx}%%"

    text = TG_TAG_PATTERN.sub(save_valid_tag, text)

    # 3. Экранирование оставшихся сырых спецсимволов HTML (&, <, >)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # 4. Преобразование заголовков Markdown (#, ##, ###) в <b>...</b>
    text = re.sub(r'(?m)^#{1,6}\s+(.+)$', r'<b>\1</b>', text)

    # 5. Преобразование ссылок [text](url) в <a href="url">text</a>
    text = re.sub(r'\[([^\]]+)\]\((https?://[^\s)]+)\)', r'<a href="\2">\1</a>', text)

    # 6. Преобразование жирного и курсивного начертания Markdown
    # ***жирный курсив***
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<b><i>\1</i></b>', text, flags=re.DOTALL)
    text = re.sub(r'___(.+?)___', r'<b><i>\1</i></b>', text, flags=re.DOTALL)

    # **жирный**
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text, flags=re.DOTALL)

    # *курсив* (только если не внутри слов/чисел)
    text = re.sub(r'(?<!\w)\*([^\s*](?:.*?[^\s*])?)\*(?!\w)', r'<i>\1</i>', text, flags=re.DOTALL)
    # _курсив_ (не затрагивая snake_case идентификаторы)
    text = re.sub(r'(?<!\w)_([^\s_](?:.*?[^\s_])?)_(?!\w)', r'<i>\1</i>', text, flags=re.DOTALL)

    # ~~зачеркнутый~~
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text, flags=re.DOTALL)

    # 7. Преобразование списков: строки со знаками списка (- , * , + ) -> аккуратный маркер •
    text = re.sub(r'(?m)^([\t ]*)[-*+]\s+', r'\1• ', text)

    # 8. Финальная зачистка любых оставшихся артефактов Markdown
    # Удаление любых оставшихся двойных или тройных звездочек (незакрытые теги)
    text = re.sub(r'\*{2,}', '', text)
    # Удаление одиночных звездочек, кроме математического умножения между числами
    text = re.sub(r'(?<!\d)\*(?!\d)', '', text)

    # 9. Восстановление сохраненных HTML-тегов и блоков кода
    for idx, tag in enumerate(tg_tags):
        text = text.replace(f"%%TGTAG{idx}%%", tag)

    for idx, replacement in enumerate(code_blocks):
        text = text.replace(f"%%TGCODEBLOCK{idx}%%", replacement)

    for idx, replacement in enumerate(inline_codes):
        text = text.replace(f"%%TGINLINECODE{idx}%%", replacement)

    return text.strip()


def strip_html_tags(text: str) -> str:
    """Удаляет все HTML-теги, возвращая чистый текст (аварийный fallback для Telegram)."""
    clean = re.sub(r'<[^>]+>', '', text)
    return html.unescape(clean)


def split_message_text(text: str, max_length: int = 4000) -> List[str]:
    """Разбивает длинное сообщение на части по границам абзацев или строк, не превышая max_length."""
    if len(text) <= max_length:
        return [text]

    chunks: List[str] = []
    lines = text.split("\n")
    current_chunk: List[str] = []
    current_len = 0

    for line in lines:
        if len(line) > max_length:
            if current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                current_len = 0
            while len(line) > max_length:
                chunks.append(line[:max_length])
                line = line[max_length:]
            if line:
                current_chunk.append(line)
                current_len = len(line)
            continue

        if current_len + len(line) + 1 > max_length and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = [line]
            current_len = len(line)
        else:
            current_chunk.append(line)
            current_len += len(line) + 1

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks
