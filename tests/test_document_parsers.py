import os
import shutil
import struct
import tempfile
import unittest
from pathlib import Path

import docx
import openpyxl
from pptx import Presentation
from pptx.util import Inches
import xlwt
from pypdf import PdfWriter

from bot.services.ingest import DocumentIngestionService


class TestDocumentParsers(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.ingest_service = DocumentIngestionService(default_chunk_size=100, default_overlap=20)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_docx_parser_with_tables(self):
        doc = docx.Document()
        doc.add_heading("Раздел 1: Введение", level=1)
        doc.add_paragraph("Это вступительная часть регламента SchoolX по безопасности.")
        
        # Добавляем таблицу в документ
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "Параметр"
        table.cell(0, 1).text = "Значение"
        table.cell(1, 0).text = "Таймаут"
        table.cell(1, 1).text = "30 секунд"

        doc.add_heading("Раздел 2: Инструкции", level=1)
        doc.add_paragraph("Сотрудники должны соблюдать правила внутреннего распорядка.")
        
        docx_path = Path(self.temp_dir) / "test.docx"
        doc.save(docx_path)

        chunks = self.ingest_service._parse_docx(docx_path, "test.docx")
        self.assertGreaterEqual(len(chunks), 2)
        headings = [c.page for c in chunks]
        self.assertTrue(any("Введение" in str(h) for h in headings))
        self.assertTrue(any("Инструкции" in str(h) for h in headings))

        all_text = " ".join(c.text for c in chunks)
        # Проверяем, что извлечены и параграфы, и строки таблицы!
        self.assertIn("SchoolX по безопасности", all_text)
        self.assertIn("Таймаут", all_text)
        self.assertIn("30 секунд", all_text)

    def test_pdf_parser(self):
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        pdf_path = Path(self.temp_dir) / "test.pdf"
        with open(pdf_path, "wb") as f:
            writer.write(f)

        chunks = self.ingest_service._parse_pdf(pdf_path, "test.pdf")
        self.assertEqual(len(chunks), 0)

    def test_xlsx_parser(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Сотрудники"
        ws.append(["ФИО", "Должность", "Оклад"])
        ws.append(["Иванов Иван", "Разработчик", 200000])
        ws.append(["Петрова Анна", "Тестировщик", 150000])

        ws2 = wb.create_sheet(title="Проекты")
        ws2.append(["Название", "Бюджет"])
        ws2.append(["SchoolX Bot", 500000])

        xlsx_path = Path(self.temp_dir) / "test.xlsx"
        wb.save(xlsx_path)

        chunks = self.ingest_service._parse_excel(xlsx_path, "test.xlsx")
        self.assertGreaterEqual(len(chunks), 2)

        pages = [c.page for c in chunks]
        self.assertTrue(any("Сотрудники" in str(p) for p in pages))
        self.assertTrue(any("Проекты" in str(p) for p in pages))

        all_text = " ".join(c.text for c in chunks)
        self.assertIn("Иванов Иван", all_text)
        self.assertIn("Разработчик", all_text)
        self.assertIn("SchoolX Bot", all_text)

    def test_xls_parser(self):
        wb = xlwt.Workbook(encoding="utf-8")
        ws = wb.add_sheet("Финансы")
        ws.write(0, 0, "Категория")
        ws.write(0, 1, "Сумма")
        ws.write(1, 0, "Серверы")
        ws.write(1, 1, 75000)

        xls_path = Path(self.temp_dir) / "test.xls"
        wb.save(xls_path)

        chunks = self.ingest_service._parse_xls(xls_path, "test.xls")
        self.assertGreaterEqual(len(chunks), 1)

        all_text = " ".join(c.text for c in chunks)
        self.assertIn("Серверы", all_text)
        self.assertIn("75000", all_text)

    def test_pptx_parser(self):
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[0])
        slide.shapes.title.text = "Презентация SchoolX"
        slide.placeholders[1].text = "Архитектура локальной RAG системы"

        slide2 = prs.slides.add_slide(prs.slide_layouts[5])
        slide2.shapes.title.text = "Слайд 2: Компоненты"
        table_shape = slide2.shapes.add_table(2, 2, Inches(1), Inches(1), Inches(4), Inches(2))
        table_shape.table.cell(0, 0).text = "Компонент"
        table_shape.table.cell(0, 1).text = "Назначение"
        table_shape.table.cell(1, 0).text = "ChromaDB"
        table_shape.table.cell(1, 1).text = "Векторная БД"

        slide2.notes_slide.notes_text_frame.text = "Важная заметка к слайду."

        pptx_path = Path(self.temp_dir) / "test.pptx"
        prs.save(pptx_path)

        chunks = self.ingest_service._parse_pptx(pptx_path, "test.pptx")
        self.assertGreaterEqual(len(chunks), 2)

        pages = [c.page for c in chunks]
        self.assertTrue(any("Слайд 1" in str(p) for p in pages))
        self.assertTrue(any("Слайд 2" in str(p) for p in pages))

        all_text = " ".join(c.text for c in chunks)
        self.assertIn("Презентация SchoolX", all_text)
        self.assertIn("ChromaDB", all_text)
        self.assertIn("Векторная БД", all_text)
        self.assertIn("Важная заметка к слайду", all_text)

    def test_rtf_parser(self):
        rtf_content = r"{\rtf1\ansi\ansicpg1251\deff0 {\fonttbl{\f0 Arial;}}\f0\fs24 Привет от SchoolX! \b Важный пункт регламента \b0}"
        rtf_path = Path(self.temp_dir) / "test.rtf"
        rtf_path.write_text(rtf_content, encoding="utf-8")

        chunks = self.ingest_service._parse_rtf(rtf_path, "test.rtf")
        self.assertGreaterEqual(len(chunks), 1)

        all_text = " ".join(c.text for c in chunks)
        self.assertIn("Привет от SchoolX", all_text)
        self.assertIn("Важный пункт регламента", all_text)

    def test_csv_parser(self):
        csv_content = "ID;Наименование;Количество\n1;Сервер GPU;2\n2;Коммутатор;5\n"
        csv_path = Path(self.temp_dir) / "test.csv"
        csv_path.write_text(csv_content, encoding="utf-8")

        chunks = self.ingest_service._parse_csv(csv_path, "test.csv")
        self.assertGreaterEqual(len(chunks), 1)

        all_text = " ".join(c.text for c in chunks)
        self.assertIn("Сервер GPU", all_text)
        self.assertIn("Коммутатор", all_text)

    def test_ppt_binary_stream_parser(self):
        # Тестируем парсер потока PPT с TextCharsAtom (0x03F8)
        text_utf16 = "Тестовый слайд PowerPoint 97".encode("utf-16-le")
        ppt_atom = struct.pack("<HHI", 0, 0x03F8, len(text_utf16)) + text_utf16
        ppt_path = Path(self.temp_dir) / "test.ppt"
        ppt_path.write_bytes(ppt_atom)

        chunks = self.ingest_service._parse_ppt(ppt_path, "test.ppt")
        self.assertGreaterEqual(len(chunks), 1)
        self.assertIn("Тестовый слайд PowerPoint 97", chunks[0].text)

    def test_doc_fallback_parser(self):
        # Тестируем извлечение текста из бинарного DOC
        text_utf16 = "Политика информационной безопасности компании".encode("utf-16-le")
        doc_bytes = b"\x00\x01\x02\x03" + text_utf16 + b"\x00\x00"
        doc_path = Path(self.temp_dir) / "test.doc"
        doc_path.write_bytes(doc_bytes)

        chunks = self.ingest_service._parse_doc(doc_path, "test.doc")
        self.assertGreaterEqual(len(chunks), 1)
        self.assertIn("Политика информационной безопасности компании", chunks[0].text)

    def test_odt_parser(self):
        import zipfile
        content_xml = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            b'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
            b'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">'
            b'<office:body><office:text>'
            b'<text:h text:outline-level="1">\xd0\x94\xd0\xbe\xd0\xb3\xd0\xbe\xd0\xb2\xd0\xbe\xd1\x80 \xd0\xbf\xd0\xbe\xd1\x81\xd1\x82\xd0\xb0\xd0\xb2\xd0\xba\xd0\xb8</text:h>'
            b'<text:p>\xd0\x9e\xd1\x81\xd0\xbd\xd0\xbe\xd0\xb2\xd0\xbd\xd1\x8b\xd0\xb5 \xd1\x83\xd1\x81\xd0\xbb\xd0\xbe\xd0\xb2\xd0\xb8\xd1\x8f \xd1\x81\xd0\xbe\xd0\xb3\xd0\xbb\xd0\xb0\xd1\x88\xd0\xb5\xd0\xbd\xd0\xb8\xd1\x8f.</text:p>'
            b'<table:table table:name="Table1">'
            b'<table:table-row>'
            b'<table:table-cell><text:p>\xd0\xa2\xd0\xbe\xd0\xb2\xd0\xb0\xd1\x80</text:p></table:table-cell>'
            b'<table:table-cell><text:p>\xd0\xa6\xd0\xb5\xd0\xbd\xd0\xb0</text:p></table:table-cell>'
            b'</table:table-row>'
            b'<table:table-row>'
            b'<table:table-cell><text:p>\xd0\xa1\xd0\xb5\xd1\x80\xd0\xb2\xd0\xb5\xd1\x80 GPU</text:p></table:table-cell>'
            b'<table:table-cell><text:p>500 000 \xd1\x80\xd1\x83\xd0\xb1</text:p></table:table-cell>'
            b'</table:table-row>'
            b'</table:table>'
            b'<text:list>'
            b'<text:list-item><text:p>\xd0\xa1\xd1\x80\xd0\xbe\xd0\xba \xd0\xbf\xd0\xbe\xd1\x81\xd1\x82\xd0\xb0\xd0\xb2\xd0\xba\xd0\xb8: 10 \xd0\xb4\xd0\xbd\xd0\xb5\xd0\xb9</text:p></text:list-item>'
            b'</text:list>'
            b'</office:text></office:body></office:document-content>'
        )
        odt_path = Path(self.temp_dir) / "test.odt"
        with zipfile.ZipFile(odt_path, "w") as zf:
            zf.writestr("mimetype", "application/vnd.oasis.opendocument.text")
            zf.writestr("content.xml", content_xml)

        chunks = self.ingest_service._parse_odt(odt_path, "test.odt")
        self.assertGreaterEqual(len(chunks), 1)
        all_text = " ".join(c.text for c in chunks)
        self.assertIn("Договор поставки", all_text)
        self.assertIn("Основные условия", all_text)
        self.assertIn("Сервер GPU", all_text)
        self.assertIn("500 000 руб", all_text)
        self.assertIn("Срок поставки: 10 дней", all_text)

    def test_ods_parser(self):
        import zipfile
        content_xml = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            b'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
            b'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
            b'<office:body><office:spreadsheet>'
            b'<table:table table:name="\xd0\x91\xd1\x8e\xd0\xb4\xd0\xb6\xd0\xb5\xd1\x82">'
            b'<table:table-row>'
            b'<table:table-cell><text:p>\xd0\xa1\xd1\x82\xd0\xb0\xd1\x82\xd1\x8c\xd1\x8f</text:p></table:table-cell>'
            b'<table:table-cell><text:p>\xd0\xa1\xd1\x83\xd0\xbc\xd0\xbc\xd0\xb0</text:p></table:table-cell>'
            b'</table:table-row>'
            b'<table:table-row>'
            b'<table:table-cell><text:p>\xd0\x9c\xd0\xbe\xd0\xb4\xd0\xb5\xd0\xbb\xd0\xb8 AI</text:p></table:table-cell>'
            b'<table:table-cell><text:p>120 000</text:p></table:table-cell>'
            b'</table:table-row>'
            b'</table:table>'
            b'</office:spreadsheet></office:body></office:document-content>'
        )
        ods_path = Path(self.temp_dir) / "test.ods"
        with zipfile.ZipFile(ods_path, "w") as zf:
            zf.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
            zf.writestr("content.xml", content_xml)

        chunks = self.ingest_service._parse_ods(ods_path, "test.ods")
        self.assertGreaterEqual(len(chunks), 1)
        all_text = " ".join(c.text for c in chunks)
        self.assertIn("Бюджет", chunks[0].page)
        self.assertIn("Модели AI", all_text)
        self.assertIn("120 000", all_text)

    def test_mislabeled_odt_as_docx(self):
        # Пользователь переименовал .odt в .docx
        import zipfile
        content_xml = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            b'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
            b'<office:body><office:text>'
            b'<text:p>\xd0\xa2\xd0\xb5\xd0\xba\xd1\x81\xd1\x82 \xd0\xb8\xd0\xb7 ODT \xd1\x84\xd0\xb0\xd0\xb9\xd0\xbb\xd0\xb0 \xd0\xbf\xd0\xbe\xd0\xb4 \xd0\xb8\xd0\xbc\xd0\xb5\xd0\xbd\xd0\xb5\xd0\xbc docx</text:p>'
            b'</office:text></office:body></office:document-content>'
        )
        fake_docx_path = Path(self.temp_dir) / "\xd0\xb4\xd0\xbe\xd0\xb3\xd0\xbe\xd0\xb2\xd0\xbe\xd1\x80.docx"
        with zipfile.ZipFile(fake_docx_path, "w") as zf:
            zf.writestr("mimetype", "application/vnd.oasis.opendocument.text")
            zf.writestr("content.xml", content_xml)

        chunks = self.ingest_service._parse_docx(fake_docx_path, "договор.docx")
        self.assertGreaterEqual(len(chunks), 1)
        self.assertIn("Текст из ODT файла под именем docx", chunks[0].text)

    def test_damaged_docx_friendly_error(self):
        # Поврежденный файл с мусорными байтами
        bad_path = Path(self.temp_dir) / "corrupt.docx"
        bad_path.write_bytes(b"PK\x03\x04not_a_valid_zip_archive_data_corrupted")

        with self.assertRaises(ValueError) as ctx:
            self.ingest_service._parse_docx(bad_path, "corrupt.docx")
        self.assertIn("поврежден", str(ctx.exception).lower())

    def test_sync_process_file_dispatch(self):
        # Проверка диспетчеризации всех расширений через _sync_process_file
        txt_path = Path(self.temp_dir) / "sample.txt"
        txt_path.write_text("Обычный текстовый файл для проверки.", encoding="utf-8")
        chunks = self.ingest_service._sync_process_file(txt_path, "sample.txt")
        self.assertEqual(len(chunks), 1)

        with self.assertRaises(ValueError):
            fake_path = Path(self.temp_dir) / "unknown.xyz"
            self.ingest_service._sync_process_file(fake_path, "unknown.xyz")


if __name__ == "__main__":
    unittest.main()
