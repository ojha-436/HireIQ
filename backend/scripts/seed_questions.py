"""Seed the fallback interview question bank from O*NET 31.0.

    python scripts/seed_questions.py              # deterministic, instant, offline
    python scripts/seed_questions.py --rewrite    # + Gemini wording pass (better, slower)

The bank is a FALLBACK. The adaptive path — a question generated from what the candidate
just said — always wins; this exists so a lull never becomes a silence. That is why the
deterministic frames are acceptable even though their grammar is rougher than the
rewritten ones: they are the safety net under the safety net.

Every row stores `source = "onet:<Task ID>"`. O*NET 31.0, USDOL/ETA, CC BY 4.0 —
see data/onet/ATTRIBUTION.md.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import InterviewQuestion  # noqa: E402
from app.services import onet  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rewrite", action="store_true",
                    help="use Gemini to phrase each question (needs GEMINI_API_KEY)")
    ap.add_argument("--per-skill", type=int, default=4,
                    help="O*NET tasks per skill (x5 difficulty bands)")
    ap.add_argument("--reset", action="store_true", help="delete the existing bank first")
    ap.add_argument("--workers", type=int, default=8,
                    help="parallel rewrite calls (higher trips the API rate limit)")
    args = ap.parse_args()

    if not onet.available():
        print("O*NET data is missing. Run: python data/onet/fetch.py", file=sys.stderr)
        return 1

    rows = onet.build_questions(limit_per_skill=args.per_skill)
    print(f"derived {len(rows)} questions from O*NET across "
          f"{len({r['skill_id'] for r in rows})} skills")

    if args.rewrite:
        from app.interview import gemini as GEM
        if not GEM.available():
            print("  --rewrite asked for but no model is reachable; keeping frames",
                  file=sys.stderr)
        else:
            import csv
            from concurrent.futures import ThreadPoolExecutor, as_completed

            tasks = {r["Task ID"]: r["Task"] for r in csv.DictReader(
                onet.TASKS_CSV.open(newline="", encoding="utf-8-sig"))}

            def rewrite_one(index: int) -> tuple[int, str | None]:
                row = rows[index]
                task = tasks.get(row["source"].split(":", 1)[1])
                if not task:
                    return index, None
                return index, onet.rewrite_question(task, row["difficulty"])

            # Modest concurrency. Sequentially this is 10-20 minutes; unbounded it trips
            # the API's per-minute limit and every call starts failing, which is worse
            # than slow. A failure keeps the deterministic frame rather than dropping
            # the row, so a partial run still leaves a usable bank.
            done, kept = 0, 0
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = [pool.submit(rewrite_one, i) for i in range(len(rows))]
                for n, fut in enumerate(as_completed(futures), 1):
                    index, better = fut.result()
                    if better:
                        rows[index]["question"] = better
                        done += 1
                    else:
                        kept += 1
                    if n % 80 == 0 or n == len(futures):
                        print(f"  {n}/{len(futures)} — rewrote {done}, kept {kept} frames",
                              flush=True)
            print(f"  rewrote {done}, kept {kept} deterministic frames")

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if args.reset:
            deleted = db.query(InterviewQuestion).delete()
            db.commit()
            print(f"  cleared {deleted} existing rows")

        existing = {(q.skill_id, q.source, q.difficulty)
                    for q in db.query(InterviewQuestion).all()}
        added = 0
        for row in rows:
            key = (row["skill_id"], row["source"], row["difficulty"])
            if key in existing:
                continue
            db.add(InterviewQuestion(**row))
            added += 1
        db.commit()

        total = db.query(InterviewQuestion).count()
        print(f"\nbank: +{added} added, {total} rows total")
        for persona in ("tech", "product", "hiring_manager", "customer", "behavioural"):
            n = db.query(InterviewQuestion).filter(
                InterviewQuestion.persona == persona).count()
            print(f"  {persona:16} {n:4} questions")
        sample = db.query(InterviewQuestion).filter(
            InterviewQuestion.persona == "tech",
            InterviewQuestion.difficulty == 3).first()
        if sample:
            print(f"\nsample (tech, L3, {sample.source}):\n  {sample.question}")
        print("\nO*NET 31.0 — USDOL/ETA, CC BY 4.0. See data/onet/ATTRIBUTION.md.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
