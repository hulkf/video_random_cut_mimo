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


def ordered_batch(items, process, *, workers, callback=None, message="处理视频"):
    items = list(items)
    total = len(items)
    results = [None] * total
    completed = 0
    next_index = 0
    pending = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, total or 1))) as pool:
        try:
            while next_index < total or pending:
                if callback:
                    callback(completed, total, message, 0)
                while next_index < total and len(pending) < workers:
                    pending[pool.submit(process, items[next_index])] = next_index
                    next_index += 1
                done, _ = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
                # Inspect every completed future before submitting more work.
                for future in done:
                    results[pending.pop(future)] = future.result()
                    completed += 1
                if done and callback:
                    callback(completed, total, message, 100)
        finally:
            for future in pending:
                future.cancel()
            # Running files finish before the caller's temp directory is removed.
    return results
