"""Minimaler Client für die Subsonic-/OpenSubsonic-API der Nextcloud Music-App."""

from __future__ import annotations

from typing import Any, Iterator

import httpx


class SubsonicError(RuntimeError):
    pass


class Subsonic:
    """Zugriff auf ``<nextcloud>/index.php/apps/music/subsonic``.

    Authentifiziert wird mit dem in der Music-App erzeugten APIKEY
    (nicht mit dem Nextcloud-Passwort).
    """

    def __init__(self, base_url: str, user: str, api_key: str,
                 client_name: str = "AcousticGalaxy"):
        self.base_url = base_url.rstrip("/")
        self.user = user
        self.api_key = api_key
        self.client_name = client_name

    # -- intern ---------------------------------------------------------
    def _params(self, **params: Any) -> dict[str, Any]:
        params.update({
            "u": self.user,
            "apiKey": self.api_key,
            "v": "1.16.1",
            "c": self.client_name,
            "f": "json",
        })
        return params

    def _call(self, method: str, **params: Any) -> dict[str, Any]:
        url = f"{self.base_url}/rest/{method}"
        try:
            resp = httpx.get(url, params=self._params(**params), timeout=30)
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPError as exc:
            raise SubsonicError(f"Verbindung fehlgeschlagen: {exc}") from exc
        except ValueError as exc:
            raise SubsonicError("Antwort ist kein JSON") from exc

        data = payload.get("subsonic-response", {})
        if data.get("status") != "ok":
            err = data.get("error", {}) or {}
            raise SubsonicError(err.get("message") or "Subsonic-Fehler")
        return data

    # -- Bibliothek -----------------------------------------------------
    def ping(self) -> dict[str, Any]:
        return self._call("ping")

    def songs(self, page_size: int = 500) -> list[dict[str, Any]]:
        """Gesamte Bibliothek über ``search3`` mit leerer Suche lesen.

        Liefert die Music-App bei leerer Suche nichts, wird über
        ``getAlbumList2`` + ``getAlbum`` seitenweise gesammelt.
        """
        songs = self._search3_all(page_size)
        if songs:
            return songs
        return self._album_songs()

    def _search3_all(self, page_size: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            data = self._call("search3", query="", songCount=page_size,
                              songOffset=offset)
            batch = (data.get("searchResult3", {}) or {}).get("song", []) or []
            out.extend(batch)
            if len(batch) < page_size or offset > 500_000:
                break
            offset += page_size
        return out

    def _album_songs(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            data = self._call("getAlbumList2", type="alphabeticalByName",
                              size=500, offset=offset)
            albums = (data.get("albumList2", {}) or {}).get("album", []) or []
            for album in albums:
                detail = self._call("getAlbum", id=album.get("id"))
                out.extend((detail.get("album", {}) or {}).get("song", []) or [])
            if len(albums) < 500 or offset > 50_000:
                break
            offset += 500
        return out

    def starred(self) -> list[dict[str, Any]]:
        """Favoriten (OpenSubsonic ``getStarred2``)."""
        try:
            data = self._call("getStarred2")
        except SubsonicError:
            return []
        return (data.get("starred2", {}) or {}).get("song", []) or []

    def set_star(self, song_id: str, on: bool) -> None:
        self._call("star" if on else "unstar", id=song_id)

    # -- Medien ---------------------------------------------------------
    def stream(self, song_id: str, range_header: str | None = None):
        """Öffnet einen Streaming-Request; Rückgabe muss geschlossen werden."""
        client = httpx.Client(timeout=httpx.Timeout(connect=15, read=120))
        headers = {"Range": range_header} if range_header else {}
        request = client.build_request(
            "GET", f"{self.base_url}/rest/stream",
            params=self._params(id=song_id), headers=headers,
        )
        response = client.send(request, stream=True)
        return client, response

    def cover(self, cover_id: str) -> tuple[bytes, str]:
        url = f"{self.base_url}/rest/getCoverArt"
        resp = httpx.get(url, params=self._params(id=cover_id), timeout=30)
        resp.raise_for_status()
        return resp.content, resp.headers.get("content-type", "image/jpeg")

    def lyrics(self, song_id: str) -> dict[str, Any]:
        """Lyrics über OpenSubsonic ``getLyricsBySongId`` (best effort)."""
        try:
            data = self._call("getLyricsBySongId", id=song_id)
        except SubsonicError:
            return {"lyrics": "", "synced": []}

        lists = (data.get("lyricsList", {}) or {}).get("structuredLyrics", []) or []
        if not lists:
            return {"lyrics": "", "synced": []}
        entry = lists[0]
        lines = entry.get("line", []) or []
        synced: list[dict[str, Any]] = []
        plain: list[str] = []
        for line in lines:
            text = (line.get("value") or "").strip()
            if not text:
                continue
            plain.append(text)
            if line.get("start") is not None:
                synced.append({"t": round(float(line["start"]) / 1000.0, 2),
                               "text": text})
        synced.sort(key=lambda x: x["t"])
        return {"lyrics": "\n".join(plain)[:20000], "synced": synced[:2000]}
