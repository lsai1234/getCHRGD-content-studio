"""Bounded parallelism for the engine's slow, independent calls.

Almost everything that makes the studio feel slow is waiting on somebody
else's server: a 20-60s image generation, a 30-90s write, a judge verdict.
The work is I/O, not CPU, so threads are exactly the right tool — the GIL is
released for the whole of a network round trip, and the alternative (an async
rewrite of every call site) buys nothing a thread pool doesn't.

Two rules the helpers here enforce, because getting them wrong is how
concurrency turns into a bug report rather than a speed-up:

* **Order is preserved.** Results come back in the order the work was
  submitted, never in completion order, so a parallel render still writes
  slide 3 into position 3.
* **The pool is bounded and short-lived.** Each call gets its own pool sized
  to the work, and it is closed before the function returns. Nothing here
  keeps a thread alive between jobs.

Callbacks handed to these helpers run ON the worker threads. Anything they
touch (a progress counter, a job row, a `notify` that writes to SQLite) needs
its own lock — see `render_carousel` for the pattern.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")

#: Nothing in this app benefits from more parallelism than this, and a runaway
#: setting would fan out into rate limits (and a bigger bill per minute) rather
#: than more speed. Every worker count is clamped to it.
MAX_WORKERS = 8


def workers_for(requested: int, items: int) -> int:
    """How many threads to actually use: never more than there is work."""
    return max(1, min(int(requested or 1), MAX_WORKERS, max(1, items)))


def run_all(
    calls: "Iterable[Callable[[], T]]", *, workers: int, name: str = "chrgd-par"
) -> list[T]:
    """Run zero-arg callables concurrently; return results in submission order.

    The first exception raised by any call propagates once every other call has
    settled — we never leave a paid API call running into the void behind a
    failure it knows nothing about. Calls that hadn't started when the failure
    happened are cancelled.
    """
    todo = list(calls)
    if not todo:
        return []
    if len(todo) == 1:
        return [todo[0]()]

    n = workers_for(workers, len(todo))
    if n == 1:
        return [call() for call in todo]

    with ThreadPoolExecutor(max_workers=n, thread_name_prefix=name) as pool:
        futures: list[Future] = [pool.submit(call) for call in todo]
        # Wait for everything before raising, so a failure in the first call
        # doesn't strand the others mid-flight.
        error: BaseException | None = None
        results: list[T] = []
        for fut in futures:
            try:
                results.append(fut.result())
            except BaseException as exc:  # noqa: BLE001 - re-raised below
                if error is None:
                    error = exc
                results.append(None)  # type: ignore[arg-type]
    if error is not None:
        raise error
    return results


def parallel_map(
    fn: "Callable[[T], object]",
    items: "Iterable[T]",
    *,
    workers: int,
    name: str = "chrgd-par",
) -> list:
    """`[fn(x) for x in items]`, with up to `workers` of them in flight."""
    return run_all(
        [(lambda x=item: fn(x)) for item in items], workers=workers, name=name
    )
