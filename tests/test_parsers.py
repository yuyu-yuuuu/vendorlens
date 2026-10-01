from io import BytesIO

import pytest
from openpyxl import Workbook

from vendorlens.parsers import parse_document


def test_markdown_preserves_heading_and_lines():
    chunks = parse_document("proposal.md", "# 交付方案\n\n周期为 45 天。\n".encode())
    assert chunks[0].text == "周期为 45 天。"
    assert chunks[0].chunk_type == "parent"
    assert chunks[1].chunk_type == "child"
    assert chunks[1].parent_position == 0
    assert chunks[0].location.heading_path == ("交付方案",)
    assert chunks[0].location.line_start == 3


def test_xlsx_preserves_sheet_and_cell_range():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "报价"
    sheet.append(["供应商", "总价"])
    sheet.append(["甲公司", 880000])
    output = BytesIO()
    workbook.save(output)

    chunks = parse_document("quote.xlsx", output.getvalue())

    assert chunks[1].text == "甲公司 | 880000"
    assert chunks[1].chunk_type == "parent"
    assert chunks[1].parent_position is None
    assert chunks[1].location.sheet == "报价"
    assert chunks[1].location.cell_range == "A2:B2"
    workbook.close()


def test_unsupported_type_fails_explicitly():
    with pytest.raises(ValueError, match="unsupported document type"):
        parse_document("scan.png", b"not an image parser")


def test_long_parent_is_split_into_bounded_children():
    text = "。".join(["这是一个包含交付范围和验收要求的句子"] * 30) + "。"

    chunks = parse_document("proposal.txt", text.encode())

    assert chunks[0].chunk_type == "parent"
    children = [chunk for chunk in chunks if chunk.chunk_type == "child"]
    assert len(children) > 1
    assert all(len(chunk.text) <= 320 for chunk in children)
    assert all(chunk.parent_position == 0 for chunk in children)
