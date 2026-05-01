import argparse
import asyncio
import os
import sys


def main():
    parser = argparse.ArgumentParser(description="DeepLook — company research")
    parser.add_argument("company", help="Company name or ticker symbol")
    parser.add_argument(
        "--type",
        dest="entity_type",
        choices=["stock", "crypto", "auto"],
        default="auto",
        help="Entity type hint: 'stock' for equities/ETFs, 'crypto' for tokens, 'auto' to infer (default)",
    )
    parser.add_argument("--no-youtube", action="store_true", help="Skip YouTube fetcher")
    parser.add_argument("--output", metavar="FILE", help="Save report as markdown file")
    args = parser.parse_args()

    from deeplook.research import load_env, run_research
    from deeplook.formatter import format_output_v3

    load_env()

    entity_type = None if args.entity_type == "auto" else args.entity_type
    data = asyncio.run(
        run_research(
            args.company,
            include_youtube=not args.no_youtube,
            output_file=args.output,
            entity_type=entity_type,
        )
    )

    schema = os.environ.get("DEEPLOOK_SCHEMA", "v3")
    if schema == "v3" and not (args.output or sys.stdout.isatty()):
        report = format_output_v3(data)
        sys.stdout.write(report)


if __name__ == "__main__":
    main()
