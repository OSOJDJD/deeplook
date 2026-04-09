# Contributing to DeepLook

Thanks for your interest in DeepLook! Whether it's a bug fix, a new data source, or just pointing out a report that looks off — every contribution makes the project better.

## Design principles

- **Data over opinions** — Numbers come from APIs, not LLMs. Code extraction > LLM generation.
- **Fast** — Don't slow down report generation. Avoid blocking calls and unnecessary roundtrips.
- **Structured** — Output should be parseable by both humans and AI clients.
- **Entity-aware** — A stock, a crypto token, and a VC firm need different data. Handle each properly.
- **Good context → good output** — DeepLook provides data + analytical instructions. Better context in, better analysis out.

## Ways to contribute

**No code required:**
- Open an issue when a company report has wrong data, missing fields, or crashes
- Add ground truth data to `/eval`
- Suggest new data sources or entity types

**Code contributions:**
- Fix a bug or improve data accuracy
- Add a new data source — market data, news, filings, transcripts, anything that helps understand a company
- Add analysis rules to help AI interpret data better
- Improve output formatting

## How DeepLook is structured

```
deeplook/
├── fetchers/                    # Each data source = one file (see wikipedia.py as template)
├── instruction_generator.py     # Analysis rules for the AI
├── verdict_generator.py         # Deterministic verdict from data
├── formatter.py                 # Output formatting
└── research.py                  # Pipeline orchestration
```

**Want to add a data source?** Write a fetcher in `deeplook/fetchers/`, follow the pattern in `wikipedia.py` (simplest example), register it in `search_strategy.py`.

**Want to add an analysis rule?** Add a condition to `deeplook/instruction_generator.py`. Each rule is an if/else that checks a data condition and generates an instruction for the AI.

**Want to change the output?** Edit `deeplook/formatter.py`.

## Pull requests

### Setup

```bash
git clone https://github.com/OSOJDJD/deeplook.git
cd deeplook
python3 -m venv venv && source venv/bin/activate
pip install -e .
```

### Before submitting

1. **Test locally** — Run against at least 3 entities (one stock, one crypto, one private company)
2. **Keep scope small** — One fix or feature per PR
3. **Don't break existing output** — If you change the report format, show before/after examples

### What we look for

- Does it make reports more accurate or faster?
- Does it follow existing patterns?
- Is the code straightforward? No unnecessary abstractions.

### What doesn't fit

- Raw LLM opinions injected as if they were data — DeepLook's numbers come from APIs, analysis instructions come from instruction_generator.py
- Dependencies that add significant install weight for marginal value
- Changes that slow report generation without clear accuracy gains

## Code style

- Python 3.10+
- Type hints on public functions
- `async` for all fetcher I/O
- Error handling with timeouts (10–30 seconds per external call)
- Clear variable names — `revenue_growth` not `rg`

## Questions?

Not sure if your idea fits? [Open an issue](https://github.com/OSOJDJD/deeplook/issues) — happy to discuss.
