"""Assign test numbers to workers.

The starter scheduler is the one the published baseline uses. The others
exist so a later agent can compare them on the same selection. The scorer
charges the sum of recorded test seconds, so splitting a file across
workers has no extra cost in this contest.
"""

from __future__ import annotations

MAX_WORKERS = 8


def _cap(worker_count: int) -> int:
    if worker_count < 1:
        return 1
    if worker_count > MAX_WORKERS:
        return MAX_WORKERS
    return worker_count


def split_starter(tests: list[dict], worker_count: int) -> list[list[int]]:
    """Official starter: longest test first, onto the lightest worker, in seconds."""
    worker_count = _cap(worker_count)
    workers: list[list[int]] = [[] for _ in range(worker_count)]
    loads = [0.0] * worker_count
    for test in sorted(tests, key=lambda item: item["seconds"], reverse=True):
        lightest = loads.index(min(loads))
        workers[lightest].append(test["number"])
        loads[lightest] += test["seconds"]
    return [worker for worker in workers if worker]


def assign(
    numbers: list[int],
    tests_by_number: dict[int, dict],
    durations: dict[int, int],
    worker_count: int,
    method: str,
) -> list[list[int]]:
    """Return occupied workers. Empty input returns no workers, which scores 0."""
    if not numbers:
        return []
    worker_count = _cap(worker_count)
    if method == "starter":
        chosen = [tests_by_number[number] for number in numbers]
        planned = split_starter(chosen, worker_count)
        return _drop_duplicates(planned)
    if method == "round_robin":
        return _round_robin(numbers, durations, worker_count)
    if method == "pack_files":
        return _pack_files(numbers, tests_by_number, durations, worker_count)
    if method == "lpt_ms":
        return _lpt(numbers, durations, worker_count)
    if method == "auto":
        return _auto(numbers, durations)
    raise ValueError(f"unknown scheduler {method}")


def _auto(numbers: list[int], durations: dict[int, int]) -> list[list[int]]:
    """Pick a worker count from 1 to 8 that minimizes makespan plus 2s per worker."""
    best: list[list[int]] | None = None
    best_cost: int | None = None
    for worker_count in range(1, MAX_WORKERS + 1):
        workers = _lpt(numbers, durations, worker_count)
        cost = _predicted_ms(workers, durations)
        if best_cost is None or cost < best_cost:
            best = workers
            best_cost = cost
    return best or []


def _predicted_ms(workers: list[list[int]], durations: dict[int, int]) -> int:
    if not workers:
        return 0
    loads = [sum(durations[number] for number in worker) for worker in workers]
    return max(loads) + 2000 * len(workers)


def _lpt(numbers: list[int], durations: dict[int, int], worker_count: int) -> list[list[int]]:
    workers: list[list[int]] = [[] for _ in range(worker_count)]
    loads = [0] * worker_count
    for number in sorted(numbers, key=lambda item: durations[item], reverse=True):
        lightest = loads.index(min(loads))
        workers[lightest].append(number)
        loads[lightest] += durations[number]
    return [worker for worker in workers if worker]


def _round_robin(numbers: list[int], durations: dict[int, int], worker_count: int) -> list[list[int]]:
    workers: list[list[int]] = [[] for _ in range(worker_count)]
    ordered = sorted(numbers, key=lambda item: durations[item], reverse=True)
    for index, number in enumerate(ordered):
        workers[index % worker_count].append(number)
    return [worker for worker in workers if worker]


def _pack_files(
    numbers: list[int],
    tests_by_number: dict[int, dict],
    durations: dict[int, int],
    worker_count: int,
) -> list[list[int]]:
    """Keep each test file on one worker, then place the heaviest files first."""
    groups: dict[str, list[int]] = {}
    for number in numbers:
        groups.setdefault(tests_by_number[number]["file"], []).append(number)
    ordered = sorted(
        groups.values(),
        key=lambda group: sum(durations[number] for number in group),
        reverse=True,
    )
    workers: list[list[int]] = [[] for _ in range(worker_count)]
    loads = [0] * worker_count
    for group in ordered:
        lightest = loads.index(min(loads))
        workers[lightest].extend(group)
        loads[lightest] += sum(durations[number] for number in group)
    return [worker for worker in workers if worker]


def _drop_duplicates(workers: list[list[int]]) -> list[list[int]]:
    seen: set[int] = set()
    cleaned: list[list[int]] = []
    for worker in workers:
        kept = []
        for number in worker:
            if number in seen:
                continue
            seen.add(number)
            kept.append(number)
        if kept:
            cleaned.append(kept)
    return cleaned
