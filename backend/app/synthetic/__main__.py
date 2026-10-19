"""python -m app.synthetic --count 300 --seed 42"""

import argparse
import sys
import time
from pathlib import Path

from app.synthetic.generate import Options, generate

DEFAULT_OUT = Path(__file__).resolve().parents[2] / "synthetic_data"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.synthetic", description="Generate synthetic claim files")
    parser.add_argument("--count", type=int, default=300, help="Number of claims (default 300)")
    parser.add_argument("--seed", type=int, default=42, help="Same seed gives the same files")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"Output folder (default {DEFAULT_OUT})")
    parser.add_argument("--genuine-share", type=float, default=0.4, help="Share of claims with no issue")
    parser.add_argument("--scanned-share", type=float, default=0.3, help="Share of claims that also get a scan")
    args = parser.parse_args(argv)
    if args.count < 1:
        parser.error("--count must be at least 1")

    started = time.time()

    def progress(done: int, total: int) -> None:
        if done == total or done % 25 == 0:
            print(f"  {done}/{total} claims", flush=True)

    summary = generate(Options(out_dir=args.out, count=args.count, seed=args.seed,
                               genuine_share=args.genuine_share, scanned_share=args.scanned_share), progress)
    print(f"Done in {time.time() - started:.0f}s -> {args.out}")
    print(f"  claims {summary['claims']}, genuine {summary['genuine']}, scanned {summary['scanned']}")
    print(f"  expected bands: {summary['bands']}")
    if summary["checks_without_cases"]:
        print(f"  WARNING: no seeded case for {', '.join(summary['checks_without_cases'])}", file=sys.stderr)
    else:
        print(f"  every one of the {len(summary['seeded_per_check'])} checks has seeded cases "
              f"(min {min(summary['seeded_per_check'].values())} each)")


if __name__ == "__main__":
    main()