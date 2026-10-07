"""Bounded file-level concurrency for local video composition.

Callbacks stay on the calling thread (Qt/task-center control), results retain
input order, and an error stops submission before temporary inputs are removed.
"""
import os
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait


def concat_workers(requested=None):
    if requested is not None:
        if isinstance(requested, bool) or requested not in (1, 2, 3):
            raise ValueError("batch_workers 必须是 1、2 或 3")
        return int(requested)
    return 2 if (os.cpu_count() or 1) >= 8 else 1


def reserve_output(path, reserved):
    """Reserve names before dispatch; filesystem existence alone races in a batch."""
    base, extension = os.path.splitext(path)
    candidate, suffix = path, 2
    while os.path.exists(candidate) or os.path.normcase(os.path.abspath(candidate)) in reserved:
        candidate = f"{base}_{suffix}{extension}"
        suffix += 1
    reserved.add(os.path.normcase(os.path.abspath(candidate)))
    return candidate


def bounded_results(pool, process, items, *, workers, should_stop=None):
    """Yield (input index, result), keeping at most workers futures in flight."""
    if workers < 1:
        raise ValueError("workers must be positive")
    iterator = iter(enumerate(items))
    pending = {}
    exhausted = False
    try:
        while pending or not exhausted:
            if should_stop and should_stop():
                return
            while not exhausted and len(pending) < workers:
                try:
                    index, item = next(iterator)
                except StopIteration:
                    exhausted = True
                    break
                pending[pool.submit(process, item)] = index
            if not pending:
                break
            done, _ = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
            for future in done:
                index = pending.pop(future)
                yield index, future.result()
    finally:
        for future in pending:
            future.cancel()


def ordered_batch(items, process, *, workers, callback=None, message="处理视频",
                  on_result=None, ffmpeg_threads=None):
    if workers < 1:
        raise ValueError("workers must be positive")
    items = list(items)
    total = len(items)
    results = [None] * total
    completed = 0
    next_index = 0
    pending = {}
    def execute(item):
        if ffmpeg_threads is None:
            return process(item)
        from core.ffmpeg_runner import ffmpeg_thread_budget
        with ffmpeg_thread_budget(ffmpeg_threads):
            return process(item)

    with ThreadPoolExecutor(max_workers=max(1, min(workers, total or 1))) as pool:
        try:
            while next_index < total or pending:
                if callback:
                    callback(completed, total, message, 0)
                while next_index < total and len(pending) < workers:
                    pending[pool.submit(execute, items[next_index])] = next_index
                    next_index += 1
                done, _ = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
                # Inspect every completed future before submitting more work.
                for future in done:
                    result = future.result()
                    results[pending.pop(future)] = result
                    completed += 1
                    if on_result:
                        on_result(result)
                if done and callback:
                    callback(completed, total, message, 100)
        finally:
            for future in pending:
                future.cancel()
            # Running files finish before the caller's temp directory is removed.
    return results
