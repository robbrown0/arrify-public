"""Bounded embedded and sidecar artwork for local audio files."""
from __future__ import annotations
import os
from tinytag import TinyTag, TinyTagException

MAX_IMAGE_BYTES = 5 * 1024 * 1024
SIDECAR_NAMES = (
    "cover.jpg", "cover.jpeg", "cover.png", "cover.webp",
    "folder.jpg", "folder.jpeg", "folder.png", "folder.webp",
    "front.jpg", "front.jpeg", "front.png", "front.webp",
    "album.jpg", "album.jpeg", "album.png", "album.webp",
)


class ArtworkError(Exception):
    """A safe artwork lookup or validation failure."""


def image_type(data: bytes) -> str | None:
    """Return a browser-safe MIME type based only on trusted magic bytes."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return None


def validate_image(data: bytes) -> tuple[str, bytes]:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ArtworkError("artwork is empty or exceeds the 5 MB limit")
    mime_type = image_type(data)
    if mime_type is None:
        raise ArtworkError("artwork is not a supported JPEG, PNG, WebP, or GIF image")
    return mime_type, data


def _read_bounded(filename: str) -> tuple[str, bytes]:
    try:
        size = os.stat(filename, follow_symlinks=False).st_size
        if size <= 0 or size > MAX_IMAGE_BYTES or os.path.islink(filename):
            raise ArtworkError("artwork file is empty, too large, or unsafe")
        with open(filename, "rb") as stream:
            return validate_image(stream.read(MAX_IMAGE_BYTES + 1))
    except OSError as exc:
        raise ArtworkError("artwork file is unavailable") from exc


def local_artwork(audio_filename: str) -> tuple[str, bytes, str] | None:
    """Read embedded art first, then conventional album-folder sidecars."""
    try:
        tag = TinyTag.get(
            audio_filename, tags=True, duration=False, image=True,
            check_magic_bytes=True,
        )
        image = tag.images.front_cover or tag.images.any
        if image is not None:
            mime_type, data = validate_image(image.data)
            return mime_type, data, "embedded"
    except (ArtworkError, OSError, TinyTagException, ValueError):
        pass

    directory = os.path.realpath(os.path.dirname(audio_filename))
    try:
        names = {entry.name.casefold(): entry.name for entry in os.scandir(directory)
                 if entry.is_file(follow_symlinks=False)}
    except OSError:
        return None
    for wanted in SIDECAR_NAMES:
        actual = names.get(wanted)
        if actual is None:
            continue
        candidate = os.path.realpath(os.path.join(directory, actual))
        if os.path.commonpath((directory, candidate)) != directory:
            continue
        try:
            mime_type, data = _read_bounded(candidate)
            return mime_type, data, "folder"
        except ArtworkError:
            continue
    return None

