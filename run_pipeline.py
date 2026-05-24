"""
Strived RFP Intelligence Pipeline — Master Orchestrator

Usage:
    python run_pipeline.py --all          # Run all phases (1-5)
    python run_pipeline.py --phase 1      # Run specific phase
    python run_pipeline.py --phase 5      # Build vector DB from existing data
    python run_pipeline.py --phase 6      # Interactive RAG query
    python run_pipeline.py --from-existing # Run Phase 5+6 from STRIVED_ALL_VERIFIED_RFPS_WITH_EMAILS.csv
"""

import argparse
import time


def run_phase(phase_num, from_existing=False, **kwargs):
    print(f"\n{'='*60}")
    print(f"  PHASE {phase_num}")
    print(f"{'='*60}\n")

    start = time.time()

    if phase_num == 1:
        from phase1_search import main as phase1_main
        result = phase1_main()
        print(f"\nPhase 1 found {result} RFP candidates")

    elif phase_num == 2:
        from phase2_verify import main as phase2_main
        result = phase2_main()
        print(f"\nPhase 2 found {result} Strived-relevant RFPs")

    elif phase_num == 3:
        from phase3_extract import main as phase3_main
        result = phase3_main()
        print(f"\nPhase 3: {result} RFPs now have emails")

    elif phase_num == 4:
        from phase4_email import main as phase4_main
        result = phase4_main()
        print(f"\nPhase 4 sent {result} emails")

    elif phase_num == 5:
        from phase5_vectordb import main as phase5_main
        if from_existing:
            from config import EXISTING_VERIFIED_CSV
            result = phase5_main(input_csv=EXISTING_VERIFIED_CSV)
        else:
            result = phase5_main()
        print(f"\nPhase 5 indexed {result} RFPs into vector DB")

    elif phase_num == 6:
        from phase6_rag import main as phase6_main
        phase6_main(query=kwargs.get("query"))
        return

    elapsed = time.time() - start
    print(f"\nPhase {phase_num} completed in {elapsed:.1f}s")


def main():
    parser = argparse.ArgumentParser(description="Strived RFP Intelligence Pipeline")
    parser.add_argument("--phase", type=int, choices=[1, 2, 3, 4, 5, 6], help="Run specific phase")
    parser.add_argument("--all", action="store_true", help="Run all phases (1-5)")
    parser.add_argument("--from-existing", action="store_true", help="Use existing verified CSV for Phase 5+6")
    parser.add_argument("--query", type=str, help="Single RAG query for Phase 6 (non-interactive)")
    args = parser.parse_args()

    print("=" * 60)
    print("  STRIVED RFP INTELLIGENCE PIPELINE")
    print("=" * 60)

    if args.all:
        for phase in [1, 2, 3, 4, 5]:
            run_phase(phase)
        print(f"\n{'='*60}")
        print("  ALL PHASES COMPLETE")
        print(f"{'='*60}")
        print("\nRun 'python run_pipeline.py --phase 6' for interactive RAG queries.")

    elif args.phase:
        run_phase(args.phase, from_existing=args.from_existing, query=args.query)

    elif args.from_existing:
        run_phase(5, from_existing=True)
        run_phase(6)

    else:
        parser.print_help()
        print("\nExamples:")
        print("  python run_pipeline.py --phase 5 --from-existing   # Index existing RFPs")
        print("  python run_pipeline.py --phase 6                   # Ask questions")
        print("  python run_pipeline.py --all                       # Full pipeline")


if __name__ == "__main__":
    main()
