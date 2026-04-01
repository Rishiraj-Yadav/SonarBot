from __future__ import annotations

from assistant.app_skills.simple_xlsx import build_workbook_bytes, load_workbook_bytes, preview_workbook_bytes


def test_simple_xlsx_round_trip_preserves_rows() -> None:
    data = build_workbook_bytes(
        sheet_name="Marks",
        rows=[
            ["Name", "Score"],
            ["Ritesh", 95],
            ["Asha", 88],
        ],
    )

    workbook = load_workbook_bytes(data)

    assert workbook.sheet_name == "Marks"
    assert workbook.rows == [["Name", "Score"], ["Ritesh", "95"], ["Asha", "88"]]


def test_simple_xlsx_preview_returns_sheet_metadata() -> None:
    data = build_workbook_bytes(sheet_name="Sheet1", rows=[["Task", "Status"], ["Homework", "Done"]])

    preview = preview_workbook_bytes(data, limit=1)

    assert preview["sheet_name"] == "Sheet1"
    assert preview["row_count"] == 2
    assert preview["rows"] == [["Task", "Status"]]
