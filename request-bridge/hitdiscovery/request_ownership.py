"""Reuse request ownership matching only for an identical inventory snapshot."""
import json
import sys
import threading

from .matching import Ownership


class RequestOwnership:
    def __init__(self, max_snapshot_bytes=64*1024*1024):
        self.max_snapshot_bytes=max_snapshot_bytes
        self._lock=threading.Lock()
        self._payloads=None
        self._ownership=None

    def get(self, store):
        with self._lock:
            # Read the inventory on every check: unrelated DB writes do not
            # invalidate it, while imports, removals and tag edits are visible
            # immediately. A single SELECT gives a consistent SQLite snapshot.
            # Compare exact payloads, not a TTL/count/mtime approximation.
            # Inventory scans replace rows while refreshing their seen stamp;
            # stable path order keeps that bookkeeping from forcing a rebuild.
            payloads=tuple(row[0] for row in store.db.execute('SELECT payload FROM files ORDER BY path'))
            if payloads==self._payloads:
                return self._ownership
            ownership=Ownership(json.loads(payload) for payload in payloads)
            size=sys.getsizeof(payloads)+sum(sys.getsizeof(payload) for payload in payloads)
            if size<=self.max_snapshot_bytes:
                self._payloads,self._ownership=payloads,ownership
            else:
                # Large inventories retain the original uncached behavior.
                self._payloads=self._ownership=None
            return ownership
