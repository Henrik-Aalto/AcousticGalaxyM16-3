"""Tests für die ExApp-Bausteine (ohne Nextcloud und ohne echte Musik)."""

from __future__ import annotations

import json

import pytest

import store
import subsonic
from galaxy import build_landscape, rotate_clusters


class FakeResponse:
    def __init__(self, payload, status_code=200, headers=None, content=b""):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise subsonic.httpx.HTTPStatusError("boom", request=None,
                                                 response=self)

    def json(self):
        return self._payload


def ok(payload):
    return FakeResponse({"subsonic-response": {"status": "ok", **payload}})


# -- Subsonic ---------------------------------------------------------------

def test_params_enthalten_auth():
    client = subsonic.Subsonic("https://cloud.example/index.php/apps/music/subsonic",
                               "alice", "secret")
    params = client._params(id="42")
    assert params["u"] == "alice"
    assert params["apiKey"] == "secret"
    assert params["f"] == "json"
    assert params["id"] == "42"


def test_call_wirft_bei_fehlerstatus(monkeypatch):
    def fake_get(*args, **kwargs):
        return FakeResponse({"subsonic-response": {
            "status": "failed", "error": {"message": "Bad key"}}})

    monkeypatch.setattr(subsonic.httpx, "get", fake_get)
    client = subsonic.Subsonic("https://cloud", "alice", "secret")
    with pytest.raises(subsonic.SubsonicError, match="Bad key"):
        client.ping()


def test_songs_ohne_treffer_nutzt_alben(monkeypatch):
    client = subsonic.Subsonic("https://cloud", "alice", "secret")

    def fake_call(method, **params):
        if method == "search3":
            return {"searchResult3": {}}
        if method == "getAlbumList2":
            if params.get("offset", 0) == 0:
                return {"albumList2": {"album": [{"id": "a1"}]}}
            return {"albumList2": {"album": []}}
        if method == "getAlbum":
            return {"album": {"song": [{"id": "s1"}, {"id": "s2"}]}}
        raise AssertionError(method)

    monkeypatch.setattr(client, "_call", fake_call)
    assert [s["id"] for s in client.songs()] == ["s1", "s2"]


def test_starred_leer_bei_fehler(monkeypatch):
    client = subsonic.Subsonic("https://cloud", "alice", "secret")

    def boom(method, **params):
        raise subsonic.SubsonicError("nope")

    monkeypatch.setattr(client, "_call", boom)
    assert client.starred() == []


def test_lyrics_parst_zeiten(monkeypatch):
    client = subsonic.Subsonic("https://cloud", "alice", "secret")
    payload = {"lyricsList": {"structuredLyrics": [{"line": [
        {"value": "eins", "start": 1000},
        {"value": "zwei", "start": 2500},
        {"value": "  "},
    ]}]}}

    monkeypatch.setattr(client, "_call", lambda method, **params: payload)
    result = client.lyrics("s1")
    assert result["lyrics"] == "eins\nzwei"
    assert result["synced"] == [{"t": 1.0, "text": "eins"},
                                {"t": 2.5, "text": "zwei"}]


# -- Galaxie ----------------------------------------------------------------

def _track(i, artist):
    return {
        "id": f"s{i}",
        "name": f"{artist} – Titel {i}",
        "artist": artist,
        "title": f"Titel {i}",
        "album": "Album",
        "genre": "Rock",
        "features": {
            "tempo": 90 + i,
            "rms": 0.1 + i * 0.001,
            "centroid": 1500 + i * 10,
            "rolloff": 4000.0,
            "zcr": 0.05,
            "flatness": 0.1,
            "band_low": 0.3,
            "band_mid": 0.4,
            "band_high": 0.3,
            "mfcc": [float(i % 7)] * 13,
        },
    }


def test_build_landscape_liefert_punkte_und_namen():
    tracks = [_track(i, "Alpha" if i < 12 else "Beta") for i in range(24)]
    landscape = build_landscape(tracks)
    assert landscape["count"] == 24
    assert len(landscape["points"]) == 24
    assert landscape["clusters"]
    assert len(landscape["names"]) == len(landscape["clusters"])
    for point in landscape["points"].values():
        assert set(point) >= {"x", "y", "z", "cluster", "color"}


def test_rotate_clusters_wechselt_repräsentanten():
    landscape = {
        "clusters": [{"size": 10, "color": "#123456", "c": [0, 0, 0]}],
        "names": [{
            "cands": ["Alpha", "Beta", "Gamma"],
            "fallback": "Unbekannt",
            "ac": {"Alpha": 6, "Beta": 3, "Gamma": 1},
            "last": -1,
        }],
    }
    first = rotate_clusters(landscape)[0]["name"]
    second = rotate_clusters(landscape)[0]["name"]
    assert first != second
    assert first in {"Alpha", "Beta", "Gamma"}


# -- Store ------------------------------------------------------------------

def test_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("ACOUSTIC_DATA_DIR", str(tmp_path))
    store.save_library("alice", {"tracks": [{"id": "1"}]})
    store.save_settings("alice", {"api_key": "k"})
    assert store.load_library("alice")["tracks"][0]["id"] == "1"
    assert store.load_settings("alice")["api_key"] == "k"
    assert store.load_library("bob") == {}


def test_store_schuetzt_pfade(tmp_path, monkeypatch):
    monkeypatch.setenv("ACOUSTIC_DATA_DIR", str(tmp_path))
    path = store.user_dir("../evil/user")
    assert tmp_path in path.parents
