"""
DeepLook Batch Runner

Runs full eval pipeline on test companies in parallel, outputs results + markdown summary.

Usage:
  python -m deeplook.eval.batch_runner                      # active set (default)
  python -m deeplook.eval.batch_runner --group active       # only eval_group=active
  python -m deeplook.eval.batch_runner --group fixed        # only eval_group=fixed
  python -m deeplook.eval.batch_runner --group all          # all companies
  python -m deeplook.eval.batch_runner --category crypto    # filter by category
  python -m deeplook.eval.batch_runner --query "Nvidia"     # single company
  python -m deeplook.eval.batch_runner --concurrency 3      # limit parallelism (default 5)
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime

import anthropic
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

EVAL_DIR = os.path.join(PROJECT_ROOT, "deeplook", "eval")

SCORE_DIMS = [
    "factual_accuracy",
    "no_hallucination",
    "actionability",
    "phase_accuracy",
    "risk_detection",
]

from deeplook.eval.eval import (
    load_test_companies,
    run_research_async,
    evaluate_with_llm,
    check_must_mention,
)


def _fetcher_summary(research_output: dict) -> dict:
    """Extract per-fetcher status and data char count from research output."""
    fetcher_results = research_output.get("fetcher_results", {})
    summary = {}
    for name, result in fetcher_results.items():
        if not result:
            summary[name] = {"status": "skipped", "data_chars": 0}
            continue
        status = result.get("status", "unknown")
        data = result.get("data")
        data_chars = len(json.dumps(data, default=str)) if data else 0
        summary[name] = {"status": status, "data_chars": data_chars}
    return summary


async def _research_one(
    tc: dict,
    semaphore: asyncio.Semaphore,
    include_youtube: bool = False,
) -> tuple[str, dict | None, float]:
    """Run research for one company, return (company, output, elapsed).

    NOTE: No outer timeout here — run_research_async already applies a 120s
    timeout to the subprocess *after* acquiring the semaphore.  Wrapping the
    whole coroutine (including semaphore wait) with a fixed timeout caused
    companies that queued late to be incorrectly skipped.
    """
    t0 = time.time()
    try:
        _, output = await run_research_async(tc["company"], semaphore, include_youtube=include_youtube)
    except asyncio.TimeoutError:
        print(f"  TIMEOUT: {tc['company']}")
        output = None
    except Exception as e:
        print(f"  ERROR: {tc['company']}: {e}")
        output = None
    return tc["company"], output, round(time.time() - t0, 1)


def _build_result(
    tc: dict,
    research_output: dict | None,
    elapsed: float,
    scores: dict | None,
    error: str | None,
) -> dict:
    company = tc["company"]
    crashed = research_output is None or error is not None

    if research_output:
        mention_results = check_must_mention(research_output, tc["must_mention"])
        mention_pass = all(mention_results.values())
        fetchers = _fetcher_summary(research_output)
        sources_succeeded = research_output.get("sources_succeeded", [])
        sources_failed = research_output.get("sources_failed", [])
    else:
        mention_results = {kw: False for kw in tc["must_mention"]}
        mention_pass = False
        fetchers = {}
        sources_succeeded = []
        sources_failed = []

    return {
        "company": company,
        "category": tc.get("category", "unknown"),
        "status": "FAILED" if crashed else "OK",
        "crashed": crashed,
        "error": error,
        "scores": scores,
        "must_mention": mention_results,
        "must_mention_pass": mention_pass,
        "fetchers": fetchers,
        "sources_succeeded": sources_succeeded,
        "sources_failed": sources_failed,
        "elapsed_seconds": elapsed,
    }


def _overall_score(scores: dict | None) -> float | None:
    if not scores:
        return None
    vals = [scores[d] for d in SCORE_DIMS if isinstance(scores.get(d), (int, float))]
    return round(sum(vals) / len(vals), 2) if vals else None


def _build_markdown(
    companies: list[dict],
    per_company: list[dict],
    run_timestamp: str,
    overall_avg: float,
) -> str:
    lines = [
        f"# DeepLook Batch Eval — {run_timestamp}",
        "",
        f"**Companies:** {len(per_company)}  |  **Overall avg:** {overall_avg}",
        "",
        "## Per-Company Results",
        "",
        "| Company | Category | Score | Fetchers OK | Failed Fetchers | Time(s) | Crashed |",
        "|---------|----------|-------|-------------|-----------------|---------|---------|",
    ]

    for r in per_company:
        score = _overall_score(r["scores"])
        score_str = f"{score:.2f}" if score is not None else "—"
        ok = ", ".join(r["sources_succeeded"]) or "—"
        failed = ", ".join(r["sources_failed"]) or "—"
        crashed = "YES" if r["crashed"] else "no"
        lines.append(
            f"| {r['company']} | {r['category']} | {score_str} | {ok} | {failed} | {r['elapsed_seconds']} | {crashed} |"
        )

    # Category averages
    lines += ["", "## Category Averages", ""]
    lines += ["| Category | Companies | Avg Score | Must-Mention Pass |", "|----------|-----------|-----------|------------------|"]

    by_cat: dict[str, list] = defaultdict(list)
    for r in per_company:
        by_cat[r["category"]].append(r)

    for cat in sorted(by_cat):
        rows = by_cat[cat]
        scored = [_overall_score(r["scores"]) for r in rows if _overall_score(r["scores"]) is not None]
        avg = round(sum(scored) / len(scored), 2) if scored else 0.0
        mm_pass = sum(1 for r in rows if r["must_mention_pass"])
        lines.append(f"| {cat} | {len(rows)} | {avg} | {mm_pass}/{len(rows)} |")

    # Dimension breakdown
    lines += ["", "## Score Dimensions Breakdown", ""]
    lines += ["| Dimension | Average |", "|-----------|---------|"]
    dim_totals = defaultdict(list)
    for r in per_company:
        if r["scores"]:
            for d in SCORE_DIMS:
                v = r["scores"].get(d)
                if isinstance(v, (int, float)):
                    dim_totals[d].append(v)
    for d in SCORE_DIMS:
        vals = dim_totals[d]
        avg = round(sum(vals) / len(vals), 2) if vals else 0.0
        lines.append(f"| {d} | {avg} |")

    return "\n".join(lines) + "\n"


RUNS_DIR = os.path.join(EVAL_DIR, "runs")
PATTERNS_FILE = os.path.join(EVAL_DIR, "patterns.json")
PROMPT_FILE = os.path.join(PROJECT_ROOT, "deeplook", "judgment", "prompt.py")


def _get_prompt_version() -> str:
    try:
        with open(PROMPT_FILE) as f:
            content = f.read()
        # Look for version string like "# v10.2" or "VERSION = 'v10'"
        import re
        m = re.search(r"v(\d+[\.\d]*)", content[:500])
        if m:
            return f"v{m.group(1)}"
        return hashlib.md5(content.encode()).hexdigest()[:8]
    except Exception:
        return "unknown"


def _load_last_run() -> dict | None:
    try:
        files = sorted(
            [f for f in os.listdir(RUNS_DIR) if f.endswith(".json")],
        )
        if not files:
            return None
        with open(os.path.join(RUNS_DIR, files[-1])) as f:
            return json.load(f)
    except Exception:
        return None


def _save_run_log(per_company: list[dict], overall_avg: float, args, timestamp: str) -> None:
    try:
        os.makedirs(RUNS_DIR, exist_ok=True)

        run_id = timestamp[:13]  # "20260312_1430"
        run_ts = datetime.now().isoformat()

        scores_list = [_overall_score(r["scores"]) for r in per_company]
        valid_scores = [s for s in scores_list if s is not None]

        # Build failures list (score < 3.5)
        failures = []
        for r in per_company:
            s = _overall_score(r["scores"])
            if s is not None and s < 3.5:
                dims = {d: r["scores"].get(d) for d in SCORE_DIMS} if r["scores"] else {}
                # failure_reasons from structured feedback if available
                reasons = []
                if r["scores"]:
                    for d in SCORE_DIMS:
                        v = r["scores"].get(d)
                        if isinstance(v, (int, float)) and v < 3.0:
                            fb = r["scores"].get(f"{d}_feedback") or r["scores"].get("feedback", {})
                            if isinstance(fb, dict):
                                fb = fb.get(d, "")
                            reasons.append(f"{d}: {fb}" if fb else d)
                failures.append({
                    "company": r["company"],
                    "score": s,
                    "dimensions": dims,
                    "failure_reasons": reasons,
                })

        # Compare with last run
        last_run = _load_last_run()
        improvements, regressions = [], []
        if last_run:
            last_scores = {
                r["company"]: r["score"]
                for r in last_run.get("failures", [])
            }
            # Also pull non-failure scores from last run summary companies
            for entry in last_run.get("all_scores", []):
                last_scores.setdefault(entry["company"], entry["score"])

            for r, s in zip(per_company, scores_list):
                if s is None:
                    continue
                prev = last_scores.get(r["company"])
                if prev is None:
                    continue
                delta = round(s - prev, 2)
                if delta >= 0.3:
                    improvements.append({"company": r["company"], "prev": prev, "now": s, "delta": delta})
                elif delta <= -0.3:
                    regressions.append({"company": r["company"], "prev": prev, "now": s, "delta": delta})

        log = {
            "run_id": run_id,
            "timestamp": run_ts,
            "prompt_version": _get_prompt_version(),
            "group": args.group,
            "args": {"concurrency": args.concurrency, "no_cache": args.no_cache},
            "summary": {
                "avg_score": overall_avg,
                "min_score": min(valid_scores) if valid_scores else None,
                "max_score": max(valid_scores) if valid_scores else None,
                "total_companies": len(per_company),
                "below_3": sum(1 for s in valid_scores if s < 3.0),
            },
            "failures": failures,
            "improvements_since_last": improvements,
            "regressions_since_last": regressions,
            # Store all scores for next run's comparison
            "all_scores": [
                {"company": r["company"], "score": _overall_score(r["scores"])}
                for r in per_company
                if _overall_score(r["scores"]) is not None
            ],
        }

        run_path = os.path.join(RUNS_DIR, f"{timestamp}.json")
        with open(run_path, "w") as f:
            json.dump(log, f, indent=2)
        print(f"Run log: {run_path}")

        # Trim to max 10 files
        run_files = sorted(os.listdir(RUNS_DIR))
        for old in run_files[:-10]:
            try:
                os.remove(os.path.join(RUNS_DIR, old))
            except Exception:
                pass

        # Update patterns.json
        try:
            if os.path.exists(PATTERNS_FILE):
                with open(PATTERNS_FILE) as f:
                    patterns = json.load(f)
            else:
                patterns = {"recurring_failures": {}, "last_updated": run_ts}

            current_failures = {r["company"] for r in failures}
            prev_failures = {r["company"] for r in (last_run or {}).get("failures", [])}

            for company in current_failures & prev_failures:
                entry = patterns["recurring_failures"].get(company, {
                    "times_failed": 1, "common_dimensions": [], "last_seen": run_ts,
                })
                entry["times_failed"] = entry.get("times_failed", 1) + 1
                entry["last_seen"] = run_ts
                # Update common_dimensions: dims scoring < 3.0 in current run
                for r in failures:
                    if r["company"] == company and r["dimensions"]:
                        bad_dims = [d for d, v in r["dimensions"].items() if isinstance(v, (int, float)) and v < 3.0]
                        entry["common_dimensions"] = list(set(entry.get("common_dimensions", []) + bad_dims))
                patterns["recurring_failures"][company] = entry

            patterns["last_updated"] = run_ts
            with open(PATTERNS_FILE, "w") as f:
                json.dump(patterns, f, indent=2)
        except Exception as e:
            print(f"WARNING: patterns.json update failed: {e}", file=sys.stderr)

    except Exception as e:
        print(f"WARNING: run log save failed: {e}", file=sys.stderr)


async def run_batch(
    companies: list[dict],
    concurrency: int,
    client: anthropic.Anthropic,
    include_youtube: bool = False,
) -> list[dict]:
    semaphore = asyncio.Semaphore(concurrency)

    # Phase 1: parallel research
    print(f"\n[research] Running {len(companies)} companies (concurrency={concurrency})...")
    t0 = time.time()
    research_tasks = [_research_one(tc, semaphore, include_youtube=include_youtube) for tc in companies]
    research_results = await asyncio.gather(*research_tasks)
    print(f"[research] Done in {round(time.time() - t0, 1)}s")

    research_map = {company: (output, elapsed) for company, output, elapsed in research_results}

    # Phase 2: serial evaluation (avoid rate limits)
    print(f"\n[eval] Evaluating {len(companies)} companies (serial)...")
    per_company = []
    for tc in companies:
        company = tc["company"]
        output, elapsed = research_map[company]

        if output is None:
            print(f"  SKIP {company} — no research output")
            per_company.append(_build_result(tc, None, elapsed, None, "research returned no output"))
            continue

        print(f"  Judging {company}...", end=" ", flush=True)
        try:
            scores = await asyncio.to_thread(evaluate_with_llm, client, company, output, tc)
            score_str = " | ".join(f"{d[:4]}: {scores.get(d, '?')}" for d in SCORE_DIMS)
            print(score_str)
        except Exception as e:
            print(f"ERROR: {e}")
            scores = None

        per_company.append(_build_result(tc, output, elapsed, scores, None))

    return per_company


def main():
    parser = argparse.ArgumentParser(description="DeepLook batch eval runner")
    parser.add_argument("--group", type=str, default="active", choices=["active", "fixed", "all"], help="Eval group to run: active (default), fixed, all")
    parser.add_argument("--category", type=str, default=None, help="Filter by category")
    parser.add_argument("--query", type=str, default=None, help="Single company name")
    parser.add_argument("--concurrency", type=int, default=5, help="Max parallel research (default 5)")
    parser.add_argument("--limit", type=int, default=None, help="Max companies to run")
    parser.add_argument("--youtube", action="store_true", default=False, help="Enable YouTube fetcher (default: off)")
    parser.add_argument("--no-cache", action="store_true", default=False, help="Bypass fetch cache, re-fetch all external APIs")
    args = parser.parse_args()

    if args.no_cache:
        os.environ["DEEPLOOK_NO_CACHE"] = "1"
        print("[cache] DISABLED — all fetchers will hit external APIs")

    companies = load_test_companies()

    if args.query:
        q = args.query.lower()
        companies = [c for c in companies if c["company"].lower() == q]
        if not companies:
            print(f"ERROR: no company matching '{args.query}'", file=sys.stderr)
            sys.exit(1)
    elif args.category:
        companies = [c for c in companies if c.get("category", "") == args.category]
        if not companies:
            print(f"ERROR: no companies in category '{args.category}'", file=sys.stderr)
            sys.exit(1)
    elif args.group != "all":
        companies = [c for c in companies if c.get("eval_group", "active") == args.group]
        if not companies:
            print(f"ERROR: no companies with eval_group='{args.group}'", file=sys.stderr)
            sys.exit(1)

    if args.limit:
        companies = companies[: args.limit]

    print(f"{'='*60}")
    print(f"DeepLook Batch Runner | group={args.group} | {len(companies)} companies | concurrency={args.concurrency}")
    print(f"{'='*60}")

    client = anthropic.Anthropic(timeout=120.0)
    per_company = asyncio.run(run_batch(companies, args.concurrency, client, include_youtube=args.youtube))

    # Aggregate scores
    scored = [_overall_score(r["scores"]) for r in per_company if _overall_score(r["scores"]) is not None]
    overall_avg = round(sum(scored) / len(scored), 2) if scored else 0.0
    succeeded = sum(1 for r in per_company if r["status"] == "OK")
    failed = sum(1 for r in per_company if r["status"] == "FAILED")
    mm_pass = sum(1 for r in per_company if r["must_mention_pass"])

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_ts = datetime.now().isoformat()

    # Build summary matching existing results format
    dim_totals = defaultdict(list)
    for r in per_company:
        if r["scores"]:
            for d in SCORE_DIMS:
                v = r["scores"].get(d)
                if isinstance(v, (int, float)):
                    dim_totals[d].append(v)
    avg_scores = {d: round(sum(dim_totals[d]) / len(dim_totals[d]), 2) if dim_totals[d] else 0.0 for d in SCORE_DIMS}

    summary = {
        "run_timestamp": run_ts,
        "total_companies": len(companies),
        "succeeded": succeeded,
        "failed": failed,
        "must_mention_pass_rate": f"{mm_pass}/{succeeded}" if succeeded else "0/0",
        "average_scores": avg_scores,
        "overall_average": overall_avg,
        "per_company": per_company,
    }

    # Save JSON
    json_path = os.path.join(EVAL_DIR, f"results_batch_{timestamp}.json")
    with open(json_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)

    # Save markdown
    md_path = os.path.join(EVAL_DIR, f"summary_{timestamp}.md")
    with open(md_path, "w") as f:
        f.write(_build_markdown(companies, per_company, run_ts, overall_avg))

    # Print summary
    print(f"\n{'='*60}")
    print(f"RESULTS [group={args.group}] | {succeeded}/{len(companies)} OK | overall avg: {overall_avg}")
    print(f"Must-mention pass: {mm_pass}/{succeeded}")
    print(f"Avg scores:")
    for d in SCORE_DIMS:
        print(f"  {d}: {avg_scores[d]}")
    print(f"\nJSON:  {json_path}")
    print(f"MD:    {md_path}")
    _save_run_log(per_company, overall_avg, args, timestamp)
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
