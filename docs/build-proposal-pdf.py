#!/usr/bin/env python3
"""Render the Amazon-engine proposal and add its fillable acceptance fields.

Chrome is used for the document body because it is the source of the approved
layout.  pypdf then adds transparent AcroForm text widgets over the existing
signature and date lines on page four.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    BooleanObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
    TextStringObject,
)


DOCS_DIR = Path(__file__).resolve().parent
HTML_PATH = DOCS_DIR / "proposal-amazon-engine.html"
OUTPUT_PATH = DOCS_DIR / "proposal-amazon-engine.pdf"
ACCEPTANCE_PAGE_INDEX = 3

# Coordinates are PDF points, measured from the Chrome-rendered signature
# lines.  PDF coordinates start at the lower-left corner of a letter page.
FIELDS = (
    ("ChipSignature", (27.75, 210.0, 398.0, 230.0)),
    ("ChipDate", (428.44, 210.0, 584.25, 230.0)),
    ("DevinSignature", (27.75, 146.25, 398.0, 166.25)),
    ("DevinDate", (428.44, 146.25, 584.25, 166.25)),
)


def chrome_binary() -> str:
    """Return the installed Chrome executable used for the original PDF."""
    for candidate in ("google-chrome", "chromium", "chromium-browser"):
        if path := shutil.which(candidate):
            return path
    raise RuntimeError("Chrome or Chromium is required to render the proposal PDF")


def render_html(destination: Path) -> None:
    subprocess.run(
        [
            chrome_binary(),
            "--headless",
            "--no-sandbox",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--print-to-pdf={destination}",
            HTML_PATH.as_uri(),
        ],
        check=True,
    )


def font_resources() -> DictionaryObject:
    return DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject("/Helv"): DictionaryObject(
                        {
                            NameObject("/Type"): NameObject("/Font"),
                            NameObject("/Subtype"): NameObject("/Type1"),
                            NameObject("/BaseFont"): NameObject("/Helvetica"),
                            NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
                        }
                    )
                }
            )
        }
    )


def text_field(name: str, rect: tuple[float, float, float, float], page_ref) -> DictionaryObject:
    return DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/FT"): NameObject("/Tx"),
            NameObject("/T"): TextStringObject(name),
            NameObject("/Rect"): ArrayObject([FloatObject(value) for value in rect]),
            NameObject("/P"): page_ref,
            # Print the entered value, but keep the widget's own border and
            # background invisible so the original signature lines remain.
            NameObject("/F"): NumberObject(4),
            NameObject("/BS"): DictionaryObject({NameObject("/W"): NumberObject(0)}),
            NameObject("/DA"): TextStringObject("/Helv 12 Tf 0 g"),
        }
    )


def add_acceptance_fields(source: Path, destination: Path) -> None:
    reader = PdfReader(source)
    if len(reader.pages) <= ACCEPTANCE_PAGE_INDEX:
        raise RuntimeError("The rendered proposal no longer has an acceptance page at page 4")

    writer = PdfWriter(clone_from=reader)
    page = writer.pages[ACCEPTANCE_PAGE_INDEX]
    annotations = page.get(NameObject("/Annots"), ArrayObject())
    if not isinstance(annotations, ArrayObject):
        annotations = ArrayObject(annotations)

    field_references = ArrayObject()
    for name, rect in FIELDS:
        field_reference = writer._add_object(text_field(name, rect, page.indirect_reference))
        annotations.append(field_reference)
        field_references.append(field_reference)
    page[NameObject("/Annots")] = annotations

    writer._root_object[NameObject("/AcroForm")] = DictionaryObject(
        {
            NameObject("/Fields"): field_references,
            NameObject("/DR"): font_resources(),
            NameObject("/DA"): TextStringObject("/Helv 12 Tf 0 g"),
            NameObject("/NeedAppearances"): BooleanObject(True),
        }
    )
    with destination.open("wb") as output:
        writer.write(output)


def field_inventory(pdf_path: Path) -> list[str]:
    reader = PdfReader(pdf_path)
    fields = reader.get_fields() or {}
    inventory = []
    for name, expected_rect in FIELDS:
        field = fields.get(name)
        if field is None:
            raise RuntimeError(f"Missing AcroForm field: {name}")
        if field.get("/FT") != "/Tx":
            raise RuntimeError(f"{name} has type {field.get('/FT')}, expected /Tx")
        inventory.append(f"{name}: /Tx, rect={expected_rect}, page=4")
    if set(fields) != {name for name, _ in FIELDS}:
        raise RuntimeError(f"Unexpected AcroForm fields: {', '.join(sorted(fields))}")
    return inventory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true", help="validate the existing PDF")
    args = parser.parse_args()

    if args.verify_only:
        inventory = field_inventory(OUTPUT_PATH)
    else:
        with tempfile.TemporaryDirectory() as temporary_directory:
            rendered_pdf = Path(temporary_directory) / "proposal-body.pdf"
            render_html(rendered_pdf)
            add_acceptance_fields(rendered_pdf, OUTPUT_PATH)
        inventory = field_inventory(OUTPUT_PATH)

    print("AcroForm field inventory:")
    for field in inventory:
        print(f"- {field}")


if __name__ == "__main__":
    main()
