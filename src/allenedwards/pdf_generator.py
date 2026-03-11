"""PDF quote generator matching Allan Edwards Template A format."""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import sys

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

# Colors matching Template A
YELLOW_HEADER = colors.Color(1, 1, 0)  # #FFFF00
BLACK = colors.black
WHITE = colors.white
GRAY = colors.Color(0.5, 0.5, 0.5)
LIGHT_GRAY = colors.Color(0.9, 0.9, 0.9)

def _resolve_default_logo_path() -> Path:
    """Resolve bundled logo path for source and frozen runtimes."""
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path.cwd()))
        bundled_logo = bundle_root / "allenedwards" / "assets" / "logo.png"
        if bundled_logo.exists():
            return bundled_logo
        return bundle_root / "assets" / "logo.png"

    package_logo = Path(__file__).parent / "assets" / "logo.png"
    if package_logo.exists():
        return package_logo

    # Backward-compatible fallback for old source layouts.
    return Path(__file__).parent.parent.parent.parent.parent / "assets" / "logo.png"


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
        """Build Template A header: logo banner, company info, quote details."""
        elements = []

        # Logo banner - use actual logo image if available
        if self.logo_path and self.logo_path.exists():
            # Use the logo image, scaled to fit nicely
            logo_img = Image(str(self.logo_path), width=4 * inch, height=0.75 * inch)
            logo_img.hAlign = "CENTER"
            banner_data = [[logo_img]]
        else:
            # Fallback to text if logo not found
            banner_data = [[Paragraph(
                '<font face="Times-Italic" size="28"><i>Allan Edwards, Inc.</i></font>',
                ParagraphStyle("Banner", alignment=1, fontName="Times-Italic", fontSize=28)
            )]]
        banner_table = Table(banner_data, colWidths=[self.content_width])
        banner_table.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        elements.append(banner_table)
        elements.append(Spacer(1, 12))

        # Company info (left) and Quote details (right)
        left_content = [
            [Paragraph("<b>Allan Edwards, Inc.</b>", self.styles["bold"])],
            [Paragraph("6468 N Yale Ave", self.styles["normal"])],
            [Paragraph("Tulsa, OK 74117", self.styles["normal"])],
            [Paragraph("(918) 583-7184", self.styles["normal"])],
        ]
        left_table = Table(left_content, colWidths=[3.5 * inch])
        left_table.setStyle(TableStyle([
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ]))

        # Right side: Quote number, date, expires (as label: value pairs)
        right_content = [
            [
                Paragraph("<b>Quote:</b>", self.styles["bold"]),
                Paragraph(self.quote.quote_number, self.styles["normal"]),
            ],
            [
                Paragraph("<b>Date:</b>", self.styles["bold"]),
                Paragraph(format_date(self.quote_date), self.styles["normal"]),
            ],
            [
                Paragraph("<b>Expires:</b>", self.styles["bold"]),
                Paragraph(format_date(self.expires_date), self.styles["normal"]),
            ],
        ]
        # Add project line reference if available (for multi-quote emails)
        if hasattr(self.quote, 'project_line') and self.quote.project_line:
            right_content.append([
                Paragraph("<b>Project:</b>", self.styles["bold"]),
                Paragraph(self.quote.project_line, self.styles["normal"]),
            ])
        right_table = Table(right_content, colWidths=[0.8 * inch, 1.5 * inch])
        right_table.setStyle(TableStyle([
            ("ALIGN", (0, 0), (0, -1), "RIGHT"),
            ("ALIGN", (1, 0), (1, -1), "LEFT"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))

        # Combine left and right
        header_table = Table(
            [[left_table, right_table]],
            colWidths=[5 * inch, 2.5 * inch]
        )
        header_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        elements.append(header_table)
        elements.append(Spacer(1, 18))

        return elements

    def _build_bill_ship_to(self) -> list:
        """Build Template A Bill To / Ship To section."""
        elements = []

        # Bill To content (customer name only in Template A)
        bill_to_lines = [
            [Paragraph("<b>Bill To:</b>", self.styles["bold"])],
        ]
        if self.quote.customer_name:
            bill_to_lines.append([Paragraph(self.quote.customer_name, self.styles["normal"])])

        bill_to_table = Table(bill_to_lines, colWidths=[3.5 * inch])
        bill_to_table.setStyle(TableStyle([
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ]))

        # Ship To content (company, contact with phone, street, city/state)
        ship_to_lines = [
            [Paragraph("<b>Ship to:</b>", self.styles["bold"])],
        ]
        if self.quote.ship_to and self.quote.ship_to.get("company"):
            ship_to_lines.append([Paragraph(self.quote.ship_to["company"], self.styles["normal"])])

        # Contact name with phone
        if self.quote.contact_name:
            contact_line = self.quote.contact_name
            if self.quote.contact_phone:
                contact_line += f" {self.quote.contact_phone}"
            ship_to_lines.append([Paragraph(contact_line, self.styles["normal"])])

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
        elements.append(Spacer(1, 18))

        return elements

    def _build_line_items_table(self) -> list:
        """Build Template A line items table with quote title."""
        elements = []

        # Quote title row (e.g., "Quote 2: HM999A3 Line")
        quote_title = self.quote.quote_number
        if self.quote.notes and ":" in self.quote.notes.split("\n")[0]:
            # Use first line of notes as quote title if it looks like a title
            first_note = self.quote.notes.split("\n")[0].strip()
            if first_note:
                quote_title = first_note
        title_para = Paragraph(f"<b>{quote_title}</b>", self.styles["bold"])
        elements.append(title_para)
        elements.append(Spacer(1, 6))

        # Header row - Template A columns
        table_data = [
            [
                Paragraph("<b>Item Number</b>", self.styles["header"]),
                Paragraph("<b>Description</b>", self.styles["header"]),
                Paragraph("<b>Quantity</b>", self.styles["header"]),
                Paragraph("<b>Unit Price</b>", self.styles["header"]),
                Paragraph("<b>Total</b>", self.styles["header"]),
            ]
        ]

        # Line items — skip rows with no real content
        for item in self.quote.line_items:
            # Skip items with zero/missing price and empty description
            if not item.is_note and not item.unit_price and not item.description.strip():
                continue
            item_number = "" if item.is_note else item.part_number
            quantity = "" if item.is_note else str(item.quantity)
            table_data.append([
                Paragraph(item_number, self.styles["normal_small"]),
                Paragraph(item.description, self.styles["normal_small"]),
                Paragraph(quantity, self.styles["normal_small"]),
                Paragraph(format_currency(item.unit_price), self.styles["normal_small"]),
                Paragraph(format_currency(item.total), self.styles["normal_small"]),
            ])

        # Column widths: Item Number 18%, Description 42%, Quantity 12%, Unit Price 14%, Total 14%
        col_widths = [
            self.content_width * 0.18,
            self.content_width * 0.42,
            self.content_width * 0.12,
            self.content_width * 0.14,
            self.content_width * 0.14,
        ]

        items_table = Table(table_data, colWidths=col_widths)

        # Build style commands
        style_commands = [
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
            # Grid - box around entire table
            ("BOX", (0, 0), (-1, -1), 0.5, BLACK),
            # Horizontal lines between all rows
            ("LINEBELOW", (0, 0), (-1, -2), 0.5, BLACK),
            # Vertical lines between columns
            ("LINEBEFORE", (1, 0), (1, -1), 0.5, BLACK),
            ("LINEBEFORE", (2, 0), (2, -1), 0.5, BLACK),
            ("LINEBEFORE", (3, 0), (3, -1), 0.5, BLACK),
            ("LINEBEFORE", (4, 0), (4, -1), 0.5, BLACK),
            # Padding
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            # Valign
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]

        items_table.setStyle(TableStyle(style_commands))
        elements.append(items_table)

        return elements

    def _build_totals(self) -> list:
        """Build Template A totals section: Subtotal, Shipping and Handling, TOTAL."""
        elements = []

        # Template A totals: right-aligned, Subtotal / Shipping and Handling / TOTAL
        totals_data = [
            [
                "",
                "",
                "",
                Paragraph("Subtotal:", self.styles["normal_small"]),
                Paragraph(format_currency(self.quote.subtotal), self.styles["normal_small"]),
            ],
            [
                "",
                "",
                "",
                Paragraph("Shipping and Handling:", self.styles["normal_small"]),
                Paragraph(format_currency(self.quote.shipping_amount) if self.quote.shipping_amount else "", self.styles["normal_small"]),
            ],
            [
                Paragraph("<b>TOTAL</b>", self.styles["bold"]),
                "",
                "",
                "",
                Paragraph(f"<b>{format_currency(self.quote.total)}</b>", self.styles["bold"]),
            ],
        ]

        # Match column widths with line items table
        col_widths = [
            self.content_width * 0.18,
            self.content_width * 0.42,
            self.content_width * 0.12,
            self.content_width * 0.14,
            self.content_width * 0.14,
        ]

        totals_table = Table(totals_data, colWidths=col_widths)
        totals_table.setStyle(TableStyle([
            # TOTAL row yellow background
            ("BACKGROUND", (0, -1), (-1, -1), YELLOW_HEADER),
            # Alignment
            ("ALIGN", (3, 0), (3, -1), "RIGHT"),
            ("ALIGN", (4, 0), (4, -1), "RIGHT"),
            ("ALIGN", (0, -1), (0, -1), "LEFT"),
            # Borders for subtotal/shipping rows
            ("LINEBELOW", (3, 0), (4, 0), 0.5, BLACK),
            ("LINEBELOW", (3, 1), (4, 1), 0.5, BLACK),
            # Box around TOTAL row
            ("BOX", (0, -1), (-1, -1), 0.5, BLACK),
            # Padding
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(totals_table)

        return elements

    def _build_footer(self, canvas, doc):
        """Draw Template A footer: website on left, page number on right."""
        canvas.saveState()

        canvas.setFont("Helvetica", 9)

        # Website on left
        canvas.drawString(self.margin, 0.4 * inch, "www.allanedwards.com")

        # Page number on right
        page_text = f"Page {doc.page} of {doc.page}"  # Will be updated by later pass
        canvas.drawRightString(self.page_width - self.margin, 0.4 * inch, page_text)

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
        # Template A: No details grid (reserved for Template B concrete coating quotes)
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
