"""
DeepLook eval harness.

Runs research.py on test companies, then uses Claude Haiku as an LLM judge
to score each result on five dimensions (1-5).

Usage: python -m deeplook.eval.eval
"""

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

import anthropic
from dotenv import load_dotenv

load_dotenv()


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EVAL_DIR = os.path.join(PROJECT_ROOT, "deeplook", "eval")
TEST_FILE = os.path.join(EVAL_DIR, "test_companies.json")

EVALUATOR_MODEL = "claude-haiku-4-5-20251001"

EVALUATOR_PROMPT = """You are an evaluator for a company research system. You will receive:
1. The research output (JSON) produced by the system
2. The expected values for this company

Score each dimension from 1 (terrible) to 5 (excellent):

- factual_accuracy: Are the stated facts plausible and supported by cited data sources? (1=mostly wrong, 5=all facts well-supported)
- no_hallucination: Does the output avoid making claims not grounded in the provided data? (1=many fabricated claims, 5=every claim traceable to a source)
- actionability: Is the recommended_action specific, useful, and tied to evidence? (1=vague/useless, 5=clear next step with reasoning)
- phase_accuracy: Does the assigned company_phase match the expected phase? (1=completely wrong, 5=exact match)
- risk_detection: Are risk signals properly identified and cited? (1=missed obvious risks, 5=all relevant risks caught with evidence)

Return valid JSON only, no markdown:
{
  "factual_accuracy": <int 1-5>,
  "no_hallucination": <int 1-5>,
  "actionability": <int 1-5>,
  "phase_accuracy": <int 1-5>,
  "risk_detection": <int 1-5>,
  "notes": "<brief explanation of scores>"
}"""


def load_test_companies() -> list[dict]:
    with open(TEST_FILE) as f:
        return json.load(f)


def run_research(company: str) -> dict | None:
    """Run research.py as a subprocess and capture the JSON output."""
    print(f"\n{'='*60}")
    print(f"Running research for: {company}")
    print(f"{'='*60}")

    try:
        result = subprocess.run(
            [sys.executable, "-m", "deeplook.research", company],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=PROJECT_ROOT,
        )

        # research.py prints status to stderr and JSON to stdout
        if result.stderr:
            print(f"[stderr] {result.stderr[:500]}", file=sys.stderr)

        # Extract JSON from stdout — find the last valid JSON object
        stdout = result.stdout.strip()
        if not stdout:
            print(f"  ERROR: no stdout from research.py (exit code {result.returncode})")
            return None

        # The JSON block is the main output; find it by looking for the
        # outermost { ... } that parses successfully.
        # research.py prints debug lines then the JSON blob.
        brace_start = stdout.rfind("\n{")
        if brace_start == -1:
            if stdout.startswith("{"):
                brace_start = -1  # will use full string
            else:
                print(f"  ERROR: could not find JSON in stdout")
                return None

        json_str = stdout[brace_start:].strip()
        print(f"  [debug] json_str len={len(json_str)} first100={json_str[:100]!r} last100={json_str[-100:]!r}")
        try:
            decoder = json.JSONDecoder()
            output, _ = decoder.raw_decode(json_str)
        except json.JSONDecodeError as e:
            print(f"  ERROR: failed to parse JSON: {e}")
            try:
                start = json_str.index('{')
                end = json_str.rindex('}') + 1
                output = json.loads(json_str[start:end])
            except Exception as e2:
                print(f"  ERROR: fallback extraction also failed: {e2}")
                return None
        print(f"  OK: got research output with {len(output)} keys")
        return output

    except subprocess.TimeoutExpired:
        print(f"  ERROR: research timed out after 120s")
        return None
    except json.JSONDecodeError as e:
        print(f"  ERROR: failed to parse JSON: {e}")
        return None
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


async def run_research_async(company: str, semaphore: asyncio.Semaphore, include_youtube: bool = False) -> tuple[str, dict | None]:
    """Run research in a subprocess with concurrency limited by semaphore."""
    async with semaphore:
        print(f"\n{'='*60}")
        print(f"Running research for: {company}")
        print(f"{'='*60}")

        try:
            cmd = [sys.executable, "-m", "deeplook.research", company]
            if not include_youtube:
                cmd.append("--no-youtube")
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=PROJECT_ROOT,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=180)
            stdout = stdout_bytes.decode().strip()
            stderr = stderr_bytes.decode()

            if stderr:
                print(f"[stderr] {stderr[:500]}", file=sys.stderr)

            if not stdout:
                print(f"  ERROR: no stdout from research.py (exit code {proc.returncode})")
                return company, None

            brace_start = stdout.rfind("\n{")
            if brace_start == -1:
                if stdout.startswith("{"):
                    brace_start = -1
                else:
                    print(f"  ERROR: could not find JSON in stdout")
                    return company, None

            json_str = stdout[brace_start:].strip()
            print(f"  [debug] {company}: json_str len={len(json_str)} first100={json_str[:100]!r} last100={json_str[-100:]!r}")
            try:
                decoder = json.JSONDecoder()
                output, _ = decoder.raw_decode(json_str)
            except json.JSONDecodeError as e:
                print(f"  ERROR: failed to parse JSON for {company}: {e}")
                try:
                    start = json_str.index('{')
                    end = json_str.rindex('}') + 1
                    output = json.loads(json_str[start:end])
                except Exception as e2:
                    print(f"  ERROR: fallback extraction also failed for {company}: {e2}")
                    return company, None
            print(f"  OK: got research output for {company} with {len(output)} keys")
            return company, output

        except asyncio.TimeoutError:
            print(f"  ERROR: research timed out after 120s for {company}")
            return company, None
        except json.JSONDecodeError as e:
            print(f"  ERROR: failed to parse JSON for {company}: {e}")
            return company, None
        except Exception as e:
            print(f"  ERROR: {company}: {e}")
            return company, None


KEYWORD_ALIASES = {
    "sbf": ["sam bankman-fried", "bankman-fried", "sbf"],
    "fraud": ["fraud", "embezzlement", "criminal", "convicted"],
    "chips": ["chips", "chip", "semiconductor", "semiconductors"],
    "sidechain": ["sidechain", "side-chain", "side chain", "layer-2", "l2"],
    "ust": ["ust", "terrausd", "terra usd"],
    "iphone": ["iphone", "ios devices"],
    "services": ["services", "apple services", "app store", "icloud", "apple tv"],
    "rewards": ["rewards", "cashback", "cash back", "earn bitcoin"],
    "app": ["app", "application", "mobile"],
    "ev": ["ev", "electric vehicle", "electric car", "bev"],
    "autonomous": ["autonomous", "self-driving", "fsd", "autopilot", "robotaxi"],
    "ecommerce": ["ecommerce", "e-commerce", "online store", "online commerce"],
    "merchants": ["merchants", "seller", "sellers", "retailer", "storefront"],
    "sec": ["sec", "securities and exchange", "regulatory", "lawsuit"],
    "coworking": ["coworking", "co-working", "office space", "workspace"],
}


def check_must_mention(output: dict, keywords: list[str]) -> dict:
    """Check whether all must_mention keywords appear somewhere in the output.

    Uses alias expansion so e.g. 'SBF' also matches 'Sam Bankman-Fried'.
    """
    output_text = json.dumps(output).lower()
    results = {}
    for kw in keywords:
        aliases = KEYWORD_ALIASES.get(kw.lower(), [kw.lower()])
        results[kw] = any(alias in output_text for alias in aliases)
    return results


def evaluate_with_llm(
    client: anthropic.Anthropic,
    company: str,
    research_output: dict,
    expected: dict,
) -> dict:
    """Use Claude Haiku to score the research output."""
    user_message = json.dumps(
        {
            "company": company,
            "expected_phase": expected["expected_phase"],
            "expected_momentum": expected["expected_momentum"],
            "expected_action_direction": expected["expected_action_direction"],
            "must_mention": expected["must_mention"],
            "research_output": research_output,
        },
        indent=2,
        default=str,
    )

    try:
        response = client.messages.create(
            model=EVALUATOR_MODEL,
            max_tokens=1024,
            system=EVALUATOR_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            timeout=10.0,
        )
    except Exception as e:
        return {
            "factual_accuracy": -1,
            "no_hallucination": -1,
            "actionability": -1,
            "phase_accuracy": -1,
            "risk_detection": -1,
            "notes": f"Evaluator API error: {e}",
        }

    raw = response.content[0].text.strip()
    # Strip code fences (same logic as synthesize.py)
    match = re.match(r"^```(?:json)?\s*\n?(.*?)```$", raw, re.DOTALL)
    if match:
        raw = match.group(1).strip()

    try:
        scores = json.loads(raw)
    except json.JSONDecodeError:
        scores = {
            "factual_accuracy": -1,
            "no_hallucination": -1,
            "actionability": -1,
            "phase_accuracy": -1,
            "risk_detection": -1,
            "notes": f"Evaluator returned unparseable response: {response.content[0].text[:200]}",
        }

    return scores


def main():
    parser = argparse.ArgumentParser(description="DeepLook eval harness")
    parser.add_argument("--limit", type=int, default=None, help="Only evaluate first N companies")
    parser.add_argument("--parallel", action="store_true", help="Run research concurrently (max 5)")
    args = parser.parse_args()

    companies = load_test_companies()
    if args.limit is not None:
        companies = companies[: args.limit]
    print(f"Loaded {len(companies)} test companies from {TEST_FILE}")

    client = anthropic.Anthropic(timeout=120.0)

    all_results = []
    score_dimensions = [
        "factual_accuracy",
        "no_hallucination",
        "actionability",
        "phase_accuracy",
        "risk_detection",
    ]
    totals = {d: 0.0 for d in score_dimensions}
    scored_count = 0

    # --- Research phase ---
    if args.parallel:
        print(f"\n[parallel] Running research for {len(companies)} companies (max 5 concurrent)...")
        t0 = time.time()
        semaphore = asyncio.Semaphore(5)

        async def _gather():
            return await asyncio.gather(
                *(run_research_async(tc["company"], semaphore) for tc in companies)
            )

        research_results = asyncio.run(_gather())
        research_map = {company: output for company, output in research_results}
        elapsed_research = round(time.time() - t0, 1)
        print(f"[parallel] All research done in {elapsed_research}s")
    else:
        research_map = {}
        for tc in companies:
            t0 = time.time()
            research_map[tc["company"]] = run_research(tc["company"])

    # --- Evaluation phase (always serial) ---
    for tc in companies:
        company = tc["company"]
        research_output = research_map.get(company)

        if research_output is None:
            entry = {
                "company": company,
                "status": "FAILED",
                "error": "research.py returned no valid output",
                "scores": None,
                "must_mention": {kw: False for kw in tc["must_mention"]},
            }
            all_results.append(entry)
            print(f"  SKIP: no output to evaluate for {company}")
            continue

        # Check must_mention keywords
        mention_results = check_must_mention(research_output, tc["must_mention"])
        mention_pass = all(mention_results.values())

        # LLM evaluation (serial to avoid rate limit)
        print(f"  Evaluating {company} with {EVALUATOR_MODEL}...")
        scores = evaluate_with_llm(client, company, research_output, tc)

        for d in score_dimensions:
            totals[d] += scores.get(d, 0)
        scored_count += 1

        entry = {
            "company": company,
            "status": "OK",
            "scores": scores,
            "must_mention": mention_results,
            "must_mention_pass": mention_pass,
        }
        all_results.append(entry)

        # Print summary for this company
        score_str = " | ".join(f"{d}: {scores.get(d, '?')}" for d in score_dimensions)
        mention_str = "PASS" if mention_pass else f"FAIL ({mention_results})"
        print(f"  Scores: {score_str}")
        print(f"  Must-mention: {mention_str}")

    # Aggregate
    print(f"\n{'='*60}")
    print("AGGREGATE RESULTS")
    print(f"{'='*60}")

    if scored_count > 0:
        averages = {d: round(totals[d] / scored_count, 2) for d in score_dimensions}
    else:
        averages = {d: 0.0 for d in score_dimensions}

    total_pass = sum(1 for r in all_results if r["status"] == "OK")
    total_fail = sum(1 for r in all_results if r["status"] == "FAILED")
    mention_pass_count = sum(1 for r in all_results if r.get("must_mention_pass", False))

    summary = {
        "run_timestamp": datetime.now().isoformat(),
        "total_companies": len(companies),
        "succeeded": total_pass,
        "failed": total_fail,
        "must_mention_pass_rate": f"{mention_pass_count}/{total_pass}" if total_pass else "0/0",
        "average_scores": averages,
        "overall_average": round(sum(averages.values()) / len(averages), 2) if averages else 0,
        "per_company": all_results,
    }

    print(f"  Succeeded: {total_pass}/{len(companies)}")
    print(f"  Must-mention pass: {mention_pass_count}/{total_pass}")
    print(f"  Average scores:")
    for d in score_dimensions:
        print(f"    {d}: {averages[d]}")
    print(f"  Overall average: {summary['overall_average']}")

    # Save results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(EVAL_DIR, f"results_{timestamp}.json")
    with open(output_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
