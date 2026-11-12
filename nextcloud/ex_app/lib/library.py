"""Bibliothek aus der Nextcloud Music-App laden, analysieren und cachen."""

from __future__ import annotations

import os
import tempfile
import time
from typing import Any, Callable

import store
from galaxy import build_landscape, extract_features
from subsonic import Subsonic, SubsonicError

MAX_STREAM_BYTES = 8_000_000  # reicht für die ersten ~60 s


def _extract_from_stream(client: Subsonic, song: dict[str, Any]) -> dict[str, Any] | None:
    suffix = (song.get("suffix") or "mp3").lower()
    tmp = tempfile.NamedTemporaryFile(suffix="." + suffix, delete=False)
    try:
        http_client, response = client.stream(
            str(song.get("id")), range_header=f"bytes=0-{MAX_STREAM_BYTES}")
        try:
            if response.status_code >= 400:
                return None
            for chunk in response.iter_bytes():
                tmp.write(chunk)
                if tmp.tell() >= MAX_STREAM_BYTES:
                    break
        finally:
            response.close()
            http_client.close()
        tmp.close()
        return extract_features(tmp.name)
    except Exception:
        return None
    finally:
        try:
            tmp.close()
        except Exception:
            pass
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _track_from_song(song: dict[str, Any], features: dict[str, Any] | None) -> dict[str, Any]:
    sid = str(song.get("id"))
    title = (song.get("title") or "").strip() or (song.get("name") or sid)
    return {
        "id": sid,
        "name": title,
        "title": title,
        "artist": (song.get("artist") or "").strip(),
        "album": (song.get("album") or "").strip(),
        "genre": (song.get("genre") or "").strip(),
        "duration": float(song.get("duration") or 0.0),
        "cover": str(song.get("coverArt") or ""),
        "suffix": (song.get("suffix") or "").strip(),
        "size": int(song.get("size") or 0),
        "path": sid,
        "features": features or {},
    }


def analyse(user: str, client: Subsonic,
            progress: Callable[[int, int], None] | None = None,
            ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    songs = client.songs()
    total = len(songs)
    cached = store.load_features(user)
    tracks: list[dict[str, Any]] = []

    for index, song in enumerate(songs):
        sid = str(song.get("id"))
        size = int(song.get("size") or 0)
        entry = cached.get(sid)
        features: dict[str, Any] | None = None
        if entry and (size == 0 or int(entry.get("size") or 0) == size):
            features = entry.get("features")
        if not features:
            features = _extract_from_stream(client, song)
            if features:
                cached[sid] = {"size": size, "features": features}
        tracks.append(_track_from_song(song, features))
        if progress:
            progress(index + 1, total)
        if (index + 1) % 25 == 0:
            store.save_features(user, cached)

    store.save_features(user, cached)
    landscape = build_landscape(tracks)
    return tracks, landscape


def analyse_and_store(user: str, client: Subsonic,
                      progress: Callable[[int, int], None] | None = None,
                      ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tracks, landscape = analyse(user, client, progress)
    store.save_library(user, {"tracks": tracks, "updated": time.time()})
    store.save_landscape(user, landscape)
    return tracks, landscape


def check_connection(client: Subsonic) -> dict[str, Any]:
    try:
        client.ping()
        return {"ok": True, "message": "Verbindung erfolgreich"}
    except SubsonicError as exc:
        return {"ok": False, "message": str(exc)}
