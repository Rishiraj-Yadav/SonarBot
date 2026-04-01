"""Minimal XLSX read/write helpers for Phase 5 app skills.

This implementation intentionally focuses on plain worksheet values.
It preserves only the first worksheet and rewrites value-only content.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET


XML_NS = "http://www.w3.org/XML/1998/namespace"
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

ET.register_namespace("", MAIN_NS)
ET.register_namespace("r", REL_NS)


@dataclass(slots=True)
class WorkbookData:
    sheet_name: str
    rows: list[list[str]]


def _column_index_from_ref(cell_ref: str) -> int:
    letters = "".join(char for char in cell_ref if char.isalpha()).upper()
    index = 0
    for char in letters:
        index = (index * 26) + (ord(char) - 64)
    return max(0, index - 1)


def _column_letter(index: int) -> str:
    if index < 0:
        raise ValueError("Column index cannot be negative.")
    index += 1
    parts: list[str] = []
    while index:
        index, remainder = divmod(index - 1, 26)
        parts.append(chr(65 + remainder))
    return "".join(reversed(parts))


def _coerce_cell_text(value: object) -> tuple[str, str | None]:
    if isinstance(value, bool):
        return ("1" if value else "0", "b")
    text = str(value)
    if re.fullmatch(r"-?\d+", text):
        return text, None
    if re.fullmatch(r"-?\d+\.\d+", text):
        return text, None
    return text, "inlineStr"


def load_workbook_bytes(data: bytes) -> WorkbookData:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        shared_strings = _load_shared_strings(archive)

        sheet_node = workbook_root.find(f".//{{{MAIN_NS}}}sheet")
        if sheet_node is None:
            return WorkbookData(sheet_name="Sheet1", rows=[])
        sheet_name = sheet_node.attrib.get("name", "Sheet1")
        sheet_rid = sheet_node.attrib.get(f"{{{REL_NS}}}id", "")
        worksheet_target = "worksheets/sheet1.xml"
        for rel in relationships_root.findall(f".//{{{PACKAGE_REL_NS}}}Relationship"):
            if rel.attrib.get("Id") == sheet_rid:
                worksheet_target = rel.attrib.get("Target", worksheet_target)
                break
        worksheet_path = f"xl/{worksheet_target.lstrip('/')}"
        worksheet_root = ET.fromstring(archive.read(worksheet_path))
        rows: list[list[str]] = []
        for row_node in worksheet_root.findall(f".//{{{MAIN_NS}}}sheetData/{{{MAIN_NS}}}row"):
            values: dict[int, str] = {}
            max_index = -1
            for cell in row_node.findall(f"{{{MAIN_NS}}}c"):
                ref = cell.attrib.get("r", "")
                col_index = _column_index_from_ref(ref)
                max_index = max(max_index, col_index)
                cell_type = cell.attrib.get("t", "")
                text = ""
                if cell_type == "inlineStr":
                    text = "".join(node.text or "" for node in cell.findall(f".//{{{MAIN_NS}}}t"))
                else:
                    value_node = cell.find(f"{{{MAIN_NS}}}v")
                    if value_node is not None and value_node.text is not None:
                        if cell_type == "s":
                            try:
                                text = shared_strings[int(value_node.text)]
                            except Exception:
                                text = value_node.text
                        elif cell_type == "b":
                            text = "TRUE" if value_node.text == "1" else "FALSE"
                        else:
                            text = value_node.text
                values[col_index] = text
            if max_index < 0:
                rows.append([])
                continue
            row_values = [""] * (max_index + 1)
            for index, text in values.items():
                row_values[index] = text
            rows.append(row_values)
        return WorkbookData(sheet_name=sheet_name, rows=rows)


def build_workbook_bytes(*, sheet_name: str, rows: list[list[object]]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types_xml())
        archive.writestr("_rels/.rels", _root_relationships_xml())
        archive.writestr("docProps/app.xml", _app_props_xml(sheet_name))
        archive.writestr("docProps/core.xml", _core_props_xml())
        archive.writestr("xl/workbook.xml", _workbook_xml(sheet_name))
        archive.writestr("xl/_rels/workbook.xml.rels", _workbook_relationships_xml())
        archive.writestr("xl/styles.xml", _styles_xml())
        archive.writestr("xl/worksheets/sheet1.xml", _worksheet_xml(rows))
    return buffer.getvalue()


def append_row_bytes(data: bytes, values: list[object]) -> bytes:
    workbook = load_workbook_bytes(data)
    workbook.rows.append([str(item) for item in values])
    return build_workbook_bytes(sheet_name=workbook.sheet_name, rows=workbook.rows)


def preview_workbook_bytes(data: bytes, *, limit: int = 8) -> dict[str, object]:
    workbook = load_workbook_bytes(data)
    rows = workbook.rows[: max(1, limit)]
    return {
        "sheet_name": workbook.sheet_name,
        "rows": rows,
        "row_count": len(workbook.rows),
        "column_count": max((len(row) for row in workbook.rows), default=0),
    }


def _load_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    values: list[str] = []
    for node in root.findall(f".//{{{MAIN_NS}}}si"):
        values.append("".join(item.text or "" for item in node.findall(f".//{{{MAIN_NS}}}t")))
    return values


def _content_types_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""


def _root_relationships_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""


def _app_props_xml(sheet_name: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>SonarBot</Application>
  <Sheets>1</Sheets>
  <HeadingPairs>
    <vt:vector size="2" baseType="variant">
      <vt:variant><vt:lpstr>Worksheets</vt:lpstr></vt:variant>
      <vt:variant><vt:i4>1</vt:i4></vt:variant>
    </vt:vector>
  </HeadingPairs>
  <TitlesOfParts>
    <vt:vector size="1" baseType="lpstr">
      <vt:lpstr>{sheet_name}</vt:lpstr>
    </vt:vector>
  </TitlesOfParts>
</Properties>"""


def _core_props_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:dcmitype="http://purl.org/dc/dcmitype/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:creator>SonarBot</dc:creator>
  <cp:lastModifiedBy>SonarBot</cp:lastModifiedBy>
</cp:coreProperties>"""


def _workbook_xml(sheet_name: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="{MAIN_NS}" xmlns:r="{REL_NS}">
  <sheets>
    <sheet name="{sheet_name}" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>"""


def _workbook_relationships_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""


def _styles_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
  <fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def _worksheet_xml(rows: list[list[object]]) -> str:
    root = ET.Element(f"{{{MAIN_NS}}}worksheet")
    sheet_data = ET.SubElement(root, f"{{{MAIN_NS}}}sheetData")
    for row_index, row_values in enumerate(rows, start=1):
        row_node = ET.SubElement(sheet_data, f"{{{MAIN_NS}}}row", {"r": str(row_index)})
        for col_index, raw_value in enumerate(row_values):
            value_text, cell_type = _coerce_cell_text(raw_value)
            if value_text == "":
                continue
            cell_ref = f"{_column_letter(col_index)}{row_index}"
            attributes = {"r": cell_ref}
            if cell_type:
                attributes["t"] = cell_type
            cell_node = ET.SubElement(row_node, f"{{{MAIN_NS}}}c", attributes)
            if cell_type == "inlineStr":
                inline = ET.SubElement(cell_node, f"{{{MAIN_NS}}}is")
                text_node = ET.SubElement(inline, f"{{{MAIN_NS}}}t")
                if value_text.strip() != value_text:
                    text_node.set(f"{{{XML_NS}}}space", "preserve")
                text_node.text = value_text
            else:
                value_node = ET.SubElement(cell_node, f"{{{MAIN_NS}}}v")
                value_node.text = value_text
    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
