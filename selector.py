"""Contest entry point.

Default behavior is the official starter, preserved in starter/selector.py:
every test, split across 3 workers. Other strategies stay off unless
BLAST_STRATEGY or BLAST_SCHEDULER is set. Logs go to stderr.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from blast.engine import answer_lines, plan_batch
from blast.logutil import log


def _use_starter() -> bool:
    """The preserved starter runs only when every switch is put back to its original."""
    return (
        os.environ.get("BLAST_STRATEGY", "imports") == "all"
        and os.environ.get("BLAST_SCHEDULER", "auto") == "starter"
        and os.environ.get("BLAST_WORKERS", "4") == "3"
        and os.environ.get("BLAST_DATA", "") == ""
    )


def main() -> None:
    if _use_starter():
        from starter.selector import main as starter_main

        starter_main()
        return

    data_dir = Path(os.environ.get("BLAST_DATA", "/data"))
    strategy = os.environ.get("BLAST_STRATEGY", "imports")
    scheduler = os.environ.get("BLAST_SCHEDULER", "auto")
    worker_count = int(os.environ.get("BLAST_WORKERS", "4"))
    try:
        plans = plan_batch(data_dir, strategy, scheduler, worker_count)
    except Exception as error:
        log("fatal", error=str(error))
        raise
    sys.stdout.write(answer_lines(plans))
    for plan in plans:
        log(
            "commit",
            commit=plan.commit,
            strategy=strategy,
            scheduler=scheduler,
            workers=len(plan.workers),
            selected=len(plan.selected),
            fallback=plan.fallback,
            comment_only=plan.comment_only,
        )


if __name__ == "__main__":
    main()
