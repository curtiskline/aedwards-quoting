"""PDF quote generator matching Allan Edwards format."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import sys

from reportlab.graphics.barcode import code128
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .pricing import Quote

# Colors matching the sample quote
YELLOW_HEADER = colors.Color(1, 1, 0)  # #FFFF00
BLACK = colors.black
WHITE = colors.white
GRAY = colors.Color(0.5, 0.5, 0.5)

def _resolve_default_logo_path() -> Path:
    """Resolve bundled logo path for source and frozen runtimes."""
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path.cwd()))
        bundled_logo = bundle_root / "allenedwards" / "assets" / "logo.jpg"
        if bundled_logo.exists():
            return bundled_logo
        return bundle_root / "assets" / "logo.jpg"

    package_logo = Path(__file__).parent / "assets" / "logo.jpg"
    if package_logo.exists():
        return package_logo

    # Backward-compatible fallback for old source layouts.
    return Path(__file__).parent.parent.parent.parent.parent / "assets" / "logo.jpg"


DEFAULT_LOGO_PATH = _resolve_default_logo_path()


def format_currency(amount: Decimal | None) -> str:
    """Format a decimal as currency."""
    if amount is None:
        return ""
    return f"${amount:,.2f}"


def format_date(d: date) -> str:
    """Format date as M/D/YYYY without leading zeros."""
    return f"{d.month}/{d.day}/{d.year}"


class QuotePDFBuilder:
    """Builder class for generating quote PDFs."""

    def __init__(
        self,
        quote: Quote,
        output_path: Path,
        quote_date: date | None = None,
        expires_date: date | None = None,
        logo_path: Path | None = None,
    ):
        self.quote = quote
        self.output_path = output_path
        self.quote_date = quote_date or date.today()
        self.expires_date = expires_date or (self.quote_date + timedelta(days=7))
        self.logo_path = logo_path or DEFAULT_LOGO_PATH
        self.page_width = letter[0]
        self.page_height = letter[1]
        self.margin = 0.5 * inch
        self.content_width = self.page_width - 2 * self.margin

        self._init_styles()

    def _init_styles(self):
        """Initialize paragraph styles."""
        self.styles = {
            "normal": ParagraphStyle(
                "Normal",
                fontName="Helvetica",
                fontSize=10,
                leading=12,
            ),
            "normal_small": ParagraphStyle(
                "NormalSmall",
                fontName="Helvetica",
                fontSize=9,
                leading=11,
            ),
            "bold": ParagraphStyle(
                "Bold",
                fontName="Helvetica-Bold",
                fontSize=10,
                leading=12,
            ),
            "bold_small": ParagraphStyle(
                "BoldSmall",
                fontName="Helvetica-Bold",
                fontSize=9,
                leading=11,
            ),
            "header": ParagraphStyle(
                "Header",
                fontName="Helvetica-Bold",
                fontSize=9,
                leading=11,
            ),
            "title": ParagraphStyle(
                "Title",
                fontName="Helvetica-Bold",
                fontSize=24,
                leading=28,
            ),
            "quote_number": ParagraphStyle(
                "QuoteNumber",
                fontName="Helvetica-Bold",
                fontSize=16,
                leading=20,
            ),
            "footer": ParagraphStyle(
                "Footer",
                fontName="Helvetica",
                fontSize=8,
                leading=10,
                textColor=GRAY,
            ),
            "grid_label": ParagraphStyle(
                "GridLabel",
                fontName="Helvetica-Bold",
                fontSize=9,
                leading=11,
            ),
            "grid_value": ParagraphStyle(
                "GridValue",
                fontName="Helvetica",
                fontSize=9,
                leading=11,
            ),
        }

    def _build_header(self) -> list:
        """Build the header section with logo, company info, and quote info."""
        elements = []

        # Create header table: [Logo + Company Info] | [Quote Info]
        # Left side: Logo and company address
        left_content = []

        # Add logo if available
        if self.logo_path.exists():
            # Scale logo to fit (original is 815x114, we want ~2 inches wide)
            logo = Image(str(self.logo_path), width=2 * inch, height=0.28 * inch)
            left_content.append([logo])

        # Company address
        left_content.append([Paragraph("Allan Edwards, Inc.", self.styles["bold"])])
        left_content.append([Paragraph("6468 N Yale Ave", self.styles["normal"])])
        left_content.append([Paragraph("Tulsa OK 74117", self.styles["normal"])])
        left_content.append([Paragraph("United States", self.styles["normal"])])
        left_content.append([Paragraph("(918) 583-7184", self.styles["normal"])])

        left_table = Table(left_content, colWidths=[3.5 * inch])
        left_table.setStyle(TableStyle([
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ]))

        # Right side: Quote title and info
        right_content = [
            [Paragraph("Quote", self.styles["title"])],
            [Paragraph(self.quote.quote_number, self.styles["quote_number"])],
            [Paragraph(format_date(self.quote_date), self.styles["normal"])],
        ]
        right_table = Table(right_content, colWidths=[3 * inch])
        right_table.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))

        # Combine left and right
        header_table = Table(
            [[left_table, right_table]],
            colWidths=[4.5 * inch, 3 * inch]
        )
        header_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        elements.append(header_table)
        elements.append(Spacer(1, 18))

        return elements

    def _build_bill_ship_to(self) -> list:
        """Build the Bill To / Ship To section."""
        elements = []

        # Bill To content
        bill_to_lines = [
            [Paragraph("<b>Bill To</b>", self.styles["bold"])],
        ]
        if self.quote.contact_email:
            bill_to_lines.append([Paragraph(self.quote.contact_email, self.styles["normal"])])
        if self.quote.customer_name:
            bill_to_lines.append([Paragraph(self.quote.customer_name, self.styles["normal"])])

        bill_to_table = Table(bill_to_lines, colWidths=[3.5 * inch])
        bill_to_table.setStyle(TableStyle([
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ]))

        # Ship To content
        ship_to_lines = [
            [Paragraph("<b>Ship To</b>", self.styles["bold"])],
        ]
        if self.quote.contact_name and self.quote.contact_phone:
            ship_to_lines.append([Paragraph(
                f"{self.quote.contact_name} {self.quote.contact_phone}",
                self.styles["normal"]
            )])
        elif self.quote.contact_name:
            ship_to_lines.append([Paragraph(self.quote.contact_name, self.styles["normal"])])

        if self.quote.ship_to:
            if self.quote.ship_to.get("company"):
                ship_to_lines.append([Paragraph(self.quote.ship_to["company"], self.styles["normal"])])
            if self.quote.ship_to.get("attention"):
                ship_to_lines.append([Paragraph(self.quote.ship_to["attention"], self.styles["normal"])])
            if self.quote.ship_to.get("street"):
                ship_to_lines.append([Paragraph(self.quote.ship_to["street"], self.styles["normal"])])

            city_state_zip_parts = []
            if self.quote.ship_to.get("city"):
                city_state_zip_parts.append(self.quote.ship_to["city"])
            if self.quote.ship_to.get("state"):
                city_state_zip_parts.append(self.quote.ship_to["state"])
            if self.quote.ship_to.get("postal_code"):
                city_state_zip_parts.append(self.quote.ship_to["postal_code"])
            if city_state_zip_parts:
                ship_to_lines.append([Paragraph(", ".join(city_state_zip_parts), self.styles["normal"])])
            if self.quote.ship_to.get("country"):
                ship_to_lines.append([Paragraph(self.quote.ship_to["country"], self.styles["normal"])])

        ship_to_table = Table(ship_to_lines, colWidths=[3.5 * inch])
        ship_to_table.setStyle(TableStyle([
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ]))

        # Combine Bill To and Ship To
        address_table = Table(
            [[bill_to_table, ship_to_table]],
            colWidths=[3.75 * inch, 3.75 * inch]
        )
        address_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        elements.append(address_table)
        elements.append(Spacer(1, 12))

        return elements

    def _build_details_grid(self) -> list:
        """Build the details grid (Template B style)."""
        elements = []

        # Row 1: PO#, Shipping Method, Expires
        # Row 2: Payment Terms, Shipping Terms, Sales Rep
        # Row 3: Requested By, Req By Email, Req By Phone
        grid_data = [
            # Labels row 1
            [
                Paragraph("<b>PO #</b>", self.styles["grid_label"]),
                Paragraph("<b>Shipping Method</b>", self.styles["grid_label"]),
                Paragraph("<b>Expires</b>", self.styles["grid_label"]),
            ],
            # Values row 1
            [
                Paragraph(self.quote.po_number or "", self.styles["grid_value"]),
                Paragraph(self.quote.shipping_method or "", self.styles["grid_value"]),
                Paragraph(format_date(self.expires_date), self.styles["grid_value"]),
            ],
            # Labels row 2
            [
                Paragraph("<b>Payment Terms</b>", self.styles["grid_label"]),
                Paragraph("<b>Shipping Terms</b>", self.styles["grid_label"]),
                Paragraph("<b>Sales Rep</b>", self.styles["grid_label"]),
            ],
            # Values row 2
            [
                Paragraph(self.quote.payment_terms or "", self.styles["grid_value"]),
                Paragraph(self.quote.shipping_terms or "", self.styles["grid_value"]),
                Paragraph(self.quote.sales_rep or "", self.styles["grid_value"]),
            ],
            # Labels row 3
            [
                Paragraph("<b>Requested By</b>", self.styles["grid_label"]),
                Paragraph("<b>Req By Email</b>", self.styles["grid_label"]),
                Paragraph("<b>Req By Phone</b>", self.styles["grid_label"]),
            ],
            # Values row 3
            [
                Paragraph(self._format_requested_by(), self.styles["grid_value"]),
                Paragraph(self.quote.requested_by_email or "", self.styles["grid_value"]),
                Paragraph(self.quote.requested_by_phone or "", self.styles["grid_value"]),
            ],
        ]

        col_width = self.content_width / 3
        grid_table = Table(grid_data, colWidths=[col_width, col_width, col_width])
        grid_table.setStyle(TableStyle([
            # Borders
            ("BOX", (0, 0), (-1, -1), 0.5, BLACK),
            ("LINEBELOW", (0, 1), (-1, 1), 0.5, BLACK),
            ("LINEBELOW", (0, 3), (-1, 3), 0.5, BLACK),
            ("LINEBEFORE", (1, 0), (1, -1), 0.5, BLACK),
            ("LINEBEFORE", (2, 0), (2, -1), 0.5, BLACK),
            # Padding
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            # Alignment
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        elements.append(grid_table)
        elements.append(Spacer(1, 18))

        return elements

    def _format_requested_by(self) -> str:
        """Format the Requested By field."""
        parts = []
        if self.quote.customer_name:
            parts.append(self.quote.customer_name)
        if self.quote.requested_by_name:
            parts.append(f": {self.quote.requested_by_name}")
        elif self.quote.contact_name:
            parts.append(f": {self.quote.contact_name}")
        return "".join(parts)

    def _build_line_items_table(self) -> list:
        """Build the line items table."""
        elements = []

        # Header row
        table_data = [
            [
                Paragraph("<b>Item</b>", self.styles["header"]),
                Paragraph("<b>Description</b>", self.styles["header"]),
                Paragraph("<b>Quantity</b>", self.styles["header"]),
                Paragraph("<b>Rate</b>", self.styles["header"]),
                Paragraph("<b>Amount</b>", self.styles["header"]),
            ]
        ]

        # Line items
        for item in self.quote.line_items:
            table_data.append([
                Paragraph(item.part_number, self.styles["normal_small"]),
                Paragraph(item.description, self.styles["normal_small"]),
                Paragraph(str(item.quantity), self.styles["normal_small"]),
                Paragraph(format_currency(item.unit_price), self.styles["normal_small"]),
                Paragraph(format_currency(item.total), self.styles["normal_small"]),
            ])

        # Add notes as description-only rows if present
        if self.quote.notes:
            for note in self.quote.notes.split("\n"):
                if note.strip():
                    table_data.append([
                        Paragraph("Description", self.styles["normal_small"]),
                        Paragraph(note.strip(), self.styles["normal_small"]),
                        "",
                        "",
                        "",
                    ])

        # Column widths: Item 15%, Description 45%, Quantity 10%, Rate 15%, Amount 15%
        col_widths = [
            self.content_width * 0.15,
            self.content_width * 0.40,
            self.content_width * 0.12,
            self.content_width * 0.15,
            self.content_width * 0.18,
        ]

        items_table = Table(table_data, colWidths=col_widths)
        items_table.setStyle(TableStyle([
            # Header row styling - yellow background
            ("BACKGROUND", (0, 0), (-1, 0), YELLOW_HEADER),
            ("TEXTCOLOR", (0, 0), (-1, 0), BLACK),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            # Data rows
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            # Alignment
            ("ALIGN", (0, 0), (-1, 0), "LEFT"),  # Header left aligned
            ("ALIGN", (2, 1), (2, -1), "CENTER"),  # Quantity centered
            ("ALIGN", (3, 1), (-1, -1), "RIGHT"),  # Prices right aligned
            # Grid
            ("BOX", (0, 0), (-1, -1), 0.5, BLACK),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, BLACK),  # Header bottom border
            # Padding
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            # Valign
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        elements.append(items_table)

        return elements

    def _build_totals(self) -> list:
        """Build the totals section."""
        elements = []
        elements.append(Spacer(1, 6))

        # Totals table (right-aligned)
        totals_data = [
            [
                "",
                Paragraph("<b>Subtotal</b>", self.styles["bold_small"]),
                Paragraph(format_currency(self.quote.subtotal), self.styles["normal_small"]),
            ],
            [
                "",
                Paragraph("<b>Shipping/Handling</b>", self.styles["bold_small"]),
                Paragraph(format_currency(self.quote.shipping_amount or Decimal("0")), self.styles["normal_small"]),
            ],
            [
                "",
                Paragraph("<b>Sales Tax (%)</b>", self.styles["bold_small"]),
                Paragraph(format_currency(self.quote.tax_amount), self.styles["normal_small"]),
            ],
            [
                "",
                Paragraph("<b>Total</b>", self.styles["bold"]),
                Paragraph(format_currency(self.quote.total), self.styles["bold"]),
            ],
        ]

        totals_table = Table(
            totals_data,
            colWidths=[self.content_width * 0.55, self.content_width * 0.25, self.content_width * 0.20]
        )
        totals_table.setStyle(TableStyle([
            # Total row yellow background
            ("BACKGROUND", (1, -1), (-1, -1), YELLOW_HEADER),
            # Alignment
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("ALIGN", (2, 0), (2, -1), "RIGHT"),
            # Borders
            ("LINEABOVE", (1, -1), (-1, -1), 0.5, BLACK),
            ("LINEBELOW", (1, -1), (-1, -1), 0.5, BLACK),
            ("BOX", (1, -1), (-1, -1), 0.5, BLACK),
            # Padding
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]))
        elements.append(totals_table)

        return elements

    def _build_footer(self, canvas, doc):
        """Draw the footer on each page."""
        canvas.saveState()

        # Barcode on left
        barcode = code128.Code128(
            self.quote.quote_number,
            barWidth=0.8,
            barHeight=0.4 * inch,
        )
        barcode.drawOn(canvas, self.margin, 0.4 * inch)

        # Quote number text below barcode
        canvas.setFont("Helvetica", 8)
        canvas.drawString(self.margin, 0.25 * inch, self.quote.quote_number)

        # Page number in center
        page_text = f"{doc.page} of {doc.page}"  # Will be updated by later pass
        canvas.drawCentredString(self.page_width / 2, 0.35 * inch, page_text)

        # Timestamp on right
        timestamp = datetime.now().strftime("%m/%d/%Y %I:%M %p")
        canvas.drawRightString(self.page_width - self.margin, 0.35 * inch, timestamp)

        canvas.restoreState()

    def build(self) -> Path:
        """Build the PDF and return the output path."""
        doc = SimpleDocTemplate(
            str(self.output_path),
            pagesize=letter,
            leftMargin=self.margin,
            rightMargin=self.margin,
            topMargin=self.margin,
            bottomMargin=0.75 * inch,  # Extra space for footer
        )

        elements = []
        elements.extend(self._build_header())
        elements.extend(self._build_bill_ship_to())
        elements.extend(self._build_details_grid())
        elements.extend(self._build_line_items_table())
        elements.extend(self._build_totals())

        # Build with footer
        doc.build(elements, onFirstPage=self._build_footer, onLaterPages=self._build_footer)

        return self.output_path


def generate_quote_pdf(
    quote: Quote,
    output_path: Path,
    quote_date: date | None = None,
    expires_date: date | None = None,
    logo_path: Path | None = None,
) -> Path:
    """Generate a PDF quote document.

    Args:
        quote: The quote data to render
        output_path: Where to save the PDF
        quote_date: Date for the quote (defaults to today)
        expires_date: Expiration date (defaults to 7 days after quote_date)
        logo_path: Path to logo image (optional)

    Returns:
        Path to the generated PDF
    """
    builder = QuotePDFBuilder(
        quote=quote,
        output_path=output_path,
        quote_date=quote_date,
        expires_date=expires_date,
        logo_path=logo_path,
    )
    return builder.build()
