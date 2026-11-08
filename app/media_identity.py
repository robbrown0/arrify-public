"""Filesystem representation fingerprint; contains no exposed paths."""
import hashlib
import json
import os


def source_revision(filename, stat=None):
    stat = stat if stat is not None else os.stat(filename)
    return hashlib.sha256(json.dumps([str(filename), stat.st_dev, stat.st_ino,
        stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]).encode()).hexdigest()
