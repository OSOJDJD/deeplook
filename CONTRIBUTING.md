# Contributing to DeepLook

Thanks for your interest in DeepLook! Whether it's a bug fix, a new data source, or just pointing out a report that looks off — every contribution makes the project better.

## Design principles

These guide every decision we make:

- **Data over opinions** — Numbers come from APIs, not LLMs. Code extraction > LLM generation.
- **Fast** — Reports under 15 seconds. Don't add blocking calls or unnecessary LLM roundtrips.
- **Structured** — Output is markdown + embedded JSON. AI clients should be able to parse it programmatically.
- **Entity-aware** — Different entity types (public_equity, crypto, private_or_unlisted, VC, exchange, foundation, defunct) need different data. Don't force one template on everything.

## Ways to contribute

**No code required:**
- Open an issue when a company report has wrong data, missing fields, or crashes
- Add ground truth data to `/eval`
- Suggest new data sources or entity types

**Code contributions:**
- Fix a bug or improve accuracy
- Add a new fetcher (data source)
- Add support for a new entity type
- Improve report formatting

## Issues

**We recommend opening an issue before writing code** — it helps us align on approach and saves you time.

Good issues include:
- Company name + what's wrong in the report
- Steps to reproduce
- Expected vs actual output

## Pull requests

### Setup

```bash
git clone https://github.com/OSOJDJD/deeplook.git
cd deeplook
python3 -m venv venv && source venv/bin/activate
pip install -e .
cp .env.example .env
```

### Before submitting

1. **Test locally** — Run against at least 3 entities (one stock, one crypto, one private company)
2. **Keep scope small** — One fix or feature per PR
3. **Don't break existing output** — If you change the report format, show before/after examples

### What we look for

- Does it make reports more accurate or faster?
- Does it follow existing patterns? (check `deeplook/fetchers/`, `deeplook/synthesize.py`, `deeplook/formatter.py`)
- Is the code straightforward? No unnecessary abstractions.

### What doesn't fit

- Raw LLM opinions injected as if they were data — DeepLook's numbers come from APIs, analysis hooks come from compressed news context
- Dependencies that add significant install weight for marginal value
- Changes that slow report generation without clear accuracy gains

Not sure if your idea fits? Open an issue first — happy to discuss.

## Code style

- Python 3.10+
- Type hints on public functions
- `async` for all fetcher I/O (exception: `finnhub_fetcher.py` is sync)
- Error handling with timeouts (10–30 seconds per external call, see `search_strategy.py`)
- Clear variable names — `revenue_growth` not `rg`

## Adding a new data source

1. Create a fetcher in `deeplook/fetchers/`
2. Follow the existing pattern: async function, returns structured dict, handles errors gracefully
3. Register in the parallel fetcher pipeline
4. Add to the entity router if it's entity-type specific
5. Test with 5+ companies across different entity types

Not sure where to start? Look at `deeplook/fetchers/wikipedia.py` — it's one of the simplest fetchers and a good template.

## Adding support for a new entity type

1. Update entity router in `deeplook/fetchers/search_strategy.py` (`get_active_fetchers` + `build_search_queries`)
2. Define which fetchers apply to this entity type
3. Update LLM compress prompt for entity-specific filtering
4. Add at least 3 test cases to `/eval`

## Questions?

[Open an issue](https://github.com/OSOJDJD/deeplook/issues) — we're happy to help.
