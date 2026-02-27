"""CLI entry point for Allan Edwards RFQ-to-Quote tool."""

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

import click

from .parser import ParsedRFQ, parse_rfq
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


@cli.command()
@click.argument("eml_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--pdf",
    type=click.Path(path_type=Path),
    help="Output PDF file path",
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
    """
    try:
        # Parse the RFQ
        provider = get_provider()
        rfq = parse_rfq(eml_file, provider)

        # Generate quote number if not provided
        if not quote_number:
            quote_number = generate_quote_number()

        # Generate the quote
        quote_data = generate_quote(rfq, quote_number)

        click.echo(f"Quote {quote_number} generated:")
        click.echo(f"  Customer: {quote_data.customer_name}")
        click.echo(f"  Items: {len(quote_data.line_items)}")
        click.echo(f"  Total: ${quote_data.total:,.2f}")

        # Output JSON if requested
        if json_output:
            from decimal import Decimal

            def decimal_default(obj):
                if isinstance(obj, Decimal):
                    return float(obj)
                raise TypeError

            json_data = {
                "quote_number": quote_data.quote_number,
                "customer_name": quote_data.customer_name,
                "contact_name": quote_data.contact_name,
                "contact_email": quote_data.contact_email,
                "contact_phone": quote_data.contact_phone,
                "ship_to": quote_data.ship_to,
                "line_items": [asdict(item) for item in quote_data.line_items],
                "subtotal": float(quote_data.subtotal),
                "shipping_amount": float(quote_data.shipping_amount)
                if quote_data.shipping_amount
                else None,
                "tax_amount": float(quote_data.tax_amount),
                "total": float(quote_data.total),
                "notes": quote_data.notes,
            }
            json_output.write_text(json.dumps(json_data, indent=2, default=decimal_default))
            click.echo(f"Wrote quote JSON to {json_output}")

        # Generate PDF if requested
        if pdf:
            generate_quote_pdf(quote_data, pdf)
            click.echo(f"Wrote quote PDF to {pdf}")

    except Exception as e:
        click.echo(f"Error generating quote: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("directory", type=click.Path(exists=True, path_type=Path))
@click.option("--output-dir", "-o", type=click.Path(path_type=Path), help="Output directory")
def batch(directory: Path, output_dir: Path | None):
    """Process all .eml files in a directory.

    DIRECTORY: Path to directory containing .eml files
    """
    if output_dir is None:
        output_dir = directory / "output"

    output_dir.mkdir(parents=True, exist_ok=True)

    eml_files = list(directory.glob("*.eml"))
    click.echo(f"Found {len(eml_files)} .eml files")

    provider = get_provider()

    for eml_file in eml_files:
        click.echo(f"\nProcessing: {eml_file.name}")
        try:
            rfq = parse_rfq(eml_file, provider)
            quote_number = generate_quote_number()
            quote_data = generate_quote(rfq, quote_number)

            # Save JSON
            json_path = output_dir / f"{eml_file.stem}.json"
            from decimal import Decimal

            def decimal_default(obj):
                if isinstance(obj, Decimal):
                    return float(obj)
                raise TypeError

            json_data = {
                "quote_number": quote_data.quote_number,
                "customer_name": quote_data.customer_name,
                "line_items": [asdict(item) for item in quote_data.line_items],
                "total": float(quote_data.total),
            }
            json_path.write_text(json.dumps(json_data, indent=2, default=decimal_default))

            # Save PDF
            pdf_path = output_dir / f"{eml_file.stem}.pdf"
            generate_quote_pdf(quote_data, pdf_path)

            click.echo(f"  -> {quote_number}: ${quote_data.total:,.2f}")

        except Exception as e:
            click.echo(f"  Error: {e}", err=True)


def main():
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
