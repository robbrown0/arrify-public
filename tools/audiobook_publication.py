"""Publish a verified, ordered chapter edition without changing its recordings.

The producer supplies source identities, never title-based grouping. The resulting
directory is a normal Audiobookshelf book, separate from production previews.
"""
import fcntl
import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def publish_chapters(source_id, entries, root):
    if not isinstance(source_id, str) or not re.fullmatch('[a-f0-9]{64}', source_id):
        raise ValueError('A verified source-book identity is required')
    if not entries:
        raise ValueError('No reviewed chapters are published')
    indexed = {}
    totals = set()
    for entry in entries:
        track, total = entry.get('track'), entry.get('track_total')
        if (entry.get('source_upload_id') != source_id or type(track) is not int
                or type(total) is not int or not 1 <= track <= total <= 5000):
            raise ValueError('Chapter identity or order does not match the source book')
        if track in indexed:
            raise ValueError('Multiple publications claim the same source chapter')
        path = Path(entry['path'])
        if path.is_symlink() or not path.is_file() or path.suffix.lower() != '.m4b' or not path.stat().st_size:
            raise ValueError('A reviewed M4B chapter is unavailable')
        indexed[track] = {**entry, 'path': path}
        totals.add(total)
    if len(totals) != 1:
        raise ValueError('Chapter publications describe different source editions')
    # Never jump past a missing chapter. Later available chapters wait until the
    # contiguous source prefix is ready; no narration or regeneration is invoked.
    prefix = []
    while len(prefix) + 1 in indexed:
        prefix.append(indexed[len(prefix) + 1])
    if not prefix:
        raise ValueError('The opening source chapter must be available first')
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = root / source_id
    with (root / ('.' + source_id + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        existing = destination.exists()
        if destination.is_symlink():
            raise ValueError('Publication destination must be a regular directory')
        names = {f'{entry["track"]:04d}.m4b' for entry in prefix}
        if existing and {p.name for p in destination.glob('*.m4b')} - names:
            raise ValueError('An existing edition would lose chapters; manual review required')
        staging = destination if existing else Path(tempfile.mkdtemp(prefix='.' + source_id + '-', dir=root))
        receipt = []
        try:
            for entry in prefix:
                source = entry['path']
                checksum = digest(source)
                target = staging / f'{entry["track"]:04d}.m4b'
                if target.is_symlink():
                    raise ValueError('Publication files must not be symbolic links')
                if not target.exists() or digest(target) != checksum:
                    fd, temporary = tempfile.mkstemp(prefix='.' + target.name + '-', dir=staging)
                    os.close(fd)
                    try:
                        shutil.copyfile(source, temporary)
                        if digest(temporary) != checksum or digest(source) != checksum:
                            raise ValueError('Chapter changed during publication; retry after production is idle')
                        os.replace(temporary, target)
                    finally:
                        Path(temporary).unlink(missing_ok=True)
                receipt.append({'track': entry['track'], 'title': entry['title'],
                                'filename': target.name, 'sha256': checksum})
            cover = Path(prefix[0]['cover'])
            target = staging / 'cover.jpg'
            if target.is_symlink():
                raise ValueError('Publication files must not be symbolic links')
            if cover.is_symlink() or not cover.is_file():
                raise ValueError('The reviewed book cover is unavailable')
            checksum = digest(cover)
            if not target.exists() or digest(target) != checksum:
                fd, temporary = tempfile.mkstemp(prefix='.cover-', dir=staging)
                os.close(fd)
                try:
                    shutil.copyfile(cover, temporary)
                    if digest(temporary) != checksum or digest(cover) != checksum:
                        raise ValueError('Book cover changed during publication')
                    os.replace(temporary, target)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            if not existing:
                staging.rename(destination)
        finally:
            if not existing and staging.exists():
                shutil.rmtree(staging)
    return {'source_id': source_id, 'path': str(destination), 'chapters': receipt,
            'available': len(prefix), 'total': totals.pop(), 'waiting_tracks': sorted(set(indexed) - set(range(1, len(prefix) + 1)))}
