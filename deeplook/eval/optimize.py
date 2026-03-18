"""
Automated prompt optimization for DeepLook judgment prompt.

Round 0: full research (fetch + synthesize) → cache fetcher_results
Round 1+: re-synthesize from cache (no API re-fetch) → eval → optimize prompt

Usage: python -m deeplook.eval.optimize --rounds 3 --parallel
"""

import argparse
import asyncio
import glob
import importlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

JUDGMENT_DIR = os.path.join(PROJECT_ROOT, "deeplook", "judgment")
PROMPT_FILE = os.path.join(JUDGMENT_DIR, "prompt.py")
EVAL_DIR = os.path.join(PROJECT_ROOT, "deeplook", "eval")
TEST_FILE = os.path.join(EVAL_DIR, "test_companies.json")

OPTIMIZER_MODEL = "claude-sonnet-4-20250514"
TARGET_SCORE = 4.0

SCORE_DIMS = [
    "factual_accuracy",
    "no_hallucination",
    "actionability",
    "phase_accuracy",
    "risk_detection",
]


# ---------------------------------------------------------------------------
# Prompt versioning helpers
# ---------------------------------------------------------------------------

def get_current_prompt() -> str:
    ns = {}
    with open(PROMPT_FILE) as f:
        exec(f.read(), ns)
    return ns["SYSTEM_PROMPT"]


def get_prompt_from_version(version: int) -> str | None:
    path = os.path.join(JUDGMENT_DIR, f"prompt_v{version}.py")
    if not os.path.exists(path):
        return None
    ns = {}
    with open(path) as f:
        exec(f.read(), ns)
    return ns.get("SYSTEM_PROMPT")


def next_version_number() -> int:
    existing = glob.glob(os.path.join(JUDGMENT_DIR, "prompt_v*.py"))
    if not existing:
        return 2
    nums = []
    for p in existing:
        m = re.search(r"prompt_v(\d+)\.py$", p)
        if m:
            nums.append(int(m.group(1)))
    return max(nums) + 1 if nums else 2


def save_versioned_prompt(prompt_text: str, version: int) -> str:
    path = os.path.join(JUDGMENT_DIR, f"prompt_v{version}.py")
    content = f'SYSTEM_PROMPT = """{prompt_text}"""\n'
    with open(path, "w") as f:
        f.write(content)
    return path


def apply_prompt(prompt_text: str):
    content = f'SYSTEM_PROMPT = """{prompt_text}"""\n'
    with open(PROMPT_FILE, "w") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Phase 1: Full fetch (subprocess, once only)
# ---------------------------------------------------------------------------

def load_disk_cache(companies: list[dict]) -> dict[str, dict | None]:
    """Load today's research outputs from deeplook/output/ if available."""
    from datetime import date
    today = date.today().isoformat()
    output_dir = os.path.join(PROJECT_ROOT, "deeplook", "output")
    cache = {}
    for tc in companies:
        company = tc["company"]
        path = os.path.join(output_dir, f"{company}_{today}.json")
        if os.path.exists(path):
            try:
                with open(path) as f:
                    data = json.load(f)
                if "fetcher_results" in data:
                    cache[company] = data
                    continue
            except Exception:
                pass
        cache[company] = None
    return cache


def fetch_missing(companies: list[dict], disk_cache: dict[str, dict | None], parallel: bool) -> dict[str, dict | None]:
    """Only fetch companies not in disk cache."""
    from deeplook.eval.eval import run_research, run_research_async

    missing = [tc for tc in companies if disk_cache.get(tc["company"]) is None]
    if not missing:
        print("[fetch] All companies loaded from disk cache")
        return disk_cache

    print(f"[fetch] {len(disk_cache) - len(missing)} cached, fetching {len(missing)} missing...")

    if parallel and len(missing) > 1:
        semaphore = asyncio.Semaphore(5)
        async def _gather():
            return await asyncio.gather(
                *(run_research_async(tc["company"], semaphore) for tc in missing)
            )
        results = asyncio.run(_gather())
        for company, output in results:
            disk_cache[company] = output
    else:
        for tc in missing:
            disk_cache[tc["company"]] = run_research(tc["company"])

    return disk_cache


# ---------------------------------------------------------------------------
# Phase 2: Re-synthesize from cached fetcher_results (fast, no network)
# ---------------------------------------------------------------------------

def _synthesize_one(company: str, entry: dict, synthesize_fn) -> tuple[str, dict]:
    """Synthesize a single company (for use with ThreadPoolExecutor)."""
    judgment = synthesize_fn(
        company,
        entry["company_type"],
        entry["fetcher_results"],
        0.0,
        0,
    )
    return company, {
        "company": company,
        "company_type": entry["company_type"],
        "sources_succeeded": entry["sources_succeeded"],
        "sources_failed": entry["sources_failed"],
        "fetcher_results": entry["fetcher_results"],
        "judgment": judgment,
    }


def resynthesize_cached(
    companies: list[dict],
    cache: dict[str, dict],
) -> dict[str, dict | None]:
    """Call synthesize() in parallel with cached fetcher_results."""
    import deeplook.judgment.prompt as prompt_mod
    import deeplook.judgment.synthesize as synth_mod
    importlib.reload(prompt_mod)
    importlib.reload(synth_mod)
    from deeplook.judgment.synthesize import synthesize
    from concurrent.futures import ThreadPoolExecutor, as_completed

    research_map = {}
    to_synth = []
    for tc in companies:
        company = tc["company"]
        entry = cache.get(company)
        if entry is None:
            research_map[company] = None
        else:
            to_synth.append((company, entry))

    print(f"  Synthesizing {len(to_synth)} companies (5 concurrent)...")
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            pool.submit(_synthesize_one, company, entry, synthesize): company
            for company, entry in to_synth
        }
        for future in as_completed(futures):
            company, result = future.result()
            research_map[company] = result
            print(f"    {company} done")

    return research_map


# ---------------------------------------------------------------------------
# Evaluation (shared between round 0 and subsequent rounds)
# ---------------------------------------------------------------------------

def evaluate_all(companies: list[dict], research_map: dict[str, dict | None]) -> dict:
    from deeplook.eval.eval import evaluate_with_llm, check_must_mention

    client = anthropic.Anthropic(timeout=120.0)
    totals = {d: 0.0 for d in SCORE_DIMS}
    scored_count = 0
    all_results = []

    for tc in companies:
        company = tc["company"]
        output = research_map.get(company)

        if output is None:
            all_results.append({
                "company": company, "status": "FAILED", "scores": None,
                "must_mention_pass": False, "expected": tc, "research_output": None,
            })
            continue

        mention_results = check_must_mention(output, tc["must_mention"])
        mention_pass = all(mention_results.values())

        print(f"  Judging {company}...", end=" ", flush=True)
        scores = evaluate_with_llm(client, company, output, tc)

        for d in SCORE_DIMS:
            totals[d] += scores.get(d, 0)
        scored_count += 1

        score_str = " | ".join(f"{d}: {scores.get(d, '?')}" for d in SCORE_DIMS)
        mm = "PASS" if mention_pass else "FAIL"
        print(f"{score_str} | mm:{mm}")

        all_results.append({
            "company": company, "status": "OK", "scores": scores,
            "must_mention": mention_results, "must_mention_pass": mention_pass,
            "expected": tc, "research_output": output,
        })

    averages = {d: round(totals[d] / scored_count, 2) for d in SCORE_DIMS} if scored_count else {d: 0.0 for d in SCORE_DIMS}
    overall = round(sum(averages.values()) / len(averages), 2) if averages else 0

    return {
        "run_timestamp": datetime.now().isoformat(),
        "total_companies": len(companies),
        "scored_count": scored_count,
        "average_scores": averages,
        "overall_average": overall,
        "per_company": all_results,
    }


# ---------------------------------------------------------------------------
# Mistake detection + prompt optimization
# ---------------------------------------------------------------------------

def find_mistakes(results: dict) -> list[dict]:
    mistakes = []
    for entry in results["per_company"]:
        if entry["status"] != "OK" or not entry["scores"]:
            continue
        scores = entry["scores"]
        has_low = any(isinstance(v, (int, float)) and v < 3 for k, v in scores.items() if k != "notes")
        if not has_low:
            continue

        expected = entry["expected"]
        research = entry["research_output"] or {}
        judgment = research.get("judgment", {})
        ai_judgment = judgment.get("ai_judgment", {}) if isinstance(judgment, dict) else {}

        mistakes.append({
            "company": entry["company"],
            "expected_phase": expected.get("expected_phase"),
            "actual_phase": ai_judgment.get("company_phase"),
            "expected_momentum": expected.get("expected_momentum"),
            "actual_momentum": ai_judgment.get("momentum"),
            "all_scores": {k: v for k, v in scores.items() if k != "notes"},
            "eval_notes": scores.get("notes", ""),
            "sources_succeeded": research.get("sources_succeeded", []),
            "sources_failed": research.get("sources_failed", []),
        })
    return mistakes


def optimize_prompt(current_prompt: str, mistakes: list[dict]) -> str:
    client = anthropic.Anthropic(timeout=120.0)
    mistakes_text = json.dumps(mistakes, indent=2, ensure_ascii=False, default=str)

    response = client.messages.create(
        model=OPTIMIZER_MODEL,
        max_tokens=8192,
        timeout=120.0,
        messages=[{
            "role": "user",
            "content": f"""你是 prompt engineer。以下是 company research judgment prompt 和它犯的錯誤。

Current prompt:
{current_prompt}

Mistakes:
{mistakes_text}

改進 prompt 讓它不犯這些錯。規則：
- 只改需要改的部分
- 不改 JSON schema
- 區分 data quality 問題（數據源沒資料）和 judgment 問題（有資料但判斷錯）
- 只修 judgment 問題，data quality 問題標記在 notes 裡
- 特別注意 phase_accuracy：確保 prompt 有明確的 phase 判斷邏輯
- 回傳完整改進後 prompt，不要加 markdown code fence""",
        }],
    )

    text = response.content[0].text.strip()
    text = re.sub(r'^```(?:python|json)?\s*\n?', '', text)
    text = re.sub(r'\n?```\s*$', '', text)
    return text


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="DeepLook prompt optimizer (cached)")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--parallel", action="store_true")
    args = parser.parse_args()

    from deeplook.eval.eval import load_test_companies
    companies = load_test_companies()
    if args.limit:
        companies = companies[:args.limit]

    print("=" * 60)
    print(f"DeepLook Optimizer | {len(companies)} companies | {args.rounds} rounds")
    print("=" * 60)

    current_prompt = get_current_prompt()
    v1_path = os.path.join(JUDGMENT_DIR, "prompt_v1.py")
    if not os.path.exists(v1_path):
        save_versioned_prompt(current_prompt, 1)

    # === Round 0: Load from disk cache, fetch only missing ===
    print("\n[Round 0] Loading cache...")
    t0 = time.time()
    disk = load_disk_cache(companies)
    disk = fetch_missing(companies, disk, parallel=args.parallel)

    # Build fetcher cache for re-synthesis rounds
    cache = {}
    research_map = {}
    for tc in companies:
        company = tc["company"]
        output = disk.get(company)
        if output and "fetcher_results" in output:
            cache[company] = {
                "company_type": output["company_type"],
                "fetcher_results": output["fetcher_results"],
                "sources_succeeded": output.get("sources_succeeded", []),
                "sources_failed": output.get("sources_failed", []),
            }
            research_map[company] = output
        else:
            cache[company] = None
            research_map[company] = output

    cached_count = sum(1 for v in cache.values() if v is not None)
    print(f"[Cache] {cached_count}/{len(companies)} ready in {round(time.time() - t0, 1)}s")

    print("\n[Round 0] Evaluating baseline...")
    baseline = evaluate_all(companies, research_map)
    baseline_score = baseline["overall_average"]
    print(f"\n[Baseline] Overall: {baseline_score}")

    if baseline_score >= TARGET_SCORE:
        print(f"Already >= {TARGET_SCORE}. Done.")
        return

    prev_score = baseline_score
    prev_results = baseline

    for round_num in range(1, args.rounds + 1):
        print(f"\n{'=' * 60}")
        print(f"Round {round_num}/{args.rounds}")
        print(f"{'=' * 60}")

        mistakes = find_mistakes(prev_results)
        if not mistakes:
            print("All scores >= 3. Done.")
            break

        print(f"{len(mistakes)} companies with score < 3:")
        for m in mistakes:
            print(f"  {m['company']}: expected={m['expected_phase']} got={m['actual_phase']} | {m['all_scores']}")

        print(f"\nOptimizing prompt with {OPTIMIZER_MODEL}...")
        try:
            improved = optimize_prompt(current_prompt, mistakes)
        except Exception as e:
            print(f"ERROR: {e}")
            break

        if not improved or len(improved) < 100:
            print("ERROR: prompt too short. Stopping.")
            break

        version = next_version_number()
        save_versioned_prompt(improved, version)
        apply_prompt(improved)
        current_prompt = improved
        print(f"Applied v{version}")

        # Re-synthesize from cache (fast!)
        print(f"\n[Round {round_num}] Re-synthesizing from cache...")
        t0 = time.time()
        new_map = resynthesize_cached(companies, cache)
        print(f"Synthesis done in {round(time.time() - t0, 1)}s")

        print(f"\n[Round {round_num}] Evaluating...")
        new_results = evaluate_all(companies, new_map)
        new_score = new_results["overall_average"]

        delta = new_score - prev_score
        pct = (delta / prev_score * 100) if prev_score > 0 else 0
        print(f"\nv{version-1}: {prev_score} → v{version}: {new_score} ({'+' if delta > 0 else ''}{pct:.1f}%)")

        if delta <= 0:
            print("No improvement. Reverting.")
            revert = get_prompt_from_version(version - 1) if version > 2 else get_prompt_from_version(1)
            if revert:
                apply_prompt(revert)
            break

        prev_score = new_score
        prev_results = new_results

        if new_score >= TARGET_SCORE:
            print(f"Target {TARGET_SCORE} reached!")
            break

    print(f"\n{'=' * 60}")
    print(f"DONE | Baseline: {baseline_score} → Final: {prev_score}")
    if baseline_score > 0:
        d = prev_score - baseline_score
        print(f"Improvement: {d:+.2f} ({d / baseline_score * 100:+.1f}%)")
    print("=" * 60)


if __name__ == "__main__":
    main()
