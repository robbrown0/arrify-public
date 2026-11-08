#!/usr/bin/env python3
"""Publish a transactionally consistent, read-only catalog snapshot for helpers."""
import fcntl,os,sqlite3
from pathlib import Path
source=Path(os.environ.get('ARRIFY_CATALOG_SOURCE','/srv/arrify-example/arrify/catalog.db'))
target=Path(os.environ.get('ARRIFY_CATALOG_TARGET','/srv/arrify-example/existing-catalog/music.db'))
with open('/run/lock/arrify-channel-catalog.lock','a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 temporary=target.with_suffix('.refresh.db')
 temporary.unlink(missing_ok=True)
 try:
  src=sqlite3.connect('file:'+str(source)+'?mode=ro',uri=True,timeout=10)
  dst=sqlite3.connect(temporary)
  try:src.backup(dst,pages=256,sleep=.05)
  finally:dst.close();src.close()
  os.chown(temporary,21012,21012);os.chmod(temporary,0o600)
  with temporary.open('rb') as f:os.fsync(f.fileno())
  os.replace(temporary,target)
  fd=os.open(target.parent,os.O_DIRECTORY)
  try:os.fsync(fd)
  finally:os.close(fd)
 finally:temporary.unlink(missing_ok=True)
