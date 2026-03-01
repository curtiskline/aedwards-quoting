"""CLI entry point for Allan Edwards RFQ-to-Quote tool."""

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

import click

from .parser import ParsedRFQ, parse_rfq, parse_rfq_multi
from .pdf_generator import generate_quote_pdf
from .pricing import generate_quote
from .providers.base import LLMProvider


def get_provider() -> LLMProvider:
    """Get the configured LLM provider based on environment variables.

    Provider selection:
    - LLM_PROVIDER=mock -> MockProvider (for testing)
    - LLM_PROVIDER=claude or ANTHROPIC_API_KEY set -> ClaudeProvider
    - Otherwise -> MiniMaxProvider (requires MINIMAX_API_KEY)
    """
    provider_name = os.environ.get("LLM_PROVIDER", "").lower()

    if provider_name == "mock":
        from .providers.mock import SAMPLE_RFQ_RESPONSE, MockProvider

        return MockProvider(SAMPLE_RFQ_RESPONSE)
    elif provider_name == "claude" or os.environ.get("ANTHROPIC_API_KEY"):
        from .providers.claude import ClaudeProvider

        return ClaudeProvider()
    else:
        from .providers.minimax import MiniMaxProvider

        return MiniMaxProvider()


def serialize_parsed_rfq(rfq: ParsedRFQ) -> dict:
    """Convert ParsedRFQ to a JSON-serializable dict."""
    result = {
        "customer_name": rfq.customer_name,
        "contact_name": rfq.contact_name,
        "contact_email": rfq.contact_email,
        "contact_phone": rfq.contact_phone,
        "ship_to": asdict(rfq.ship_to) if rfq.ship_to else None,
        "po_number": rfq.po_number,
        "items": [asdict(item) for item in rfq.items],
        "urgency": rfq.urgency,
        "notes": rfq.notes,
        "confidence": rfq.confidence,
        "message_id": rfq.message_id,
        "subject": rfq.subject,
    }
    return result


def generate_quote_number() -> str:
    """Generate a new quote number.

    Format: 126-XXX (fiscal year prefix + sequential number)
    For POC, just use a timestamp-based number.
    """
    import time

    ts = int(time.time()) % 1000
    return f"126-{ts:03d}"


@click.group()
@click.version_option()
def cli():
    """Allan Edwards RFQ-to-Quote CLI Tool.

    Parse RFQ emails and generate quotes.
    """
    pass


@cli.command()
@click.argument("eml_file", type=click.Path(exists=True, path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path), help="Output JSON file")
@click.option("--pretty", is_flag=True, help="Pretty-print JSON output")
def parse(eml_file: Path, output: Path | None, pretty: bool):
    """Parse an RFQ email and output structured JSON.

    EML_FILE: Path to the .eml file to parse
    """
    try:
        provider = get_provider()
        rfq = parse_rfq(eml_file, provider)

        data = serialize_parsed_rfq(rfq)

        if pretty:
            json_str = json.dumps(data, indent=2)
        else:
            json_str = json.dumps(data)

        if output:
            output.write_text(json_str)
            click.echo(f"Wrote parsed RFQ to {output}")
        else:
            click.echo(json_str)

    except Exception as e:
        click.echo(f"Error parsing RFQ: {e}", err=True)
        sys.exit(1)


def _sanitize_filename(name: str) -> str:
    """Sanitize a string for use in filenames."""
    # Remove or replace invalid characters
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        name = name.replace(char, '')
    # Replace spaces with hyphens
    name = name.replace(' ', '-')
    # Remove any trailing dots or spaces
    name = name.strip('. ')
    return name


def _generate_pdf_filename(base_path: Path, quote_number: str, project_line: str | None, index: int, total: int) -> Path:
    """Generate a PDF filename for a quote.

    Args:
        base_path: Base path for the PDF (can be directory or file)
        quote_number: The quote number
        project_line: Project line reference if available
        index: Index of this quote (0-based)
        total: Total number of quotes

    Returns:
        Path for the PDF file
    """
    if total == 1:
        # Single quote - use base path as-is if it's a file, or generate name
        if base_path.suffix.lower() == '.pdf':
            return base_path
        else:
            return base_path / f"quote-{quote_number}.pdf"

    # Multiple quotes - generate numbered/named files
    if base_path.suffix.lower() == '.pdf':
        # User specified a file path - use it as a base
        base_dir = base_path.parent
        base_name = base_path.stem
    else:
        base_dir = base_path
        base_name = f"quote-{quote_number}"

    if project_line:
        # Use project line in filename (sanitized)
        safe_project = _sanitize_filename(project_line)
        return base_dir / f"{base_name}-{safe_project}.pdf"
    else:
        # Use numeric suffix
        return base_dir / f"{base_name}-{index + 1}.pdf"


@cli.command()
@click.argument("eml_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--pdf",
    type=click.Path(path_type=Path),
    help="Output PDF file path (or directory for multi-quote emails)",
)
@click.option(
    "--json",
    "json_output",
    type=click.Path(path_type=Path),
    help="Output JSON file path",
)
@click.option(
    "--quote-number",
    type=str,
    help="Quote number to use (auto-generated if not specified)",
)
def quote(eml_file: Path, pdf: Path | None, json_output: Path | None, quote_number: str | None):
    """Generate a quote from an RFQ email.

    EML_FILE: Path to the .eml file to process

    For emails containing multiple quote requests (different ship-to addresses),
    separate PDFs will be generated for each quote.
    """
    try:
        # Parse the RFQ - get all quote requests
        provider = get_provider()
        rfqs = parse_rfq_multi(eml_file, provider)

        if not rfqs:
            click.echo("No quote requests found in email.", err=True)
            sys.exit(1)

        # Notify user about multiple quotes
        if len(rfqs) > 1:
            click.echo(f"Detected {len(rfqs)} separate quote requests in email.")

        # Generate base quote number if not provided
        base_quote_number = quote_number or generate_quote_number()

        all_quotes = []
        for i, rfq in enumerate(rfqs):
            # Generate unique quote number for each quote
            if len(rfqs) > 1:
                qn = f"{base_quote_number}-{i + 1:02d}"
            else:
                qn = base_quote_number

            # Generate the quote
            quote_data = generate_quote(rfq, qn)
            all_quotes.append(quote_data)

            # Display quote info
            click.echo(f"\nQuote {qn}:")
            if rfq.project_line:
                click.echo(f"  Project: {rfq.project_line}")
            click.echo(f"  Customer: {quote_data.customer_name}")
            if quote_data.ship_to:
                ship_city = quote_data.ship_to.get('city', '')
                ship_state = quote_data.ship_to.get('state', '')
                if ship_city or ship_state:
                    click.echo(f"  Ship To: {ship_city}, {ship_state}")
            if quote_data.po_number:
                click.echo(f"  PO #: {quote_data.po_number}")
            click.echo(f"  Items: {len(quote_data.line_items)}")
            click.echo(f"  Total: ${quote_data.total:,.2f}")

        # Output JSON if requested (includes all quotes)
        if json_output:
            from decimal import Decimal

            def decimal_default(obj):
                if isinstance(obj, Decimal):
                    return float(obj)
                raise TypeError

            if len(all_quotes) == 1:
                # Single quote - output as before for compatibility
                quote_data = all_quotes[0]
                json_data = {
                    "quote_number": quote_data.quote_number,
                    "customer_name": quote_data.customer_name,
                    "contact_name": quote_data.contact_name,
                    "contact_email": quote_data.contact_email,
                    "contact_phone": quote_data.contact_phone,
                    "ship_to": quote_data.ship_to,
                    "po_number": quote_data.po_number,
                    "project_line": quote_data.project_line,
                    "line_items": [asdict(item) for item in quote_data.line_items],
                    "subtotal": float(quote_data.subtotal),
                    "shipping_amount": float(quote_data.shipping_amount)
                    if quote_data.shipping_amount
                    else None,
                    "tax_amount": float(quote_data.tax_amount),
                    "total": float(quote_data.total),
                    "notes": quote_data.notes,
                }
            else:
                # Multiple quotes - output as array
                json_data = {
                    "quote_count": len(all_quotes),
                    "quotes": [
                        {
                            "quote_number": q.quote_number,
                            "customer_name": q.customer_name,
                            "contact_name": q.contact_name,
                            "contact_email": q.contact_email,
                            "contact_phone": q.contact_phone,
                            "ship_to": q.ship_to,
                            "po_number": q.po_number,
                            "project_line": q.project_line,
                            "line_items": [asdict(item) for item in q.line_items],
                            "subtotal": float(q.subtotal),
                            "shipping_amount": float(q.shipping_amount)
                            if q.shipping_amount
                            else None,
                            "tax_amount": float(q.tax_amount),
                            "total": float(q.total),
                            "notes": q.notes,
                        }
                        for q in all_quotes
                    ],
                }
            json_output.write_text(json.dumps(json_data, indent=2, default=decimal_default))
            click.echo(f"\nWrote quote JSON to {json_output}")

        # Generate PDF(s) if requested
        if pdf:
            # Ensure parent directory exists
            if pdf.suffix.lower() == '.pdf':
                pdf.parent.mkdir(parents=True, exist_ok=True)
            else:
                pdf.mkdir(parents=True, exist_ok=True)

            for i, quote_data in enumerate(all_quotes):
                pdf_path = _generate_pdf_filename(
                    pdf, base_quote_number, quote_data.project_line, i, len(all_quotes)
                )
                generate_quote_pdf(quote_data, pdf_path)
                click.echo(f"Wrote quote PDF to {pdf_path}")

    except Exception as e:
        click.echo(f"Error generating quote: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("directory", type=click.Path(exists=True, path_type=Path))
@click.option("--output-dir", "-o", type=click.Path(path_type=Path), help="Output directory")
def batch(directory: Path, output_dir: Path | None):
    """Process all .eml files in a directory.

    DIRECTORY: Path to directory containing .eml files

    For emails containing multiple quote requests, separate PDFs will be
    generated for each quote.
    """
    if output_dir is None:
        output_dir = directory / "output"

    output_dir.mkdir(parents=True, exist_ok=True)

    eml_files = list(directory.glob("*.eml"))
    click.echo(f"Found {len(eml_files)} .eml files")

    provider = get_provider()
    total_quotes = 0

    for eml_file in eml_files:
        click.echo(f"\nProcessing: {eml_file.name}")
        try:
            rfqs = parse_rfq_multi(eml_file, provider)

            if not rfqs:
                click.echo("  No quote requests found")
                continue

            if len(rfqs) > 1:
                click.echo(f"  Found {len(rfqs)} quote requests")

            base_quote_number = generate_quote_number()

            from decimal import Decimal

            def decimal_default(obj):
                if isinstance(obj, Decimal):
                    return float(obj)
                raise TypeError

            for i, rfq in enumerate(rfqs):
                # Generate unique quote number
                if len(rfqs) > 1:
                    quote_number = f"{base_quote_number}-{i + 1:02d}"
                else:
                    quote_number = base_quote_number

                quote_data = generate_quote(rfq, quote_number)

                # Generate filename based on project line or index
                if rfq.project_line:
                    safe_project = _sanitize_filename(rfq.project_line)
                    base_name = f"{eml_file.stem}-{safe_project}"
                elif len(rfqs) > 1:
                    base_name = f"{eml_file.stem}-{i + 1}"
                else:
                    base_name = eml_file.stem

                # Save JSON
                json_path = output_dir / f"{base_name}.json"
                json_data = {
                    "quote_number": quote_data.quote_number,
                    "customer_name": quote_data.customer_name,
                    "po_number": quote_data.po_number,
                    "project_line": quote_data.project_line,
                    "ship_to": quote_data.ship_to,
                    "line_items": [asdict(item) for item in quote_data.line_items],
                    "total": float(quote_data.total),
                }
                json_path.write_text(json.dumps(json_data, indent=2, default=decimal_default))

                # Save PDF
                pdf_path = output_dir / f"{base_name}.pdf"
                generate_quote_pdf(quote_data, pdf_path)

                # Show quote info
                project_info = f" ({rfq.project_line})" if rfq.project_line else ""
                click.echo(f"  -> {quote_number}{project_info}: ${quote_data.total:,.2f}")
                total_quotes += 1

        except Exception as e:
            click.echo(f"  Error: {e}", err=True)

    click.echo(f"\nGenerated {total_quotes} quotes from {len(eml_files)} emails")


def main():
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
