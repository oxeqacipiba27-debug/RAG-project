import asyncio
import csv
import io
import os
import re
import struct
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from loguru import logger
import pypdf
import docx
import openpyxl
from pptx import Presentation
import xlrd
from striprtf.striprtf import rtf_to_text
import olefile
import xml.etree.ElementTree as ET

from bot.services.vector_db import VectorDBService


class DocumentChunk:
    def __init__(self, text: str, source: str, page: int | str, chunk_index: int):
        self.text = text
        self.source = source
        self.page = page
        self.chunk_index = chunk_index

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "page": str(self.page),
            "chunk_index": self.chunk_index
        }


class DocumentIngestionService:
    """Сервис для парсинга документов (PDF, DOCX, DOC, RTF, XLSX, XLS, CSV, PPTX, PPT, TXT, MD) и нарезки на чанки."""

    def __init__(self, default_chunk_size: int = 500, default_overlap: int = 50):
        self.default_chunk_size = default_chunk_size
        self.default_overlap = default_overlap
        # Приблизительное соотношение: 1 токен ~ 3.5-4 символа для RU/EN
        self.chunk_size_chars = default_chunk_size * 4
        self.overlap_chars = default_overlap * 4

    def _split_text_into_chunks(
        self,
        text: str,
        source: str,
        page: int | str,
        start_index: int = 0,
        chunk_size_chars: Optional[int] = None,
        overlap_chars: Optional[int] = None,
    ) -> List[DocumentChunk]:
        """Разбивка текста на перекрывающиеся чанки с сохранением границ предложений/абзацев."""
        cleaned_text = re.sub(r"\s+", " ", text).strip()
        if not cleaned_text:
            return []

        limit_chunk_size = chunk_size_chars if chunk_size_chars is not None else self.chunk_size_chars
        limit_overlap = overlap_chars if overlap_chars is not None else self.overlap_chars

        # Если текст меньше размера чанка, возвращаем его целиком
        if len(cleaned_text) <= limit_chunk_size:
            return [DocumentChunk(text=cleaned_text, source=source, page=page, chunk_index=start_index)]

        chunks: List[DocumentChunk] = []
        # Разделение по предложениям и знакам препинания
        sentences = re.split(r"(?<=[.!?…])\s+", cleaned_text)
        
        current_chunk_sentences: List[str] = []
        current_length = 0
        idx = start_index

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            sentence_len = len(sentence)
            if current_length + sentence_len > limit_chunk_size and current_chunk_sentences:
                # Фиксация накопленного чанка
                chunk_str = " ".join(current_chunk_sentences).strip()
                chunks.append(DocumentChunk(text=chunk_str, source=source, page=page, chunk_index=idx))
                idx += 1

                # Расчет перекрытия (overlap) с конца текущего чанка
                overlap_sentences: List[str] = []
                overlap_len = 0
                for prev_sent in reversed(current_chunk_sentences):
                    if overlap_len + len(prev_sent) <= limit_overlap:
                        overlap_sentences.insert(0, prev_sent)
                        overlap_len += len(prev_sent)
                    else:
                        break

                current_chunk_sentences = overlap_sentences
                current_length = sum(len(s) for s in current_chunk_sentences)

            current_chunk_sentences.append(sentence)
            current_length += sentence_len

        if current_chunk_sentences:
            chunk_str = " ".join(current_chunk_sentences).strip()
            chunks.append(DocumentChunk(text=chunk_str, source=source, page=page, chunk_index=idx))

        return chunks

    @staticmethod
    def _extract_strings_from_bytes(data: bytes) -> str:
        """Извлечение читаемого текста (UTF-16LE и 8-bit CP1251/UTF-8) из бинарного потока."""
        lines = []
        # UTF-16LE последовательности (от 3 символов)
        for match in re.finditer(rb'(?:[\x20-\x7e\xa0-\xff]\x00|[\x00-\xff][\x04-\x05]){3,}', data):
            try:
                txt = match.group(0).decode("utf-16-le", errors="ignore").strip()
                if len(txt) >= 3 and not all(c in " \t\r\n" for c in txt):
                    lines.append(txt)
            except Exception:
                pass

        # 8-bit CP1251 / UTF-8 последовательности (от 4 символов)
        for match in re.finditer(rb'[\x20-\x7e\xc0-\xff]{4,}', data):
            try:
                txt = match.group(0).decode("windows-1251", errors="ignore").strip()
                if len(txt) >= 4 and not all(c in " \t\r\n" for c in txt):
                    lines.append(txt)
            except Exception:
                pass

        return "\n".join(lines)

    def _parse_pdf(
        self,
        file_path: Path,
        filename: str,
        chunk_size_chars: Optional[int] = None,
        overlap_chars: Optional[int] = None,
    ) -> List[DocumentChunk]:
        """Извлечение текста из PDF с разбивкой по страницам."""
        reader = pypdf.PdfReader(str(file_path))
        all_chunks: List[DocumentChunk] = []
        chunk_idx = 0

        for page_num, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            chunks = self._split_text_into_chunks(
                text,
                source=filename,
                page=page_num,
                start_index=chunk_idx,
                chunk_size_chars=chunk_size_chars,
                overlap_chars=overlap_chars,
            )
            all_chunks.extend(chunks)
            chunk_idx += len(chunks)

        return all_chunks

    def _parse_docx(
        self,
        file_path: Path,
        filename: str,
        chunk_size_chars: Optional[int] = None,
        overlap_chars: Optional[int] = None,
    ) -> List[DocumentChunk]:
        """Извлечение текста и таблиц из DOCX (.docx, .docm, .dotx, .dotm) с сохранением порядка и разделов."""
        # 1. Сигнатурный анализ заголовка файла
        try:
            with open(file_path, "rb") as f:
                header = f.read(2048)
        except Exception as e:
            logger.warning(f"Failed to read header of '{filename}': {e}")
            header = b""

        # Если файл на самом деле является OLE doc:
        if header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            logger.info(f"DOCX '{filename}' has OLE CFB header. Redirecting to _parse_doc.")
            return self._parse_doc(file_path, filename, chunk_size_chars, overlap_chars)

        # Если файл на самом деле является RTF:
        if header.startswith(b"{\\rtf"):
            logger.info(f"DOCX '{filename}' has RTF header. Redirecting to _parse_rtf.")
            return self._parse_rtf(file_path, filename, chunk_size_chars, overlap_chars)

        # Если файл является OpenDocument (ODT) или переименован:
        if b"application/vnd.oasis.opendocument" in header or (header.startswith(b"PK") and b"content.xml" in header):
            logger.info(f"DOCX '{filename}' has OpenDocument signature. Redirecting to _parse_odt.")
            return self._parse_odt(file_path, filename, chunk_size_chars, overlap_chars)

        # 2. Попытка открыть как стандартный Word DOCX
        doc = None
        try:
            doc = docx.Document(str(file_path))
        except (zipfile.BadZipFile, Exception) as open_err:
            logger.warning(f"python-docx failed to open '{filename}': {open_err}. Checking fallback recovery.")
            # Проверим, может это ODT, у которого не совпал mimetype в первых 2048 байтах
            try:
                if zipfile.is_zipfile(str(file_path)):
                    with zipfile.ZipFile(str(file_path), "r") as zf:
                        names = zf.namelist()
                        if "content.xml" in names and "word/document.xml" not in names:
                            return self._parse_odt(file_path, filename, chunk_size_chars, overlap_chars)
            except Exception:
                pass

            # Попытка извлечь читаемый текст с помощью string extractor
            try:
                with open(file_path, "rb") as f:
                    raw_data = f.read()
                raw_text = self._extract_strings_from_bytes(raw_data)
                cleaned_lines = [
                    line for line in raw_text.splitlines()
                    if not line.startswith("PK") and "word/" not in line and "[Content_Types]" not in line
                ]
                recovered_text = "\n".join(cleaned_lines).strip()
                if len(recovered_text) >= 50:
                    logger.info(f"Recovered {len(recovered_text)} chars from damaged DOCX '{filename}'.")
                    return self._split_text_into_chunks(
                        recovered_text,
                        source=filename,
                        page="Восстановленный текст",
                        start_index=0,
                        chunk_size_chars=chunk_size_chars,
                        overlap_chars=overlap_chars
                    )
            except Exception:
                pass

            raise ValueError(
                f"Файл «{filename}» поврежден или имеет недопустимый формат архива DOCX ({open_err}). "
                "Пожалуйста, проверьте целостность файла, сохраните его заново и повторите отправку."
            )

        all_chunks: List[DocumentChunk] = []
        chunk_idx = 0

        current_heading = "Общий раздел"
        buffer: List[str] = []

        try:
            # Последовательный обход элементов документа (параграфы и таблицы в естественном порядке)
            for child in doc.element.body:
                if child.tag.endswith("p"):
                    p = docx.text.paragraph.Paragraph(child, doc)
                    text = p.text.strip()
                    if not text:
                        continue

                    is_heading = False
                    if p.style and p.style.name:
                        st_name = p.style.name.lower()
                        if any(h in st_name for h in ["heading", "заголовок", "title", "название"]):
                            is_heading = True

                    if is_heading:
                        if buffer:
                            combined = "\n".join(buffer)
                            chunks = self._split_text_into_chunks(
                                combined,
                                source=filename,
                                page=current_heading,
                                start_index=chunk_idx,
                                chunk_size_chars=chunk_size_chars,
                                overlap_chars=overlap_chars,
                            )
                            all_chunks.extend(chunks)
                            chunk_idx += len(chunks)
                            buffer = []
                        current_heading = text
                    else:
                        buffer.append(text)

                elif child.tag.endswith("tbl"):
                    table = docx.table.Table(child, doc)
                    table_rows = []
                    for row in table.rows:
                        row_cells = [cell.text.strip() for cell in row.cells]
                        if any(row_cells):
                            table_rows.append(" | ".join(row_cells))
                    if table_rows:
                        buffer.append("\n".join(table_rows))
        except Exception as e:
            logger.warning(f"Sequential body traversal failed for {filename}: {e}. Falling back to standard paragraphs/tables.")
            for p in doc.paragraphs:
                text = p.text.strip()
                if text:
                    buffer.append(text)
            for tbl in doc.tables:
                for row in tbl.rows:
                    row_cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if row_cells:
                        buffer.append(" | ".join(row_cells))

        if buffer:
            combined = "\n".join(buffer)
            chunks = self._split_text_into_chunks(
                combined,
                source=filename,
                page=current_heading,
                start_index=chunk_idx,
                chunk_size_chars=chunk_size_chars,
                overlap_chars=overlap_chars,
            )
            all_chunks.extend(chunks)

        return all_chunks

    def _parse_odt(
        self,
        file_path: Path,
        filename: str,
        chunk_size_chars: Optional[int] = None,
        overlap_chars: Optional[int] = None,
    ) -> List[DocumentChunk]:
        """Извлечение текста, заголовков и таблиц из документов OpenDocument Text (.odt, .ott)."""
        content_xml = None
        # Попытка открыть через zipfile
        try:
            with zipfile.ZipFile(str(file_path), "r") as zf:
                if "content.xml" in zf.namelist():
                    content_xml = zf.read("content.xml")
        except Exception as e:
            logger.warning(f"Zip read failed for ODT '{filename}': {e}. Attempting fallback.")

        if not content_xml:
            # Fallback: попытка найти content.xml или извлечь строки
            try:
                with open(file_path, "rb") as f:
                    raw = f.read()
                raw_text = self._extract_strings_from_bytes(raw)
                if len(raw_text) >= 50:
                    return self._split_text_into_chunks(
                        raw_text,
                        source=filename,
                        page="OpenDocument Text",
                        start_index=0,
                        chunk_size_chars=chunk_size_chars,
                        overlap_chars=overlap_chars
                    )
            except Exception:
                pass

            raise ValueError(
                f"Не удалось прочитать документ OpenDocument «{filename}». "
                "Файл поврежден или имеет неверную структуру архива."
            )

        all_chunks: List[DocumentChunk] = []
        chunk_idx = 0

        try:
            root = ET.fromstring(content_xml)
            body = root.find('.//{urn:oasis:names:tc:opendocument:xmlns:office:1.0}body')
            target = body.find('.//{urn:oasis:names:tc:opendocument:xmlns:office:1.0}text') if body is not None else root
            if target is None:
                target = root

            current_heading = "Общий раздел"
            buffer: List[str] = []

            def get_node_text(node) -> str:
                return "".join(node.itertext()).strip()

            for elem in target:
                tag = elem.tag.split('}')[-1]
                if tag == 'h':
                    h_text = get_node_text(elem)
                    if h_text:
                        if buffer:
                            combined = "\n".join(buffer)
                            chunks = self._split_text_into_chunks(
                                combined,
                                source=filename,
                                page=current_heading,
                                start_index=chunk_idx,
                                chunk_size_chars=chunk_size_chars,
                                overlap_chars=overlap_chars,
                            )
                            all_chunks.extend(chunks)
                            chunk_idx += len(chunks)
                            buffer = []
                        current_heading = h_text
                        buffer.append(f"# {h_text}")
                elif tag == 'p':
                    p_text = get_node_text(elem)
                    if p_text:
                        buffer.append(p_text)
                elif tag == 'table':
                    table_rows = []
                    for row in elem.findall('.//{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table-row'):
                        cells = [get_node_text(c) for c in row.findall('.//{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table-cell')]
                        if any(cells):
                            table_rows.append(" | ".join(cells))
                    if table_rows:
                        buffer.append("\n".join(table_rows))
                elif tag == 'list':
                    for item in elem.findall('.//{urn:oasis:names:tc:opendocument:xmlns:text:1.0}list-item'):
                        it_txt = get_node_text(item)
                        if it_txt:
                            buffer.append(f"• {it_txt}")

            if buffer:
                combined = "\n".join(buffer)
                chunks = self._split_text_into_chunks(
                    combined,
                    source=filename,
                    page=current_heading,
                    start_index=chunk_idx,
                    chunk_size_chars=chunk_size_chars,
                    overlap_chars=overlap_chars,
                )
                all_chunks.extend(chunks)

        except Exception as e:
            logger.error(f"Error parsing XML in ODT '{filename}': {e}")
            raw_text = self._extract_strings_from_bytes(content_xml)
            if raw_text:
                return self._split_text_into_chunks(
                    raw_text,
                    source=filename,
                    page="OpenDocument Text",
                    start_index=0,
                    chunk_size_chars=chunk_size_chars,
                    overlap_chars=overlap_chars
                )

        return all_chunks

    def _parse_ods(
        self,
        file_path: Path,
        filename: str,
        chunk_size_chars: Optional[int] = None,
        overlap_chars: Optional[int] = None,
    ) -> List[DocumentChunk]:
        """Извлечение табличных данных из электронных таблиц OpenDocument Spreadsheet (.ods, .ots)."""
        content_xml = None
        try:
            with zipfile.ZipFile(str(file_path), "r") as zf:
                if "content.xml" in zf.namelist():
                    content_xml = zf.read("content.xml")
        except Exception as e:
            raise ValueError(f"Ошибка чтения ODS-архива «{filename}»: {e}")

        if not content_xml:
            raise ValueError(f"Файл «{filename}» не содержит таблицы content.xml.")

        root = ET.fromstring(content_xml)
        body = root.find('.//{urn:oasis:names:tc:opendocument:xmlns:office:1.0}body')
        target = body.find('.//{urn:oasis:names:tc:opendocument:xmlns:office:1.0}spreadsheet') if body is not None else root
        if target is None:
            target = root

        all_chunks: List[DocumentChunk] = []
        chunk_idx = 0

        for table in target.findall('.//{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table'):
            sheet_name = table.attrib.get('{urn:oasis:names:tc:opendocument:xmlns:table:1.0}name', 'Лист')
            page_label = f"Лист: {sheet_name}"
            rows_text = []
            header_row = ""

            for row in table.findall('.//{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table-row'):
                cells = ["".join(c.itertext()).strip() for c in row.findall('.//{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table-cell')]
                while cells and not cells[-1]:
                    cells.pop()
                if cells:
                    row_str = " | ".join(cells)
                    if not header_row:
                        header_row = row_str
                    rows_text.append(row_str)

            if rows_text:
                full_sheet_text = "\n".join(rows_text)
                chunks = self._split_text_into_chunks(
                    full_sheet_text,
                    source=filename,
                    page=page_label,
                    start_index=chunk_idx,
                    chunk_size_chars=chunk_size_chars,
                    overlap_chars=overlap_chars,
                )
                if len(chunks) > 1 and header_row:
                    for i in range(1, len(chunks)):
                        if not chunks[i].text.startswith(header_row):
                            chunks[i].text = f"[Заголовок таблицы: {header_row}]\n" + chunks[i].text
                all_chunks.extend(chunks)
                chunk_idx += len(chunks)

        return all_chunks

    def _parse_odp(
        self,
        file_path: Path,
        filename: str,
        chunk_size_chars: Optional[int] = None,
        overlap_chars: Optional[int] = None,
    ) -> List[DocumentChunk]:
        """Извлечение текста и слайдов из презентаций OpenDocument Presentation (.odp, .otp)."""
        content_xml = None
        try:
            with zipfile.ZipFile(str(file_path), "r") as zf:
                if "content.xml" in zf.namelist():
                    content_xml = zf.read("content.xml")
        except Exception as e:
            raise ValueError(f"Ошибка чтения ODP-архива «{filename}»: {e}")

        if not content_xml:
            raise ValueError(f"Файл «{filename}» не содержит презентации content.xml.")

        root = ET.fromstring(content_xml)
        pages = root.findall('.//{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}page')
        all_chunks: List[DocumentChunk] = []
        chunk_idx = 0

        for slide_idx, page in enumerate(pages, start=1):
            slide_title = page.attrib.get('{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}name', f"Слайд {slide_idx}")
            page_label = f"Слайд {slide_idx}: {slide_title}"
            slide_lines = []
            for elem in page.findall('.//{urn:oasis:names:tc:opendocument:xmlns:text:1.0}p'):
                txt = "".join(elem.itertext()).strip()
                if txt:
                    slide_lines.append(txt)

            if slide_lines:
                slide_text = "\n".join(slide_lines)
                chunks = self._split_text_into_chunks(
                    slide_text,
                    source=filename,
                    page=page_label,
                    start_index=chunk_idx,
                    chunk_size_chars=chunk_size_chars,
                    overlap_chars=overlap_chars,
                )
                all_chunks.extend(chunks)
                chunk_idx += len(chunks)

        return all_chunks

    def _parse_doc(
        self,
        file_path: Path,
        filename: str,
        chunk_size_chars: Optional[int] = None,
        overlap_chars: Optional[int] = None,
    ) -> List[DocumentChunk]:
        """Извлечение текста из бинарных файлов Microsoft Word 97-2003 (.doc, .dot)."""
        # 1. Проверка: не переименован ли DOCX в DOC
        if zipfile.is_zipfile(str(file_path)):
            logger.info(f"File '{filename}' is actually OpenXML (DOCX). Delegating to _parse_docx.")
            return self._parse_docx(file_path, filename, chunk_size_chars, overlap_chars)

        # 2. Проверка: не переименован ли RTF в DOC
        try:
            with open(file_path, "rb") as f:
                header = f.read(10)
                if header.startswith(b"{\\rtf"):
                    logger.info(f"File '{filename}' is actually RTF. Delegating to _parse_rtf.")
                    return self._parse_rtf(file_path, filename, chunk_size_chars, overlap_chars)
        except Exception:
            pass

        # 3. Парсинг OLE Compound Document (структура WordDocument и Piece Table)
        extracted_text = ""
        if olefile.isOleFile(str(file_path)):
            try:
                with olefile.OleFileIO(str(file_path)) as ole:
                    if ole.exists("WordDocument"):
                        word_stream = ole.openstream("WordDocument").read()
                        # Попытка парсинга FIB (File Information Block) и Clx Piece Table
                        if len(word_stream) >= 0x01A6 and struct.unpack_from("<H", word_stream, 0)[0] == 0xA5EC:
                            fib_flags = struct.unpack_from("<H", word_stream, 0x000A)[0]
                            table_stream_name = "1Table" if (fib_flags & 0x0200) else "0Table"
                            fcClx = struct.unpack_from("<I", word_stream, 0x01A2)[0]
                            lcbClx = struct.unpack_from("<I", word_stream, 0x01A6)[0]

                            if ole.exists(table_stream_name) and lcbClx > 0:
                                table_stream = ole.openstream(table_stream_name).read()
                                if fcClx + lcbClx <= len(table_stream):
                                    clx_data = table_stream[fcClx : fcClx + lcbClx]
                                    pos = 0
                                    text_parts = []
                                    while pos < len(clx_data):
                                        clxt = clx_data[pos]
                                        if clxt == 0x01:  # grpprl
                                            cbGrpprl = struct.unpack_from("<H", clx_data, pos + 1)[0]
                                            pos += 3 + cbGrpprl
                                        elif clxt == 0x02:  # Plcfpcd (Pcdt)
                                            lcb = struct.unpack_from("<I", clx_data, pos + 1)[0]
                                            pcdt_data = clx_data[pos + 5 : pos + 5 + lcb]
                                            n_pieces = (len(pcdt_data) - 4) // 12
                                            if n_pieces > 0:
                                                cps = [struct.unpack_from("<I", pcdt_data, i * 4)[0] for i in range(n_pieces + 1)]
                                                pcds_offset = 4 * (n_pieces + 1)
                                                for i in range(n_pieces):
                                                    char_count = cps[i + 1] - cps[i]
                                                    pcd_bytes = pcdt_data[pcds_offset + i * 8 : pcds_offset + (i + 1) * 8]
                                                    fcValue = struct.unpack_from("<I", pcd_bytes, 2)[0]
                                                    fCompressed = (fcValue & 0x40000000) != 0
                                                    actual_fc = fcValue & ~0x40000000
                                                    if fCompressed:
                                                        byte_offset = actual_fc // 2
                                                        raw = word_stream[byte_offset : byte_offset + char_count]
                                                        try:
                                                            text_parts.append(raw.decode("windows-1251", errors="replace"))
                                                        except Exception:
                                                            text_parts.append(raw.decode("latin-1", errors="replace"))
                                                    else:
                                                        byte_offset = actual_fc
                                                        raw = word_stream[byte_offset : byte_offset + char_count * 2]
                                                        text_parts.append(raw.decode("utf-16-le", errors="replace"))
                                                extracted_text = "".join(text_parts)
                                            break
                                        else:
                                            break

                        if not extracted_text:
                            # Запасное извлечение строк из потока WordDocument
                            extracted_text = self._extract_strings_from_bytes(word_stream)
            except Exception as e:
                logger.warning(f"Error extracting from OLE .doc stream for '{filename}': {e}")

        # 4. Запасное чтение файла как бинарного потока / plain text
        if not extracted_text:
            try:
                raw_bytes = file_path.read_bytes()
                extracted_text = self._extract_strings_from_bytes(raw_bytes)
            except Exception as e:
                logger.warning(f"Raw string extraction failed for '{filename}': {e}")

        # Очистка управляющих символов Word
        cleaned = extracted_text.replace("\r\n", "\n").replace("\r", "\n").replace("\x07", " | ").replace("\x0b", "\n")
        cleaned = "".join(c for c in cleaned if c in ("\n", "\t") or (ord(c) >= 32 and ord(c) != 127))

        return self._split_text_into_chunks(
            cleaned,
            source=filename,
            page="Документ Word",
            start_index=0,
            chunk_size_chars=chunk_size_chars,
            overlap_chars=overlap_chars,
        )

    def _parse_rtf(
        self,
        file_path: Path,
        filename: str,
        chunk_size_chars: Optional[int] = None,
        overlap_chars: Optional[int] = None,
    ) -> List[DocumentChunk]:
        """Извлечение текста из файлов RTF (Rich Text Format)."""
        encodings = ["utf-8", "windows-1251", "latin-1"]
        raw_content = ""
        for enc in encodings:
            try:
                raw_content = file_path.read_text(encoding=enc)
                break
            except Exception:
                continue

        if not raw_content:
            logger.warning(f"Could not read RTF file content: {file_path}")
            return []

        try:
            plain_text = rtf_to_text(raw_content, errors="ignore")
        except Exception as e:
            logger.warning(f"Error parsing RTF via striprtf for {file_path}: {e}")
            plain_text = raw_content

        return self._split_text_into_chunks(
            plain_text,
            source=filename,
            page="Документ RTF",
            start_index=0,
            chunk_size_chars=chunk_size_chars,
            overlap_chars=overlap_chars,
        )

    def _parse_excel(
        self,
        file_path: Path,
        filename: str,
        chunk_size_chars: Optional[int] = None,
        overlap_chars: Optional[int] = None,
    ) -> List[DocumentChunk]:
        """Извлечение табличных данных из Excel (.xlsx, .xlsm, .xltx, .xltm)."""
        wb = openpyxl.load_workbook(str(file_path), data_only=True, read_only=True)
        all_chunks: List[DocumentChunk] = []
        chunk_idx = 0
        limit_chunk_size = chunk_size_chars if chunk_size_chars is not None else self.chunk_size_chars

        try:
            for sheet_name in wb.sheetnames:
                sheet = wb[sheet_name]
                page_name = f"Лист: {sheet_name}"

                rows = []
                for row in sheet.iter_rows(values_only=True):
                    clean_row = [str(c).strip() if c is not None else "" for c in row]
                    if any(clean_row):
                        rows.append(clean_row)

                if not rows:
                    continue

                header = rows[0]
                header_str = " | ".join(header)
                data_rows = rows[1:] if len(rows) > 1 else []

                if not data_rows:
                    chunks = self._split_text_into_chunks(
                        header_str,
                        source=filename,
                        page=page_name,
                        start_index=chunk_idx,
                        chunk_size_chars=chunk_size_chars,
                        overlap_chars=overlap_chars,
                    )
                    all_chunks.extend(chunks)
                    chunk_idx += len(chunks)
                    continue

                current_block = [f"Таблица '{sheet_name}':", header_str, "-" * min(len(header_str), 60)]
                curr_len = sum(len(x) for x in current_block)

                for r in data_rows:
                    row_str = " | ".join(r)
                    if curr_len + len(row_str) > limit_chunk_size and len(current_block) > 3:
                        block_text = "\n".join(current_block)
                        chunks = self._split_text_into_chunks(
                            block_text,
                            source=filename,
                            page=page_name,
                            start_index=chunk_idx,
                            chunk_size_chars=chunk_size_chars,
                            overlap_chars=overlap_chars,
                        )
                        all_chunks.extend(chunks)
                        chunk_idx += len(chunks)

                        # Переносим заголовок таблицы в следующий чанк для сохранения контекста
                        current_block = [f"Таблица '{sheet_name}' (продолжение):", header_str, "-" * min(len(header_str), 60), row_str]
                        curr_len = sum(len(x) for x in current_block)
                    else:
                        current_block.append(row_str)
                        curr_len += len(row_str)

                if current_block:
                    block_text = "\n".join(current_block)
                    chunks = self._split_text_into_chunks(
                        block_text,
                        source=filename,
                        page=page_name,
                        start_index=chunk_idx,
                        chunk_size_chars=chunk_size_chars,
                        overlap_chars=overlap_chars,
                    )
                    all_chunks.extend(chunks)
                    chunk_idx += len(chunks)
        finally:
            wb.close()

        return all_chunks

    def _parse_xls(
        self,
        file_path: Path,
        filename: str,
        chunk_size_chars: Optional[int] = None,
        overlap_chars: Optional[int] = None,
    ) -> List[DocumentChunk]:
        """Извлечение данных из файлов Excel 97-2003 (.xls)."""
        if zipfile.is_zipfile(str(file_path)):
            logger.info(f"File '{filename}' is actually OpenXML (XLSX). Delegating to _parse_excel.")
            return self._parse_excel(file_path, filename, chunk_size_chars, overlap_chars)

        wb = xlrd.open_workbook(str(file_path))
        all_chunks: List[DocumentChunk] = []
        chunk_idx = 0
        limit_chunk_size = chunk_size_chars if chunk_size_chars is not None else self.chunk_size_chars

        for sheet in wb.sheets():
            sheet_name = sheet.name
            page_name = f"Лист: {sheet_name}"

            rows = []
            for r in range(sheet.nrows):
                clean_row = [str(val).strip() for val in sheet.row_values(r)]
                # Приведение вещественных чисел типа 100.0 к 100, если нет дробной части
                formatted_row = []
                for val in clean_row:
                    if val.endswith(".0"):
                        try:
                            val = str(int(float(val)))
                        except ValueError:
                            pass
                    formatted_row.append(val)
                if any(formatted_row):
                    rows.append(formatted_row)

            if not rows:
                continue

            header = rows[0]
            header_str = " | ".join(header)
            data_rows = rows[1:] if len(rows) > 1 else []

            if not data_rows:
                chunks = self._split_text_into_chunks(
                    header_str,
                    source=filename,
                    page=page_name,
                    start_index=chunk_idx,
                    chunk_size_chars=chunk_size_chars,
                    overlap_chars=overlap_chars,
                )
                all_chunks.extend(chunks)
                chunk_idx += len(chunks)
                continue

            current_block = [f"Таблица '{sheet_name}':", header_str, "-" * min(len(header_str), 60)]
            curr_len = sum(len(x) for x in current_block)

            for r in data_rows:
                row_str = " | ".join(r)
                if curr_len + len(row_str) > limit_chunk_size and len(current_block) > 3:
                    block_text = "\n".join(current_block)
                    chunks = self._split_text_into_chunks(
                        block_text,
                        source=filename,
                        page=page_name,
                        start_index=chunk_idx,
                        chunk_size_chars=chunk_size_chars,
                        overlap_chars=overlap_chars,
                    )
                    all_chunks.extend(chunks)
                    chunk_idx += len(chunks)

                    current_block = [f"Таблица '{sheet_name}' (продолжение):", header_str, "-" * min(len(header_str), 60), row_str]
                    curr_len = sum(len(x) for x in current_block)
                else:
                    current_block.append(row_str)
                    curr_len += len(row_str)

            if current_block:
                block_text = "\n".join(current_block)
                chunks = self._split_text_into_chunks(
                    block_text,
                    source=filename,
                    page=page_name,
                    start_index=chunk_idx,
                    chunk_size_chars=chunk_size_chars,
                    overlap_chars=overlap_chars,
                )
                all_chunks.extend(chunks)
                chunk_idx += len(chunks)

        return all_chunks

    def _parse_csv(
        self,
        file_path: Path,
        filename: str,
        chunk_size_chars: Optional[int] = None,
        overlap_chars: Optional[int] = None,
    ) -> List[DocumentChunk]:
        """Извлечение табличных данных из CSV-файлов."""
        encodings = ["utf-8-sig", "utf-8", "windows-1251", "latin-1"]
        content = ""
        for enc in encodings:
            try:
                content = file_path.read_text(encoding=enc)
                break
            except Exception:
                continue

        if not content:
            logger.warning(f"Could not read CSV file content: {file_path}")
            return []

        # Определение разделителя (запятая, точка с запятой, табуляция)
        delimiter = ","
        first_line = content.splitlines()[0] if content.splitlines() else ""
        if ";" in first_line and first_line.count(";") > first_line.count(","):
            delimiter = ";"
        elif "\t" in first_line:
            delimiter = "\t"

        reader = csv.reader(io.StringIO(content), delimiter=delimiter)
        rows = [[cell.strip() for cell in row] for row in reader if any(cell.strip() for cell in row)]

        if not rows:
            return []

        all_chunks: List[DocumentChunk] = []
        chunk_idx = 0
        limit_chunk_size = chunk_size_chars if chunk_size_chars is not None else self.chunk_size_chars
        page_name = "Таблица CSV"

        header = rows[0]
        header_str = " | ".join(header)
        data_rows = rows[1:] if len(rows) > 1 else []

        if not data_rows:
            return self._split_text_into_chunks(
                header_str,
                source=filename,
                page=page_name,
                start_index=0,
                chunk_size_chars=chunk_size_chars,
                overlap_chars=overlap_chars,
            )

        current_block = [f"Таблица '{filename}':", header_str, "-" * min(len(header_str), 60)]
        curr_len = sum(len(x) for x in current_block)

        for r in data_rows:
            row_str = " | ".join(r)
            if curr_len + len(row_str) > limit_chunk_size and len(current_block) > 3:
                block_text = "\n".join(current_block)
                chunks = self._split_text_into_chunks(
                    block_text,
                    source=filename,
                    page=page_name,
                    start_index=chunk_idx,
                    chunk_size_chars=chunk_size_chars,
                    overlap_chars=overlap_chars,
                )
                all_chunks.extend(chunks)
                chunk_idx += len(chunks)

                current_block = [f"Таблица '{filename}' (продолжение):", header_str, "-" * min(len(header_str), 60), row_str]
                curr_len = sum(len(x) for x in current_block)
            else:
                current_block.append(row_str)
                curr_len += len(row_str)

        if current_block:
            block_text = "\n".join(current_block)
            chunks = self._split_text_into_chunks(
                block_text,
                source=filename,
                page=page_name,
                start_index=chunk_idx,
                chunk_size_chars=chunk_size_chars,
                overlap_chars=overlap_chars,
            )
            all_chunks.extend(chunks)

        return all_chunks

    def _parse_pptx(
        self,
        file_path: Path,
        filename: str,
        chunk_size_chars: Optional[int] = None,
        overlap_chars: Optional[int] = None,
    ) -> List[DocumentChunk]:
        """Извлечение текста и таблиц из презентаций PowerPoint (.pptx, .pptm, .potx, .potm, .ppsx, .pps)."""
        prs = Presentation(str(file_path))
        all_chunks: List[DocumentChunk] = []
        chunk_idx = 0

        for slide_idx, slide in enumerate(prs.slides, start=1):
            slide_page = f"Слайд {slide_idx}"
            slide_texts = []

            # Заголовок слайда
            if slide.shapes.title and slide.shapes.title.text.strip():
                slide_texts.append(f"Заголовок: {slide.shapes.title.text.strip()}")

            # Текстовые блоки, списки и таблицы
            for shape in slide.shapes:
                if shape == slide.shapes.title:
                    continue

                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        text = para.text.strip()
                        if text:
                            slide_texts.append(text)

                if shape.has_table:
                    for row in shape.table.rows:
                        row_cells = [c.text.strip() for c in row.cells if c.text.strip()]
                        if row_cells:
                            slide_texts.append(" | ".join(row_cells))

            # Заметки докладчика
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                notes_text = slide.notes_slide.notes_text_frame.text.strip()
                if notes_text:
                    slide_texts.append(f"Заметки к слайду: {notes_text}")

            if slide_texts:
                combined_slide_text = "\n".join(slide_texts)
                chunks = self._split_text_into_chunks(
                    combined_slide_text,
                    source=filename,
                    page=slide_page,
                    start_index=chunk_idx,
                    chunk_size_chars=chunk_size_chars,
                    overlap_chars=overlap_chars,
                )
                all_chunks.extend(chunks)
                chunk_idx += len(chunks)

        return all_chunks

    def _parse_ppt(
        self,
        file_path: Path,
        filename: str,
        chunk_size_chars: Optional[int] = None,
        overlap_chars: Optional[int] = None,
    ) -> List[DocumentChunk]:
        """Извлечение текста из бинарных презентаций PowerPoint 97-2003 (.ppt, .pot)."""
        if zipfile.is_zipfile(str(file_path)):
            logger.info(f"File '{filename}' is actually OpenXML (PPTX). Delegating to _parse_pptx.")
            return self._parse_pptx(file_path, filename, chunk_size_chars, overlap_chars)

        texts = []
        if olefile.isOleFile(str(file_path)):
            try:
                with olefile.OleFileIO(str(file_path)) as ole:
                    if ole.exists("PowerPoint Document"):
                        ppt_stream = ole.openstream("PowerPoint Document").read()
                        pos = 0
                        while pos + 8 <= len(ppt_stream):
                            ver_inst, rec_type, rec_len = struct.unpack_from("<HHI", ppt_stream, pos)
                            pos += 8
                            if pos + rec_len > len(ppt_stream):
                                break
                            rec_data = ppt_stream[pos : pos + rec_len]
                            pos += rec_len

                            if rec_type == 0x03EE:  # TextBytesAtom
                                try:
                                    txt = rec_data.decode("windows-1251", errors="replace").strip()
                                    txt = "".join(c for c in txt if c in ("\n", "\t") or (ord(c) >= 32 and ord(c) != 127))
                                    if txt:
                                        texts.append(txt)
                                except Exception:
                                    pass
                            elif rec_type in (0x03F8, 0x0FA8):  # TextCharsAtom / CString
                                try:
                                    txt = rec_data.decode("utf-16-le", errors="replace").strip()
                                    txt = "".join(c for c in txt if c in ("\n", "\t") or (ord(c) >= 32 and ord(c) != 127))
                                    if txt:
                                        texts.append(txt)
                                except Exception:
                                    pass

                        if not texts:
                            fallback = self._extract_strings_from_bytes(ppt_stream)
                            if fallback.strip():
                                texts.append(fallback.strip())
            except Exception as e:
                logger.warning(f"Error parsing OLE PPT for '{filename}': {e}")

        if not texts:
            try:
                raw_bytes = file_path.read_bytes()
                fallback = self._extract_strings_from_bytes(raw_bytes)
                if fallback.strip():
                    texts.append(fallback.strip())
            except Exception as e:
                logger.warning(f"Raw extraction failed for PPT '{filename}': {e}")

        combined = "\n".join(texts)
        return self._split_text_into_chunks(
            combined,
            source=filename,
            page="Презентация PPT",
            start_index=0,
            chunk_size_chars=chunk_size_chars,
            overlap_chars=overlap_chars,
        )

    def _parse_text(
        self,
        file_path: Path,
        filename: str,
        chunk_size_chars: Optional[int] = None,
        overlap_chars: Optional[int] = None,
    ) -> List[DocumentChunk]:
        """Чтение текстовых файлов (.txt, .md)."""
        encodings = ["utf-8", "windows-1251", "latin-1"]
        content = ""
        for enc in encodings:
            try:
                content = file_path.read_text(encoding=enc)
                break
            except UnicodeDecodeError:
                continue

        if not content:
            logger.warning(f"Could not decode file content: {file_path}")
            return []

        return self._split_text_into_chunks(
            content,
            source=filename,
            page=1,
            start_index=0,
            chunk_size_chars=chunk_size_chars,
            overlap_chars=overlap_chars,
        )

    def _sync_process_file(
        self,
        file_path: Path,
        filename: str,
        chunk_size_chars: Optional[int] = None,
        overlap_chars: Optional[int] = None,
    ) -> List[DocumentChunk]:
        """Синхронный парсинг документа в зависимости от расширения."""
        file_path = Path(file_path)
        suffix = file_path.suffix.lower()
        if suffix == ".pdf":
            return self._parse_pdf(file_path, filename, chunk_size_chars, overlap_chars)
        elif suffix in [".docx", ".docm", ".dotx", ".dotm"]:
            return self._parse_docx(file_path, filename, chunk_size_chars, overlap_chars)
        elif suffix in [".odt", ".ott"]:
            return self._parse_odt(file_path, filename, chunk_size_chars, overlap_chars)
        elif suffix in [".doc", ".dot"]:
            return self._parse_doc(file_path, filename, chunk_size_chars, overlap_chars)
        elif suffix == ".rtf":
            return self._parse_rtf(file_path, filename, chunk_size_chars, overlap_chars)
        elif suffix in [".xlsx", ".xlsm", ".xltx", ".xltm"]:
            return self._parse_excel(file_path, filename, chunk_size_chars, overlap_chars)
        elif suffix in [".ods", ".ots"]:
            return self._parse_ods(file_path, filename, chunk_size_chars, overlap_chars)
        elif suffix == ".xls":
            return self._parse_xls(file_path, filename, chunk_size_chars, overlap_chars)
        elif suffix == ".csv":
            return self._parse_csv(file_path, filename, chunk_size_chars, overlap_chars)
        elif suffix in [".pptx", ".pptm", ".potx", ".potm", ".ppsx", ".pps"]:
            return self._parse_pptx(file_path, filename, chunk_size_chars, overlap_chars)
        elif suffix in [".odp", ".otp"]:
            return self._parse_odp(file_path, filename, chunk_size_chars, overlap_chars)
        elif suffix in [".ppt", ".pot"]:
            return self._parse_ppt(file_path, filename, chunk_size_chars, overlap_chars)
        elif suffix in [".txt", ".md"]:
            return self._parse_text(file_path, filename, chunk_size_chars, overlap_chars)
        else:
            raise ValueError(f"Неподдерживаемый формат файла: {suffix}")

    async def ingest_file(
        self,
        user_id: int,
        file_path: Path,
        original_filename: str,
        vector_db: VectorDBService,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        db_service: Optional[Any] = None,
    ) -> int:
        """Асинхронный процесс обработки и индексации документа в персональную базу пользователя.
        
        Поддерживает индивидуальные параметры chunk_size и chunk_overlap для пользователя.
        При наличии db_service автоматически регистрирует метаданные файла в SQLite БД.
        """
        logger.info(
            f"User {user_id}: Starting ingestion for '{original_filename}' ({file_path.stat().st_size} bytes). "
            f"Chunk size: {chunk_size or self.default_chunk_size}, overlap: {chunk_overlap or self.default_overlap}"
        )
        
        chunk_size_chars = chunk_size * 4 if chunk_size is not None else None
        overlap_chars = chunk_overlap * 4 if chunk_overlap is not None else None

        # Парсинг в фоновом потоке
        chunks = await asyncio.to_thread(
            self._sync_process_file,
            file_path,
            original_filename,
            chunk_size_chars,
            overlap_chars
        )
        if not chunks:
            logger.warning(f"No text extracted from '{original_filename}'.")
            return 0

        documents = [c.text for c in chunks]
        metadatas = [c.to_metadata() for c in chunks]
        ids = [f"{user_id}_{original_filename}_{c.page}_{c.chunk_index}" for c in chunks]

        # Добавление в персональную коллекцию пользователя в ChromaDB (векторизация)
        added = await vector_db.add_documents(
            user_id=user_id,
            documents=documents,
            metadatas=metadatas,
            ids=ids
        )

        # Сохранение в SQLite БД (если передан db_service)
        if db_service is not None and added > 0:
            try:
                file_size = file_path.stat().st_size if file_path.exists() else 0
                file_ext = file_path.suffix.lower()
                await db_service.record_document(
                    user_id=user_id,
                    filename=original_filename,
                    file_type=file_ext,
                    file_size=file_size,
                    chunks_count=added
                )
            except Exception as e:
                logger.warning(f"Failed to record document in SQLite DB: {e}")

        logger.info(f"User {user_id}: Ingestion completed for '{original_filename}'. Added {added} chunks.")
        return added
