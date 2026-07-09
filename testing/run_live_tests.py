#!/usr/bin/env python3
"""
run_live_tests.py — Run the 48 test scenarios through the LIVE Hermes brain on the VPS.

WHY THIS IS SAFE / ISOLATED
---------------------------
It calls Hermes DIRECTLY in one-shot mode (`hermes --yolo -z "..."`), the same brain the
processor uses — but it does NOT go through the webhook -> processor -> gorgias_writer path,
so NOTHING is ever posted to Gorgias. Hermes' tools are read-only anyway, and the test order
numbers / emails are fake (test-*@example.com, #103xx) so they match no real customer. No
customer is ever contacted. This only reads the KB and prints a draft to your terminal/file.

USAGE (on the VPS, from the folder that contains scenarios.json)
---------------------------------------------------------------
    python3 run_live_tests.py                 # run all scenarios -> results-live.json
    python3 run_live_tests.py --limit 1       # smoke test: just the first scenario
    python3 run_live_tests.py --ids R05,E01   # run only specific scenario ids
    python3 run_live_tests.py --hermes /usr/local/bin/hermes   # custom hermes path

Then send results-live.json back to Claude and say "judge these".
"""
import argparse, json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
SCENARIOS = os.path.join(HERE, "scenarios.json")
OUT = os.path.join(HERE, "results-live.json")

# The one-shot prompt. Mirrors the processor's "process ticket" framing, but supplies the
# ticket inline (there is no real Gorgias ticket) and forbids any writes.
PROMPT_TEMPLATE = """process ticket (OFFLINE QA TEST — do NOT post, tag, send, or write anything anywhere; \
do NOT look this customer or order up in Gorgias, the IDs are fake test data; treat the text below as the ENTIRE ticket):

Subject: {subject}
Customer email: {email}
Message: {message}

Follow your normal Buttons Bebe workflow: call search_kb FIRST to ground yourself in store policy, \
classify the risk, then either draft a reply (LOW risk) or escalate with an internal note (SENSITIVE). \
Never guess sizing/measurements/fabric. If there is no actionable content, do not draft. \
Reply in the customer's language. Output EXACTLY this format and nothing else:

RISK: LOW | SENSITIVE
ACTION: DRAFT | ESCALATE | NO_ACTION
ANSWER:
<the customer-facing draft if DRAFT, otherwise the internal note for a human>
"""

def run_one(hermes, prompt, timeout):
    started = time.time()
    try:
        proc = subprocess.run(
            [hermes, "--yolo", "-z", prompt],
            capture_output=True, text=True, timeout=timeout,
        )
        out = (proc.stdout or "").strip()
        if not out and proc.stderr:
            out = "[stderr] " + proc.stderr.strip()
    except subprocess.TimeoutExpired:
        out = f"[TIMEOUT after {timeout}s]"
    except FileNotFoundError:
        print(f"ERROR: could not find hermes at '{hermes}'. Pass --hermes /path/to/hermes", file=sys.stderr)
        sys.exit(1)
    return out, round(time.time() - started, 1)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hermes", default="hermes", help="path to the hermes binary (default: hermes on PATH)")
    ap.add_argument("--limit", type=int, default=0, help="only run the first N scenarios (smoke test)")
    ap.add_argument("--ids", default="", help="comma-separated scenario ids to run (e.g. R05,E01)")
    ap.add_argument("--timeout", type=int, default=180, help="seconds per scenario (default 180)")
    ap.add_argument("--sleep", type=float, default=2.0, help="pause between scenarios (default 2s)")
    args = ap.parse_args()

    with open(SCENARIOS) as f:
        scenarios = json.load(f)

    if args.ids:
        want = {s.strip() for s in args.ids.split(",") if s.strip()}
        scenarios = [s for s in scenarios if s["id"] in want]
    if args.limit:
        scenarios = scenarios[:args.limit]

    print(f"Running {len(scenarios)} scenario(s) through Hermes ({args.hermes}).")
    print("This is isolated: nothing is posted to Gorgias.\n")

    results = []
    for i, s in enumerate(scenarios, 1):
        prompt = PROMPT_TEMPLATE.format(
            subject=s.get("subject", ""),
            email=s.get("email", ""),
            message=s.get("message", ""),
        )
        print(f"[{i}/{len(scenarios)}] {s['id']} — {s.get('subject','')} ...", flush=True)
        output, secs = run_one(args.hermes, prompt, args.timeout)
        results.append({
            "id": s["id"],
            "cat": s.get("cat", ""),
            "intent": s.get("intent", ""),
            "subject": s.get("subject", ""),
            "email": s.get("email", ""),
            "message": s.get("message", ""),
            "expect": s.get("expect", ""),
            "hermes_output": output,
            "secs": secs,
        })
        # write after each one so a crash/timeout never loses progress
        with open(OUT, "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"      done in {secs}s")
        time.sleep(args.sleep)

    print(f"\nSaved {len(results)} results to {OUT}")
    print("Next: send results-live.json back to Claude and say 'judge these'.")

if __name__ == "__main__":
    main()
