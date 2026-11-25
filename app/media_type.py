"""Identify original audio by its container, including incorrectly named imports."""
import os


def audio_mime(audio, fallback):
    """Read a bounded header without changing the caller's file cursor.

    Skip a valid ID3v2 tag by seeking, so embedded artwork is never loaded.
    Unknown formats retain the catalog type; no subprocess or transcoding.
    """
    position = audio.tell()
    try:
        audio.seek(0)
        head = audio.read(12)
        if head.startswith(b'fLaC'): return 'audio/flac'
        if head[4:8] == b'ftyp': return 'audio/mp4'
        if head[:4] == b'RIFF' and head[8:12] == b'WAVE': return 'audio/wav'
        if head[:4] == b'FORM' and head[8:12] in (b'AIFF', b'AIFC'): return 'audio/aiff'
        if head.startswith(b'OggS'): return 'audio/ogg'
        if head.startswith(b'ID3'):
            if len(head) < 10 or head[3] not in (2, 3, 4) or any(v & 128 for v in head[6:10]): return fallback
            size = 0
            for value in head[6:10]: size = (size << 7) | value
            offset = 10 + size + (10 if head[3] == 4 and head[5] & 16 else 0)
            if offset + 4 > os.fstat(audio.fileno()).st_size: return fallback
            audio.seek(offset)
            head = audio.read(4)
        if len(head) >= 4 and head[0] == 255:
            # MPEG audio: sync, non-reserved version/layer, bitrate and sample rate.
            if (head[1] & 224 == 224 and head[1] & 24 != 8 and head[1] & 6 != 0
                    and head[2] >> 4 not in (0, 15) and head[2] & 12 != 12):
                return 'audio/mpeg'
            if head[1] & 246 == 240 and head[2] & 60 != 60: return 'audio/aac'
        return fallback
    finally:
        audio.seek(position)


def original_mime(filename, fallback):
    with open(filename, 'rb') as audio:
        return audio_mime(audio, fallback)
