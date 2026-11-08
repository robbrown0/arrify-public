"""Arrify catalog storage, scanning, playlist matching, and local artwork.

Playback sessions, authentication and HTTP transport belong to their dedicated
Arrify services; this module owns only catalog data and bounded file access.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import unicodedata
from collections import OrderedDict
from pathlib import Path
from tinytag import TinyTag, TinyTagException
from .artwork_local import local_artwork
from .catalog_artwork import FALLBACK
from .errors import CatalogError

def connect(path: str, *, check_same_thread: bool = True) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    db = sqlite3.connect(path, timeout=10, isolation_level=None,
                         check_same_thread=check_same_thread)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA trusted_schema=OFF")
    return db


DYNAMIC_ALL = "all"


ARTWORK_CACHE_BYTES = 32 * 1024 * 1024


SUPPORTED = {
    ".aac": "audio/aac",
    ".aif": "audio/aiff",
    ".aiff": "audio/aiff",
    ".alac": "audio/mp4",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
    # Catalog WMA so the UI can report it honestly. Browser playback support
    # varies and the web player labels it unsupported rather than transcoding.
    ".wma": "audio/x-ms-wma",
}


SKIP_DIRECTORIES = {"#recycle", "@recycle", ".trash", ".trashes"}


PLACEHOLDER_TAGS = {
    "artist", "album", "title", "track", "unknown", "unknown artist",
    "unknown album", "untitled",
}


SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  title TEXT NOT NULL,
  artist TEXT NOT NULL DEFAULT '',
  album TEXT NOT NULL DEFAULT '',
  extension TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  size INTEGER NOT NULL,
  modified_ns INTEGER NOT NULL,
  available INTEGER NOT NULL DEFAULT 1 CHECK(available IN (0,1)),
  UNIQUE(source,relative_path)
);
CREATE INDEX IF NOT EXISTS tracks_search
  ON tracks(available,title,artist,album);
CREATE TABLE IF NOT EXISTS track_metadata (
  track_id INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
  title TEXT NOT NULL DEFAULT '',
  artist TEXT NOT NULL DEFAULT '',
  album TEXT NOT NULL DEFAULT '',
  album_artist TEXT NOT NULL DEFAULT '',
  track_number TEXT NOT NULL DEFAULT '',
  disc_number TEXT NOT NULL DEFAULT '',
  year TEXT NOT NULL DEFAULT '',
  source_type TEXT NOT NULL CHECK(source_type IN ('embedded','reviewed')),
  confidence INTEGER NOT NULL CHECK(confidence BETWEEN 0 AND 100),
  file_modified_ns INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  updated_by TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS track_metadata_source
  ON track_metadata(source_type,confidence);
CREATE TABLE IF NOT EXISTS track_technical (
  track_id INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
  file_modified_ns INTEGER NOT NULL,
  bitrate_kbps INTEGER NOT NULL DEFAULT 0 CHECK(bitrate_kbps>=0),
  lossless INTEGER NOT NULL DEFAULT 0 CHECK(lossless IN (0,1)),
  codec TEXT NOT NULL DEFAULT '',
  analyzed_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS track_metadata_audit (
  id INTEGER PRIMARY KEY,
  track_id INTEGER NOT NULL,
  action TEXT NOT NULL,
  old_metadata_json TEXT NOT NULL,
  new_metadata_json TEXT NOT NULL,
  changed_at INTEGER NOT NULL,
  changed_by TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS track_metadata_audit_track
  ON track_metadata_audit(track_id,changed_at);
CREATE TABLE IF NOT EXISTS extra_sources (
  name TEXT PRIMARY KEY,
  relative_path TEXT NOT NULL UNIQUE,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS playlists (
  slug TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  position INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS playlist_tracks (
  playlist_slug TEXT NOT NULL REFERENCES playlists(slug) ON DELETE CASCADE,
  track_id INTEGER NOT NULL REFERENCES tracks(id),
  position INTEGER NOT NULL,
  PRIMARY KEY(playlist_slug,track_id)
);
CREATE INDEX IF NOT EXISTS playlist_tracks_order
  ON playlist_tracks(playlist_slug,position);
CREATE TABLE IF NOT EXISTS album_artwork (
  artwork_key TEXT PRIMARY KEY,
  artist TEXT NOT NULL,
  album TEXT NOT NULL,
  provider TEXT NOT NULL,
  external_id TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  image BLOB NOT NULL,
  image_sha256 TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  created_by TEXT NOT NULL
);
"""


def _safe_component(value: str, maximum: int = 160) -> str:
    """Render filesystem metadata as inert, bounded terminal/browser text."""
    cleaned = "".join(ch if 32 <= ord(ch) != 127 else "?" for ch in value)
    return cleaned[:maximum]


def _metadata(relative: str) -> tuple[str, str, str]:
    parts = Path(relative).parts
    title = _safe_component(Path(parts[-1]).stem)
    album = _safe_component(parts[-2]) if len(parts) >= 2 else ""
    artist = _safe_component(parts[-3]) if len(parts) >= 3 else ""
    return title or "Untitled", artist, album


def _music_key(value: str) -> str:
    """Normalize uneven directory-derived music metadata for curated matching."""
    ascii_value = unicodedata.normalize("NFKD", value).encode(
        "ascii", "ignore").decode("ascii").casefold()
    ascii_value = re.sub(
        r"^[a-z0-9]+_\(disc_\d+\)_\d+_-_", "", ascii_value)
    ascii_value = re.sub(r"^\s*\d{1,3}(?:-\d{1,3})?\s*[-_.]+\s*", "", ascii_value)
    ascii_value = re.sub(r"^\s*\d{1,3}\s+", "", ascii_value)
    return "".join(character for character in ascii_value if character.isalnum())


def _recording_versions(artist,title,album=''):
    text=title.casefold();album=album.casefold()
    result=set(re.findall(r'\b(?:live|acoustic|unplugged|remix|demo|tribute|karaoke|rerecorded)\b',text))
    if re.search(r'\b(?:acoustic|acoustical|unplugged)\b',album):result.add('acoustic')
    if re.search(r'\b(?:live|in concert)\b',album):result.add('live')
    if (_music_key(artist),_music_key(title))==('tesla','signs') and 'five man acoustical jam' in album:result.discard('acoustic')
    if (_music_key(artist),_music_key(title))==('nirvana','themanwhosoldtheworld') and 'mtv unplugged' in album:result.discard('acoustic')
    return result


def _display_title(value: str) -> str:
    """Hide filename track/disc prefixes without changing catalog identity."""
    original = _safe_component(value).strip()
    cleaned = original.replace("_", " ")
    verbose = re.fullmatch(
        r".+?\s+-\s+.+?\s+-\s+\(?\d{1,3}\)?\s+-\s+(.+)", cleaned,
    )
    if verbose:
        cleaned = verbose.group(1)
    cleaned = re.sub(r"^\s*\(disc\s+\d+\)\s*", "", cleaned,
                     count=1, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"^\s*\d{1,3}(?:(?:[._]\s*)|(?:-(?!\d)\s*))+", "", cleaned, count=1,
    )
    cleaned = re.sub(
        r"^\s*(?:(?:\d{1,2}-\d{1,2})(?:\s*[-_.]\s*|\s+)"
        r"|\d{1,3}(?:\s+|\s*[._]\s*|\s+-\s*|\s*-\s+))(?=\S)",
        "", cleaned, count=1,
    ).lstrip(" -.")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lstrip("-_. ")
    return cleaned or original or "Untitled"


def _display_artist(value: str) -> str:
    """Make directory-derived artist text readable without guessing identity."""
    cleaned = _safe_component(value).replace("_", " ")
    return re.sub(r"\s+", " ", cleaned).strip(" -_.")


def _display_album(value: str) -> str:
    """Remove filename separators and non-title explicit-content markers."""
    cleaned = _safe_component(value).replace("_", " ")
    cleaned = re.sub(r"\s*[\[(]\s*explicit\s*[\])]\s*", " ", cleaned,
                     flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip(" -_.")


def _tag_text(value: object, maximum: int = 160) -> str:
    """Normalize trusted tag text while preserving spelling and punctuation."""
    if value is None:
        return ""
    cleaned = unicodedata.normalize("NFC", str(value))
    cleaned = "".join(ch if 32 <= ord(ch) != 127 else " " for ch in cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()[:maximum]


CATALOG_COLUMNS = """
t.id,
CASE WHEN m.file_modified_ns=t.modified_ns AND m.title<>'' THEN m.title ELSE t.title END AS title,
CASE WHEN m.file_modified_ns=t.modified_ns AND m.artist<>'' THEN m.artist ELSE t.artist END AS artist,
CASE WHEN m.file_modified_ns=t.modified_ns AND m.album<>'' THEN m.album ELSE t.album END AS album,
t.title AS raw_title,t.artist AS raw_artist,t.album AS raw_album,
t.source,t.relative_path,t.extension,t.mime_type,t.size,t.modified_ns,t.available,
CASE WHEN m.file_modified_ns=t.modified_ns THEN m.album_artist ELSE '' END AS album_artist,
CASE WHEN m.file_modified_ns=t.modified_ns THEN m.track_number ELSE '' END AS track_number,
CASE WHEN m.file_modified_ns=t.modified_ns THEN m.disc_number ELSE '' END AS disc_number,
CASE WHEN m.file_modified_ns=t.modified_ns THEN m.year ELSE '' END AS year,
CASE WHEN m.file_modified_ns=t.modified_ns THEN m.source_type ELSE 'path' END AS metadata_source,
CASE WHEN m.file_modified_ns=t.modified_ns THEN m.confidence ELSE 25 END AS metadata_confidence
"""


CATALOG_JOIN = " FROM tracks t LEFT JOIN track_metadata m ON m.track_id=t.id "


def _public_row(row: sqlite3.Row | dict[str, object]) -> dict[str, object]:
    result = dict(row)
    result["title"] = _display_title(str(result.get("title", "")))
    result["artist"] = _display_artist(str(result.get("artist", "")))
    result["album"] = _display_album(str(result.get("album", "")))
    result.pop("relative_path", None)
    result.pop("modified_ns", None)
    result.pop("available", None)
    result.pop("raw_title", None)
    result.pop("raw_artist", None)
    result.pop("raw_album", None)
    return result


class Catalog:
    def __init__(self, db_path: str, sources: dict[str, str],
                 extra_root: str | None = None,
                 playlists_file: str | None = None):
        self.db = connect(db_path, check_same_thread=False)
        self.audio_exclusions_file = Path(db_path).parent / 'audio-exclusions.json'
        self.db.executescript(SCHEMA)
        self.base_sources = {name: os.path.realpath(path) for name, path in sources.items()}
        self.extra_root = os.path.realpath(extra_root) if extra_root else None
        self.lock = threading.RLock()
        self.scan_running = False
        self.metadata_running = False
        self.last_scan = 0
        self.last_scan_result: dict[str, object] = {"tracks": 0, "errors": []}
        self.last_metadata_result: dict[str, object] = {
            "examined": 0, "updated": 0, "skipped": 0, "errors": 0,
        }
        self.artwork_cache: OrderedDict[tuple[int, int], tuple[str, bytes, str]] = OrderedDict()
        self.artwork_cache_bytes = 0
        self.playlists_file = playlists_file
        self.playlist_definitions = self._load_playlists(playlists_file)
        self.dynamic_playlists = {
            str(item["slug"]): str(item.get("dynamic", ""))
            for item in self.playlist_definitions if item.get("dynamic")
        }
        self._refresh_playlists()

    @staticmethod
    def _load_playlists(filename: str | None) -> list[dict[str, object]]:
        if filename is None:
            return []
        try:
            with open(filename, encoding="utf-8") as stream:
                raw = json.load(stream)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("curated playlist file is unavailable or invalid") from exc
        if not isinstance(raw, list) or len(raw) > 50:
            raise RuntimeError("curated playlist file must contain a list")
        result: list[dict[str, object]] = []
        slugs: set[str] = set()
        for position, playlist in enumerate(raw):
            if not isinstance(playlist, dict):
                raise RuntimeError("curated playlist entry is invalid")
            slug = str(playlist.get("slug", ""))
            name = str(playlist.get("name", ""))
            description = str(playlist.get("description", ""))
            tracks = playlist.get("tracks")
            dynamic = str(playlist.get("dynamic", ""))
            section = str(playlist.get("section", "channels" if slug=="the-spectrum" else "playlists"))
            if section not in ("channels","playlists","moods"):
                raise RuntimeError("invalid collection section")
            if (not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", slug)
                    or slug in slugs or not 1 <= len(name) <= 80
                    or len(description) > 240 or dynamic not in ("", DYNAMIC_ALL)
                    or (dynamic and tracks not in (None, []))
                    or (not dynamic and (not isinstance(tracks, list)
                                         or not 1 <= len(tracks) <= 10000))):
                raise RuntimeError("curated playlist entry is invalid")
            clean_tracks: list[dict[str, object]] = []
            for track in tracks or []:
                if not isinstance(track, dict):
                    raise RuntimeError("curated playlist track is invalid")
                title = str(track.get("title", ""))
                artists = track.get("artists", [track.get("artist", "")])
                album = str(track.get("album", ""))
                if (not 1 <= len(title) <= 160 or not isinstance(artists, list)
                        or not 1 <= len(artists) <= 8 or len(album) > 160
                        or any(not isinstance(value, str) or not value or len(value) > 160
                               for value in artists)):
                    raise RuntimeError("curated playlist track is invalid")
                weight=track.get('weight',1)
                if isinstance(weight,bool) or not isinstance(weight,(int,float)) or not 0.1<=weight<=12:
                    raise RuntimeError("invalid channel rotation weight")
                clean_tracks.append({"title": title, "artists": artists, "album": album, "weight":float(weight)})
            result.append({"slug": slug, "name": name, "description": description,
                           "position": position, "tracks": clean_tracks,
                           "dynamic": dynamic, "section": section})
            slugs.add(slug)
        return result


    @staticmethod
    def _playlist_match(track: sqlite3.Row, wanted: dict[str, object]) -> int:
        keys = set(track.keys()) if isinstance(track, sqlite3.Row) else set(track)
        if {"raw_artist", "raw_title", "raw_album"}.issubset(keys):
            # Playlist manifests were audited against this immutable identity.
            # Canonical tags improve presentation but must never retarget a
            # curated entry to a different copy or recording.
            variants = [(str(track["raw_artist"]), str(track["raw_title"]),
                         str(track["raw_album"]))]
        else:
            variants = [(str(track["artist"]), str(track["title"]),
                         str(track["album"]))]
        wanted_artists = {_music_key(str(value)) for value in wanted["artists"]}
        wanted_title = _music_key(str(wanted["title"]))
        wanted_album = _music_key(str(wanted.get("album", "")))
        scores: list[int] = []
        for artist, title, album in variants:
            if _music_key(artist) not in wanted_artists:
                continue
            if not wanted.get('_allow_alternate') and _recording_versions(artist,_display_title(title),album) != _recording_versions(artist,str(wanted['title']),str(wanted.get('album',''))):
                continue
            presented_title = _display_title(title)
            title_key = _music_key(presented_title)
            if title_key == wanted_title or _music_key(title) == wanted_title:
                score = 100
            elif wanted_title in title_key:
                score = 70
            else:
                continue
            if wanted_album:
                album_key = _music_key(album)
                if album_key == wanted_album:
                    score += 30
                elif wanted_album in album_key or album_key in wanted_album:
                    score += 15
                else:
                    score -= 10
            lowered = presented_title.casefold()
            if any(word in lowered for word in
                   ("live", "demo", "tribute", "workout", "cover")):
                score -= 20
            scores.append(score)
        return max(scores, default=-1)


    def _refresh_playlists(self) -> None:
        """Resolve portable artist/title definitions against this host's catalog."""
        with self.lock:
            base = self._load_playlists(self.playlists_file)
            for channel_file in sorted(Path('/spectrum-channel').glob('*.json')):
                extra = self._load_playlists(str(channel_file))
                slugs = {p["slug"] for p in base}
                base.extend(p for p in extra if p["slug"] not in slugs)
                for position, entry in enumerate(base):
                    entry["position"] = position
            self.playlist_definitions = base
            available = self.db.execute(
                "SELECT " + CATALOG_COLUMNS + CATALOG_JOIN + "WHERE t.available=1"
            ).fetchall()
            exclusions=self._audio_exclusions()
            available=[r for r in available if exclusions.get((r['source'],r['relative_path'])) != (r['size'],r['modified_ns'])]
            by_artist: dict[str, list[sqlite3.Row]] = {}
            for row in available:
                raw_artist_key = _music_key(str(row["raw_artist"]))
                by_artist.setdefault(raw_artist_key, []).append(row)
            resolved: list[tuple[str, int, int]] = []
            channel_weights={}
            self.radio_artists={int(r['id']):_music_key(str(r['artist'])) for r in available}
            for playlist in self.playlist_definitions:
                if playlist.get("dynamic"):
                    continue
                used: set[int] = set()
                exact_identity=playlist.get('section')=='channels' or playlist['slug']=='jazz-for-sleep'
                for position, wanted in enumerate(playlist["tracks"]):
                    wanted=dict(wanted)
                    wanted['_allow_alternate']=('acoustic' in str(playlist['name']).casefold() or 'unplugged' in str(playlist['name']).casefold())
                    matching_rows: list[sqlite3.Row] = []
                    for artist in wanted["artists"]:
                        matching_rows.extend(by_artist.get(_music_key(str(artist)), []))
                    candidates = [((100 if _music_key(str(row["raw_title"])) ==
                                    _music_key(str(wanted["title"])) else -1)
                                   if exact_identity
                                   else self._playlist_match(row, wanted), int(row["id"]))
                                  for row in matching_rows]
                    candidates = [item for item in candidates
                                  if item[0] >= (100 if exact_identity else 0)
                                  and (not exact_identity or self._playlist_match(next(r for r in matching_rows if int(r['id'])==item[1]),wanted)>=0)
                                  and item[1] not in used]
                    if candidates:
                        lossless_ids={int(row['id']) for row in matching_rows
                                      if str(row['extension']).lower() in ('.flac','.alac','.wav','.aiff','.ape','.wv')}
                        _, track_id = max(candidates, key=lambda item: (item[0], item[1] in lossless_ids, -item[1]))
                        resolved.append((str(playlist["slug"]), track_id, position))
                        used.add(track_id)
                        if playlist.get('section')=='channels':
                            channel_weights.setdefault(str(playlist['slug']),{})[track_id]=float(wanted.get('weight',1))
            self.channel_weights=channel_weights
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute("DELETE FROM playlist_tracks")
                self.db.execute("DELETE FROM playlists")
                self.db.executemany(
                    "INSERT INTO playlists(slug,name,description,position) VALUES(?,?,?,?)",
                    [(item["slug"], item["name"], item["description"], item["position"])
                     for item in self.playlist_definitions])
                self.db.executemany(
                    "INSERT INTO playlist_tracks(playlist_slug,track_id,position) VALUES(?,?,?)",
                    resolved)
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise


    def _extra_path(self, relative: str, *, require_directory: bool = True) -> str:
        if self.extra_root is None:
            raise CatalogError("additional source root is not configured")
        candidate_path = Path(relative)
        if (not relative or candidate_path.is_absolute() or ".." in candidate_path.parts
                or relative in (".", os.curdir)):
            raise CatalogError("source location must be a relative directory")
        candidate = os.path.realpath(os.path.join(self.extra_root, relative))
        if os.path.commonpath((self.extra_root, candidate)) != self.extra_root:
            raise CatalogError("source location is outside the approved root")
        if require_directory and not os.path.isdir(candidate):
            raise CatalogError("source location is not available")
        return candidate


    def _all_sources(self) -> dict[str, str]:
        result = dict(self.base_sources)
        with self.lock:
            rows = self.db.execute(
                "SELECT name,relative_path FROM extra_sources ORDER BY name"
            ).fetchall()
        for row in rows:
            try:
                result[str(row["name"])] = self._extra_path(
                    str(row["relative_path"]), require_directory=False)
            except CatalogError:
                # Invalid legacy rows remain visible to administrators as
                # unavailable but can never escape the approved mount root.
                continue
        return result


    def refresh_metadata(self) -> dict[str, object]:
        """Read embedded tags without changing audio files or raw path metadata.

        Existing entries tied to the current file modification stamp are cheap
        to skip, so the first catalog campaign is comprehensive while later
        rescans only parse new or changed files. Administrator-reviewed rows
        are never silently replaced.
        """
        with self.lock:
            if self.metadata_running:
                return dict(self.last_metadata_result)
            self.metadata_running = True
            rows = self.db.execute(
                "SELECT t.id,t.source,t.relative_path,t.title,t.artist,t.album,t.modified_ns,"
                "m.source_type,m.file_modified_ns FROM tracks t "
                "LEFT JOIN track_metadata m ON m.track_id=t.id "
                "WHERE t.available=1 ORDER BY t.id"
            ).fetchall()
        sources = self._all_sources()
        examined = updated = skipped = errors = 0
        pending: list[tuple[object, ...]] = []

        def flush() -> None:
            nonlocal pending
            if not pending:
                return
            with self.lock:
                self.db.execute("BEGIN IMMEDIATE")
                try:
                    self.db.executemany(
                        "INSERT INTO track_metadata(track_id,title,artist,album,album_artist,"
                        "track_number,disc_number,year,source_type,confidence,file_modified_ns,"
                        "updated_at,updated_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(track_id) DO UPDATE SET title=excluded.title,"
                        "artist=excluded.artist,album=excluded.album,"
                        "album_artist=excluded.album_artist,track_number=excluded.track_number,"
                        "disc_number=excluded.disc_number,year=excluded.year,"
                        "source_type=excluded.source_type,confidence=excluded.confidence,"
                        "file_modified_ns=excluded.file_modified_ns,"
                        "updated_at=excluded.updated_at,updated_by=excluded.updated_by "
                        "WHERE track_metadata.source_type='embedded'",
                        pending,
                    )
                    self.db.execute("COMMIT")
                except BaseException:
                    self.db.execute("ROLLBACK")
                    raise
            pending = []

        try:
            for row in rows:
                examined += 1
                modified_ns = int(row["modified_ns"])
                if (row["file_modified_ns"] is not None
                        and int(row["file_modified_ns"]) == modified_ns):
                    skipped += 1
                    continue
                if row["source_type"] == "reviewed":
                    # A changed file invalidates the reviewed row via its stamp,
                    # but only an administrator may replace that decision.
                    skipped += 1
                    continue
                root = sources.get(str(row["source"]))
                if root is None:
                    errors += 1
                    continue
                filename = os.path.realpath(os.path.join(root, str(row["relative_path"])))
                if (os.path.commonpath((root, filename)) != root
                        or os.path.islink(filename)):
                    errors += 1
                    continue
                try:
                    tag = TinyTag.get(filename, image=False, duration=False)
                except (OSError, TinyTagException, ValueError):
                    errors += 1
                    continue
                title = _tag_text(tag.title)
                artist = _tag_text(tag.artist)
                album = _tag_text(tag.album)
                album_artist = _tag_text(tag.albumartist)
                if not any((title, artist, album, album_artist)):
                    skipped += 1
                    continue
                if any(value.casefold() in PLACEHOLDER_TAGS
                       for value in (title, artist, album) if value):
                    skipped += 1
                    continue
                title_matches = (_music_key(_display_title(str(row["title"]))) ==
                                 _music_key(_display_title(title))) if title else False
                artist_matches = (_music_key(str(row["artist"])) ==
                                  _music_key(artist)) if artist else False
                if title and artist and album:
                    confidence = 95 if title_matches and artist_matches else \
                        90 if title_matches or artist_matches else 80
                elif title and artist:
                    confidence = 85 if title_matches or artist_matches else 75
                else:
                    confidence = 65
                pending.append((
                    int(row["id"]), title, artist, album, album_artist,
                    _tag_text(tag.track, 24), _tag_text(tag.disc, 24),
                    _tag_text(tag.year, 24), "embedded", confidence, modified_ns,
                    int(time.time()), "tag-scan",
                ))
                updated += 1
                if len(pending) >= 250:
                    flush()
            flush()
            result = {"examined": examined, "updated": updated,
                      "skipped": skipped, "errors": errors}
            with self.lock:
                self.last_metadata_result = result
            return result
        finally:
            with self.lock:
                self.metadata_running = False


    def _audio_exclusions(self):
        try:
            rows=json.loads(self.audio_exclusions_file.read_text())
            return {(r['source'],r['relative_path']):(r['size'],r['modified_ns']) for r in rows}
        except (OSError,ValueError,KeyError,TypeError):return {}


    def scan(self, claimed: bool = False) -> dict[str, object]:
        if not claimed:
            with self.lock:
                if self.scan_running:
                    return {"started": False, "message": "catalog scan already running"}
                self.scan_running = True
        found_total = 0
        errors: list[str] = []
        audio_exclusions=self._audio_exclusions()
        try:
            for source, root_text in self._all_sources().items():
                root = Path(root_text)
                if not root.is_dir():
                    errors.append(f"{source}: source unavailable")
                    continue
                found: list[tuple[object, ...]] = []
                try:
                    for directory, names, files in os.walk(root, followlinks=False):
                        names[:] = [name for name in names
                                    if name.casefold() not in SKIP_DIRECTORIES
                                    and not os.path.islink(os.path.join(directory, name))]
                        for name in files:
                            extension = Path(name).suffix.casefold()
                            if extension not in SUPPORTED:
                                continue
                            absolute = os.path.join(directory, name)
                            if os.path.islink(absolute):
                                continue
                            try:
                                stat = os.stat(absolute, follow_symlinks=False)
                            except OSError:
                                continue
                            relative = os.path.relpath(absolute, root)
                            if relative.startswith(".." + os.sep) or relative == "..":
                                continue
                            if audio_exclusions.get((source,relative)) == (int(stat.st_size),int(stat.st_mtime_ns)):
                                continue
                            title, artist, album = _metadata(relative)
                            found.append((source, relative, title, artist, album,
                                          extension, SUPPORTED[extension],
                                          int(stat.st_size), int(stat.st_mtime_ns)))
                except OSError:
                    errors.append(f"{source}: scan interrupted")
                    continue
                with self.lock:
                    # An administrator may remove an additional source while
                    # this thread is walking it. Recheck under the same lock
                    # used by removal so a completed removal cannot be made
                    # available again by a stale scan result.
                    if self._all_sources().get(source) != root_text:
                        continue
                    # The catalog connection uses SQLite autocommit mode. Without an explicit
                    # transaction, executemany commits every track separately;
                    # a large NAS catalog can then hold this lock for minutes
                    # and make health and playback requests time out.
                    self.db.execute("BEGIN IMMEDIATE")
                    try:
                        self.db.execute(
                            "UPDATE tracks SET available=0 WHERE source=?", (source,))
                        self.db.executemany(
                            "INSERT INTO tracks(source,relative_path,title,artist,album,extension,"
                            "mime_type,size,modified_ns,available) VALUES(?,?,?,?,?,?,?,?,?,1) "
                            "ON CONFLICT(source,relative_path) DO UPDATE SET title=excluded.title,"
                            "artist=excluded.artist,album=excluded.album,extension=excluded.extension,"
                            "mime_type=excluded.mime_type,size=excluded.size,"
                            "modified_ns=excluded.modified_ns,available=1",
                            found)
                        self.db.execute("COMMIT")
                    except BaseException:
                        self.db.execute("ROLLBACK")
                        raise
                found_total += len(found)
            with self.lock:
                self.last_scan = int(time.time())
            metadata = self.refresh_metadata()
            self._refresh_playlists()
            result = {"started": True, "tracks": found_total, "errors": errors,
                      "metadata": metadata}
            with self.lock:
                self.last_scan_result = result
            return result
        finally:
            with self.lock:
                self.scan_running = False


    def collection_section(self, slug: str) -> str:
        for item in self.playlist_definitions:
            if str(item["slug"])==slug:
                return str(item.get("section","channels" if slug=="the-spectrum" else "playlists"))
        return "playlists"


    def channel_tracks(self, slug: str) -> list[int]:
        if self.collection_section(slug) != 'channels': raise CatalogError("channel not found")
        return [int(r[0]) for r in self.db.execute(
            "SELECT pt.track_id FROM playlist_tracks pt JOIN tracks t ON t.id=pt.track_id "
            "WHERE pt.playlist_slug=? AND t.available=1 ORDER BY pt.position", (slug,))]


    def radio_pick(self, slug: str, history: list[int]) -> int:
        """Radio rotation: core songs recur, with song and artist spacing."""
        tracks=self.channel_tracks(slug)
        if not tracks:raise CatalogError("channel has no available songs")
        song_gap=min(12,max(0,len(tracks)-1))
        recent=set(history[-song_gap:]) if song_gap else set()
        artists=getattr(self,'radio_artists',{})
        recent_artists={artists.get(t) for t in history[-3:]}
        choices=[t for t in tracks if t not in recent and artists.get(t) not in recent_artists]
        if not choices:choices=[t for t in tracks if t not in recent] or tracks
        weights=getattr(self,'channel_weights',{}).get(slug,{})
        return secrets.SystemRandom().choices(choices,weights=[weights.get(t,1) for t in choices],k=1)[0]


    def playlists(self) -> list[dict[str, object]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT p.slug,p.name,p.description,count(pt.track_id) AS tracks "
                "FROM playlists p LEFT JOIN playlist_tracks pt ON pt.playlist_slug=p.slug "
                "GROUP BY p.slug,p.name,p.description,p.position ORDER BY p.position"
            ).fetchall()
            available = int(self.db.execute(
                "SELECT count(*) FROM tracks WHERE available=1").fetchone()[0])
        result = [dict(row) for row in rows]
        for row in result:
            row["section"] = self.collection_section(str(row["slug"]))
            dynamic = self.dynamic_playlists.get(str(row["slug"]), "")
            if dynamic == DYNAMIC_ALL:
                row.update(dynamic=dynamic, tracks=available)
        return result


    @staticmethod
    def _quality_label(row: sqlite3.Row | dict[str, object]) -> dict[str, object]:
        if bool(row["lossless"]):
            return {"label": "LOSSLESS", "lossless": True}
        bitrate = int(row["bitrate_kbps"])
        if bitrate > 0:
            return {"label": f"{bitrate} kbps", "lossless": False}
        return {}


    def track_quality(self, track_id: int) -> dict[str, object]:
        """Return cached, parser-confirmed quality without exposing file details."""
        row, filename = self._track_file(track_id)
        modified_ns = int(row["modified_ns"])
        with self.lock:
            cached = self.db.execute(
                "SELECT bitrate_kbps,lossless FROM track_technical "
                "WHERE track_id=? AND file_modified_ns=?",
                (track_id, modified_ns),
            ).fetchone()
        if cached is not None:
            return self._quality_label(cached)

        bitrate = 0
        lossless = False
        codec = ""
        try:
            tag = TinyTag.get(filename, image=False, duration=True)
            measured = float(getattr(tag, "bitrate", 0) or 0)
            if 1 <= measured <= 100000:
                bitrate = int(round(measured))
            lossless = bool(getattr(tag, "is_lossless", False))
            codec = _tag_text(getattr(tag, "mime_type", ""), 96)
        except (OSError, TinyTagException, TypeError, ValueError):
            # Cache an unavailable result until the file changes. Repeated page
            # polling must never repeatedly parse a malformed or unsupported file.
            pass
        with self.lock:
            self.db.execute(
                "INSERT INTO track_technical(track_id,file_modified_ns,bitrate_kbps,"
                "lossless,codec,analyzed_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(track_id) DO UPDATE SET "
                "file_modified_ns=excluded.file_modified_ns,"
                "bitrate_kbps=excluded.bitrate_kbps,lossless=excluded.lossless,"
                "codec=excluded.codec,analyzed_at=excluded.analyzed_at",
                (track_id, modified_ns, bitrate, int(lossless), codec, int(time.time())),
            )
        return self._quality_label({
            "bitrate_kbps": bitrate, "lossless": int(lossless),
        })


    def _track_file(self, track_id: int) -> tuple[sqlite3.Row, str]:
        with self.lock:
            row = self.db.execute(
                "SELECT " + CATALOG_COLUMNS + CATALOG_JOIN +
                "WHERE t.id=? AND t.available=1", (track_id,)).fetchone()
        sources = self._all_sources()
        if row is None or str(row["source"]) not in sources:
            raise CatalogError("track unavailable")
        root = sources[str(row["source"])]
        candidate = os.path.realpath(os.path.join(root, str(row["relative_path"])))
        if os.path.commonpath((root, candidate)) != root or os.path.islink(candidate):
            raise CatalogError("track unavailable")
        try:
            stat = os.stat(candidate, follow_symlinks=False)
        except OSError as exc:
            raise CatalogError("track unavailable") from exc
        if not os.path.isfile(candidate) or int(stat.st_size) != int(row["size"]):
            raise CatalogError("track changed; catalog refresh required")
        if self._audio_exclusions().get((str(row['source']),str(row['relative_path']))) == (int(stat.st_size),int(stat.st_mtime_ns)):
            raise CatalogError('track unavailable')
        return row, candidate


    @staticmethod
    def _artwork_key(row: sqlite3.Row | dict[str, object]) -> str:
        artist = _music_key(str(row["artist"]))
        album = _music_key(str(row["album"]))
        return artist + "\x1f" + album if artist and album else ""

    @classmethod
    def _artwork_keys(cls, row: sqlite3.Row | dict[str, object]) -> list[str]:
        """Prefer canonical identity while retaining pre-campaign overrides."""
        keys = [cls._artwork_key(row)]
        raw = {
            "artist": row.get("raw_artist", "") if isinstance(row, dict)
            else row["raw_artist"],
            "album": row.get("raw_album", "") if isinstance(row, dict)
            else row["raw_album"],
        }
        keys.append(cls._artwork_key(raw))
        return list(dict.fromkeys(key for key in keys if key))


    def _cache_artwork(self, key: tuple[int, int], value: tuple[str, bytes, str]) -> None:
        size = len(value[1])
        if size > ARTWORK_CACHE_BYTES:
            return
        with self.lock:
            previous = self.artwork_cache.pop(key, None)
            if previous is not None:
                self.artwork_cache_bytes -= len(previous[1])
            while self.artwork_cache and self.artwork_cache_bytes + size > ARTWORK_CACHE_BYTES:
                _, removed = self.artwork_cache.popitem(last=False)
                self.artwork_cache_bytes -= len(removed[1])
            self.artwork_cache[key] = value
            self.artwork_cache_bytes += size


    def artwork(self, track_id: int) -> tuple[str, bytes, str]:
        """Return confirmed, embedded, folder, or branded fallback artwork."""
        row, filename = self._track_file(track_id)
        artwork_keys = self._artwork_keys(row)
        if artwork_keys:
            with self.lock:
                confirmed = self.db.execute(
                    "SELECT mime_type,image FROM album_artwork WHERE artwork_key IN (" +
                    ",".join("?" for _ in artwork_keys) + ") "
                    "ORDER BY CASE artwork_key WHEN ? THEN 0 ELSE 1 END LIMIT 1",
                    artwork_keys + [artwork_keys[0]],
                ).fetchone()
            if confirmed is not None:
                return str(confirmed["mime_type"]), bytes(confirmed["image"]), "confirmed"

        cache_key = (track_id, int(self.artwork_version(track_id), 16))
        with self.lock:
            cached = self.artwork_cache.pop(cache_key, None)
            if cached is not None:
                self.artwork_cache[cache_key] = cached
                return cached
        found = local_artwork(filename)
        value = found if found is not None else (
            "image/svg+xml", FALLBACK, "fallback",
        )
        self._cache_artwork(cache_key, value)
        return value


    def artwork_version(self, track_id: int) -> str:
        """Return an opaque change token without exposing file metadata."""
        with self.lock:
            row = self.db.execute(
                "SELECT " + CATALOG_COLUMNS + CATALOG_JOIN +
                "WHERE t.id=? AND t.available=1", (track_id,),
            ).fetchone()
            if row is None:
                raise CatalogError("track unavailable")
            artwork_keys = self._artwork_keys(row)
            confirmed = (self.db.execute(
                "SELECT image_sha256 FROM album_artwork WHERE artwork_key IN (" +
                ",".join("?" for _ in artwork_keys) + ") "
                "ORDER BY CASE artwork_key WHEN ? THEN 0 ELSE 1 END LIMIT 1",
                artwork_keys + [artwork_keys[0]],
            ).fetchone() if artwork_keys else None)
        if confirmed is not None:
            return str(confirmed["image_sha256"])[:16]
        sidecars = []
        try:
            _, filename = self._track_file(track_id)
            for cover in Path(filename).parent.iterdir():
                if cover.name.casefold() in ('cover.jpg','cover.png','folder.jpg','folder.png','front.jpg'):
                    info=cover.stat()
                    sidecars.append((cover.name,info.st_mtime_ns,info.st_size))
        except (OSError,CatalogError):
            pass
        seed = f"{track_id}:{int(row['modified_ns'])}:{sorted(sidecars)}".encode("utf-8")
        return hashlib.sha256(seed).hexdigest()[:16]

