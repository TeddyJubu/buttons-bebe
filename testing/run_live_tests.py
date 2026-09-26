#!/usr/bin/env python3
"""Run exactly the production draft path in an isolated, synthetic QA profile."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
from qa_harness import Harness, atomic_json
from qa_safety import scenario_fixture


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes",type=Path,required=True,help="Underlying Hermes executable, not a production shell wrapper")
    parser.add_argument("--hermes-python",type=Path,required=True)
    parser.add_argument("--hermes-source",type=Path,required=True)
    parser.add_argument("--model-config",type=Path,required=True,help="Private model-only JSON; never pass the production profile")
    parser.add_argument("--output",type=Path,required=True,help="New private run directory outside the live application")
    parser.add_argument("--product-manifest",type=Path)
    parser.add_argument("--product-manifest-sha256")
    parser.add_argument('--policy-overlay', type=Path, help='Reviewed proposed KB snapshot; no live publication')
    parser.add_argument('--policy-overlay-sha256')
    parser.add_argument("--kb-mode",choices=("fixture","policies-only"),default="fixture")
    parser.add_argument("--base-port",type=int,default=18877)
    parser.add_argument("--timeout",type=int,default=180)
    parser.add_argument("--limit",type=int,default=0)
    parser.add_argument("--ids",default="")
    parser.add_argument('--suite',choices=('core','reliability'),default='core')
    args=parser.parse_args()
    filename,expected=('scenarios.json',48) if args.suite=='core' else ('reliability-scenarios.json',10)
    scenarios=json.loads((Path(__file__).parent/filename).read_text())
    if len(scenarios)!=expected or len({s["id"] for s in scenarios})!=expected:
        parser.error(f'Scenario catalog must have exactly {expected} unique IDs')
    indexed=list(enumerate(scenarios,1))
    if args.ids:
        wanted=set(args.ids.split(","))
        if not wanted.issubset({s["id"] for s in scenarios}):parser.error("Unknown scenario ID")
        indexed=[(i,s) for i,s in indexed if s["id"] in wanted]
    if args.limit:indexed=indexed[:args.limit]
    if not indexed or not 10<=args.timeout<=600 or not 1024<=args.base_port<=65532:
        parser.error("Invalid run bounds")
    if args.output.exists():
        parser.error("Use a new output directory; prior evidence is never overwritten")
    harness=None
    try:
        harness=Harness(output=args.output,model_config=args.model_config,hermes=args.hermes,hermes_python=args.hermes_python,
                        hermes_source=args.hermes_source,kb_mode=args.kb_mode,timeout=args.timeout,base_port=args.base_port,
                        product_manifest=args.product_manifest,product_manifest_sha256=args.product_manifest_sha256,
                        policy_overlay=args.policy_overlay,policy_overlay_sha256=args.policy_overlay_sha256)
        harness.start(scenario_fixture(indexed[0][1],indexed[0][0]))
        results=[]
        for ordinal,scenario in indexed:
            print(f"QA {scenario['id']} ({len(results)+1}/{len(indexed)})",flush=True)
            results.append(harness.run(scenario,ordinal))
            atomic_json(harness.output/"results.json",results)
        print(f"Captured {len(results)} synthetic cases; human grading remains required.")
        return 0
    except Exception as exc:
        # Deliberately omit exception text: transport/provider errors may contain
        # auth material. All expected failure reasons are reflected in receipts.
        print(f"QA stopped safely ({type(exc).__name__}); inspect private receipts.",file=sys.stderr)
        return 1
    finally:
        if harness:harness.close()


if __name__=="__main__":
    raise SystemExit(main())
