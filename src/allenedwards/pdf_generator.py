"""PDF quote generator matching Allan Edwards format."""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
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


def format_currency(amount: Decimal | None) -> str:
    """Format a decimal as currency."""
    if amount is None:
        return ""
    return f"${amount:,.2f}"


def generate_quote_pdf(quote: Quote, output_path: Path, quote_date: date | None = None) -> Path:
    """Generate a PDF quote document.

    Args:
        quote: The quote data to render
        output_path: Where to save the PDF
        quote_date: Date for the quote (defaults to today)

    Returns:
        Path to the generated PDF
    """
    if quote_date is None:
        quote_date = date.today()

    expires_date = quote_date + timedelta(days=7)

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )

    # Build the document content
    elements = []

    # Styles
    title_style = ParagraphStyle(
        "Title",
        fontName="Times-BoldItalic",
        fontSize=28,
        alignment=1,  # Center
        spaceAfter=6,
    )

    normal_style = ParagraphStyle(
        "Normal",
        fontName="Helvetica",
        fontSize=10,
        leading=12,
    )

    bold_style = ParagraphStyle(
        "Bold",
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=12,
    )

    header_style = ParagraphStyle(
        "Header",
        fontName="Helvetica-Bold",
        fontSize=10,
        textColor=WHITE,
    )

    # Title / Logo (script font approximation)
    title = Paragraph("<i>Allan Edwards, Inc.</i>", title_style)
    elements.append(title)

    # Horizontal line
    line_table = Table([[""]], colWidths=[7.5 * inch])
    line_table.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, -1), 2, BLACK),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    elements.append(line_table)
    elements.append(Spacer(1, 12))

    # Header info table (company info on left, quote info on right)
    header_data = [
        [
            Paragraph("<b>Allan Edwards, Inc.</b>", normal_style),
            "",
            Paragraph(f"<b>Quote:</b> {quote.quote_number}", normal_style),
        ],
        [
            Paragraph("6468 N Yale Ave", normal_style),
            "",
            Paragraph(f"<b>Date:</b> {quote_date.strftime('%-m/%-d/%Y')}", normal_style),
        ],
        [
            Paragraph("Tulsa, OK 74117", normal_style),
            "",
            Paragraph(f"<b>Expires:</b> {expires_date.strftime('%-m/%-d/%Y')}", normal_style),
        ],
        [Paragraph("(918) 583-7184", normal_style), "", ""],
    ]

    header_table = Table(header_data, colWidths=[3 * inch, 2.5 * inch, 2 * inch])
    header_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )
    elements.append(header_table)
    elements.append(Spacer(1, 18))

    # Bill To / Ship To
    bill_to_lines = [Paragraph("<b>Bill To:</b>", normal_style)]
    if quote.customer_name:
        bill_to_lines.append(Paragraph(quote.customer_name, normal_style))

    ship_to_lines = [Paragraph("<b>Ship to:</b>", normal_style)]
    if quote.ship_to:
        if quote.ship_to.get("company"):
            ship_to_lines.append(Paragraph(quote.ship_to["company"], normal_style))
        if quote.contact_name and quote.contact_phone:
            ship_to_lines.append(
                Paragraph(f"{quote.contact_name} {quote.contact_phone}", normal_style)
            )
        elif quote.contact_name:
            ship_to_lines.append(Paragraph(quote.contact_name, normal_style))
        if quote.ship_to.get("street"):
            ship_to_lines.append(Paragraph(quote.ship_to["street"], normal_style))
        city_state = []
        if quote.ship_to.get("city"):
            city_state.append(quote.ship_to["city"])
        if quote.ship_to.get("state"):
            city_state.append(quote.ship_to["state"])
        if city_state:
            ship_to_lines.append(Paragraph(", ".join(city_state), normal_style))

    # Create nested tables for bill to / ship to
    bill_to_table = Table([[line] for line in bill_to_lines], colWidths=[3.5 * inch])
    ship_to_table = Table([[line] for line in ship_to_lines], colWidths=[3.5 * inch])

    address_table = Table([[bill_to_table, ship_to_table]], colWidths=[3.75 * inch, 3.75 * inch])
    address_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    elements.append(address_table)
    elements.append(Spacer(1, 18))

    # Details grid
    details_data = [
        [Paragraph("<b>Sales Rep</b>", normal_style), Paragraph(quote.sales_rep, normal_style)],
        [Paragraph("<b>Payment Terms</b>", normal_style), Paragraph(quote.payment_terms, normal_style)],
        [
            Paragraph("<b>PO #</b>", normal_style),
            Paragraph(quote.po_number or "", normal_style),
        ],
        [Paragraph("<b>Shipping Method</b>", normal_style), Paragraph(quote.shipping_terms, normal_style)],
    ]
    details_table = Table(details_data, colWidths=[1.75 * inch, 5.75 * inch])
    details_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, BLACK),
                ("BACKGROUND", (0, 0), (0, -1), colors.Color(0.95, 0.95, 0.95)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elements.append(details_table)
    elements.append(Spacer(1, 18))

    # Line items table
    # Header row
    table_data = [
        [
            Paragraph("<b>Item Number</b>", header_style),
            Paragraph("<b>Description</b>", header_style),
            Paragraph("<b>Quantity</b>", header_style),
            Paragraph("<b>Unit Price</b>", header_style),
            Paragraph("<b>Total</b>", header_style),
        ]
    ]

    # Line items
    for item in quote.line_items:
        table_data.append(
            [
                Paragraph(item.part_number, normal_style),
                Paragraph(item.description, normal_style),
                str(item.quantity),
                format_currency(item.unit_price),
                format_currency(item.total),
            ]
        )

    # Add empty rows to fill space (like the sample)
    while len(table_data) < 11:
        table_data.append(["", "", "", "$0.00", "$0.00"])

    # Subtotal row
    table_data.append(["", "", "", "Subtotal:", format_currency(quote.subtotal)])

    # Shipping row
    shipping_str = format_currency(quote.shipping_amount) if quote.shipping_amount else ""
    table_data.append(["", "", "", "Shipping and Handling:", shipping_str])

    # Total row
    table_data.append(
        [Paragraph("<b>TOTAL</b>", bold_style), "", "", "", format_currency(quote.total)]
    )

    col_widths = [1.3 * inch, 3 * inch, 0.8 * inch, 1 * inch, 1 * inch]
    items_table = Table(table_data, colWidths=col_widths)

    items_table.setStyle(
        TableStyle(
            [
                # Header row styling
                ("BACKGROUND", (0, 0), (-1, 0), YELLOW_HEADER),
                ("TEXTCOLOR", (0, 0), (-1, 0), BLACK),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                # Data rows
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (2, 1), (2, -1), "CENTER"),  # Quantity centered
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),  # Prices right aligned
                # Grid
                ("GRID", (0, 0), (-1, -4), 0.5, BLACK),
                ("LINEBELOW", (0, -3), (-1, -3), 0.5, BLACK),  # Subtotal line
                ("LINEBELOW", (0, -1), (-1, -1), 1, BLACK),  # Total line
                # Padding
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                # Valign
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )

    elements.append(items_table)

    # Footer
    elements.append(Spacer(1, 36))
    footer_table = Table(
        [
            [
                Paragraph("www.allanedwards.com", normal_style),
                Paragraph("Page 1 of 1", normal_style),
            ]
        ],
        colWidths=[5.5 * inch, 2 * inch],
    )
    footer_table.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (0, 0), "LEFT"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ]
        )
    )
    elements.append(footer_table)

    # Build PDF
    doc.build(elements)

    return output_path
