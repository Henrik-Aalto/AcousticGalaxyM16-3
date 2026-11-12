"""AcousticGalaxy als Nextcloud-ExApp (AppAPI).

Die Musikbibliothek kommt aus der Nextcloud Music-App (Subsonic-API), die
Favoriten aus deren Starred-Liste. Der Zustand wird pro Benutzer im
persistenten ExApp-Volume abgelegt.
"""

from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import numpy as np
from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from nc_py_api import NextcloudApp
from nc_py_api.ex_app import AppAPIAuthMiddleware, nc_app, run_app, set_handlers

import store
from galaxy import build_landscape, feature_vector, rotate_clusters
from library import analyse_and_store, check_connection
from subsonic import Subsonic, SubsonicError

APP_ID = os.environ.get("APP_ID", "acousticgalaxy")


# ---------------------------------------------------------------------------
# Zustand pro Benutzer
# ---------------------------------------------------------------------------

class UserState:
    def __init__(self, user: str):
        self.user = user
        self.lock = threading.Lock()
        self.tracks: dict[str, dict[str, Any]] = {}
        self.landscape: dict[str, Any] = {}
        self.loaded = False
        self.status: dict[str, Any] = {
            "running": False,
            "progress": 0,
            "total": 0,
            "landscape_running": False,
            "landscape_done": False,
            "message": "",
        }

    def load(self) -> None:
        if self.loaded:
            return
        self.loaded = True
        library = store.load_library(self.user)
        self.tracks = {t["id"]: t for t in library.get("tracks", []) if t.get("id")}
        self.landscape = store.load_landscape(self.user)
        if self.tracks:
            self.status["message"] = f"{len(self.tracks)} Stücke geladen"


STATES: dict[str, UserState] = {}
STATES_LOCK = threading.Lock()


def get_state(user: str) -> UserState:
    with STATES_LOCK:
        state = STATES.get(user)
        if state is None:
            state = UserState(user)
            STATES[user] = state
    state.load()
    return state


# ---------------------------------------------------------------------------
# Music-Anbindung
# ---------------------------------------------------------------------------

RUNTIME: dict[str, str] = {
    "nextcloud_url": (os.environ.get("NEXTCLOUD_URL") or "").rstrip("/"),
}


def music_base_url(settings: dict[str, Any]) -> str:
    url = (settings.get("music_url") or "").strip()
    if url:
        return url.rstrip("/")
    nextcloud = (RUNTIME["nextcloud_url"]
                 or (os.environ.get("NEXTCLOUD_URL") or "")).rstrip("/")
    return f"{nextcloud}/index.php/apps/music/subsonic" if nextcloud else ""


def get_client(user: str) -> Subsonic | None:
    settings = store.load_settings(user)
    key = (settings.get("api_key")
           or os.environ.get("ACOUSTIC_MUSIC_APIKEY") or "").strip()
    base = music_base_url(settings)
    if not key or not base:
        return None
    return Subsonic(base, user, key)


def start_analysis(user: str) -> None:
    state = get_state(user)
    client = get_client(user)
    with state.lock:
        if state.status.get("running"):
            return
        if client is None:
            state.status.update(running=False, message="Music-API-Key fehlt")
            return
        state.status.update(running=True, progress=0, total=0,
                            landscape_running=False, landscape_done=False,
                            message="Lade Bibliothek …")
    threading.Thread(target=_run_analysis, args=(user, client), daemon=True).start()


def _run_analysis(user: str, client: Subsonic) -> None:
    state = get_state(user)

    def progress(done: int, total: int) -> None:
        with state.lock:
            state.status.update(progress=done, total=total,
                                message=f"Analysiere {done}/{total}")

    try:
        tracks, landscape = analyse_and_store(user, client, progress)
        with state.lock:
            state.tracks = {t["id"]: t for t in tracks}
            state.landscape = landscape
            regions = len(landscape.get("clusters", []))
            state.status.update(running=False, progress=len(tracks),
                                total=len(tracks), landscape_done=True,
                                message=f"{regions} Regionen, {len(tracks)} Stücke")
    except Exception as exc:  # pragma: no cover - Laufzeitfehler
        with state.lock:
            state.status.update(running=False,
                                message=f"Fehler: {exc}")


def start_landscape(user: str) -> None:
    state = get_state(user)
    with state.lock:
        if state.status.get("landscape_running") or state.status.get("running"):
            return
        if not state.tracks:
            return
        state.status.update(landscape_running=True, landscape_done=False,
                            message="Berechne Landschaft …")
        tracks = list(state.tracks.values())

    def work() -> None:
        try:
            landscape = build_landscape(tracks)
            store.save_landscape(user, landscape)
            with state.lock:
                state.landscape = landscape
                state.status.update(landscape_running=False, landscape_done=True,
                                    message="Landschaft berechnet")
        except Exception as exc:  # pragma: no cover
            with state.lock:
                state.status.update(landscape_running=False,
                                    message=f"Fehler: {exc}")

    threading.Thread(target=work, daemon=True).start()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

def enabled_handler(enabled: bool, nc: NextcloudApp) -> str:
    print(f"[AcousticGalaxy] enabled={enabled}", flush=True)
    return ""


@asynccontextmanager
async def lifespan(_: FastAPI):
    set_handlers(APP, enabled_handler)
    yield


APP = FastAPI(title="AcousticGalaxy", lifespan=lifespan)
APP.add_middleware(AppAPIAuthMiddleware)


def current_user(nc: Annotated[NextcloudApp, Depends(nc_app)]) -> str:
    user = (getattr(nc, "user", "") or "").strip()
    if not user:
        raise HTTPException(status_code=401, detail="Nicht authentifiziert")
    try:
        url = (getattr(nc.app_cfg, "nextcloud_url", "") or "").rstrip("/")
        if url:
            RUNTIME["nextcloud_url"] = url
    except Exception:
        pass
    return user


@APP.get("/heartbeat")
def heartbeat() -> dict[str, str]:
    return {"status": "ok"}


# -- Einstellungen ----------------------------------------------------------

@APP.get("/api/settings")
def get_settings(user: Annotated[str, Depends(current_user)]) -> dict[str, Any]:
    settings = store.load_settings(user)
    key = (settings.get("api_key")
           or os.environ.get("ACOUSTIC_MUSIC_APIKEY") or "")
    return {
        "user": user,
        "has_key": bool(key),
        "music_url": music_base_url(settings),
        "nextcloud_url": RUNTIME["nextcloud_url"],
    }


@APP.post("/api/settings")
def set_settings(user: Annotated[str, Depends(current_user)],
                 payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    settings = store.load_settings(user)
    api_key = (payload.get("apiKey") or "").strip()
    music_url = (payload.get("musicUrl") or "").strip()
    if api_key:
        settings["api_key"] = api_key
    if music_url:
        settings["music_url"] = music_url
    store.save_settings(user, settings)
    start_analysis(user)
    return {"ok": True, "has_key": bool(settings.get("api_key"))}


@APP.post("/api/settings/test")
def test_settings(user: Annotated[str, Depends(current_user)]) -> dict[str, Any]:
    client = get_client(user)
    if client is None:
        return {"ok": False, "message": "Kein API-Key hinterlegt"}
    return check_connection(client)


# -- Status / Scan ----------------------------------------------------------

@APP.get("/api/status")
def status(user: Annotated[str, Depends(current_user)]) -> dict[str, Any]:
    state = get_state(user)
    with state.lock:
        return {**state.status, "count": len(state.tracks)}


@APP.post("/api/scan")
def scan(user: Annotated[str, Depends(current_user)]) -> dict[str, Any]:
    start_analysis(user)
    return {"status": "started"}


# -- Karte / Landschaft -----------------------------------------------------

@APP.get("/api/map")
def get_map(user: Annotated[str, Depends(current_user)]) -> dict[str, Any]:
    state = get_state(user)
    if not state.tracks and not state.status.get("running"):
        start_analysis(user)

    with state.lock:
        tracks = list(state.tracks.values())
        landscape = state.landscape

    points = landscape.get("points", {}) if landscape else {}
    clusters = rotate_clusters(landscape) if landscape else []

    items: list[dict[str, Any]] = []
    for track in tracks:
        point = points.get(track["id"]) or {
            "x": 0.0, "y": 0.0, "z": 0.0, "cluster": -1, "color": "#475569",
        }
        items.append({
            "id": track["id"],
            "name": track.get("name") or track["id"],
            "artist": track.get("artist") or "",
            "title": track.get("title") or track.get("name") or track["id"],
            "album": track.get("album") or "",
            "x": point["x"], "y": point["y"], "z": point.get("z", 0.0),
            "cluster": point.get("cluster", -1),
            "color": point.get("color", "#475569"),
            "tempo": float((track.get("features") or {}).get("tempo", 0.0)),
            "genre": track.get("genre") or "",
        })

    return {
        "items": items,
        "count": len(items),
        "layout": "landscape" if points else "basic",
        "clusters": clusters,
    }


@APP.get("/api/landscape")
def landscape_state(user: Annotated[str, Depends(current_user)]) -> dict[str, Any]:
    state = get_state(user)
    with state.lock:
        return {
            "running": state.status.get("landscape_running", False),
            "done": state.status.get("landscape_done", False),
            "message": state.status.get("message", ""),
        }


@APP.post("/api/landscape")
def landscape_start(user: Annotated[str, Depends(current_user)]) -> dict[str, Any]:
    start_landscape(user)
    return {"status": "started"}


# -- Einzelne Titel ---------------------------------------------------------

def _track_or_404(state: UserState, song_id: str) -> dict[str, Any]:
    track = state.tracks.get(song_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track nicht gefunden")
    return track


@APP.get("/api/track/{song_id:path}")
def track(song_id: str, user: Annotated[str, Depends(current_user)]) -> dict[str, Any]:
    return _track_or_404(get_state(user), song_id)


@APP.get("/api/similar/{song_id:path}")
def similar(song_id: str, user: Annotated[str, Depends(current_user)],
            limit: int = 10) -> dict[str, Any]:
    state = get_state(user)
    base_track = _track_or_404(state, song_id)
    if not base_track.get("features"):
        return {"similar": []}

    base = feature_vector(base_track["features"])
    points = state.landscape.get("points", {})
    scored: list[tuple[float, dict[str, Any]]] = []
    for other in state.tracks.values():
        if other["id"] == song_id or not other.get("features"):
            continue
        vec = feature_vector(other["features"])
        score = float(np.dot(base, vec))
        scored.append((score, other))
    scored.sort(key=lambda item: -item[0])

    out: list[dict[str, Any]] = []
    for score, other in scored[: max(0, limit)]:
        point = points.get(other["id"], {})
        out.append({
            "id": other["id"],
            "name": other.get("name") or other["id"],
            "title": other.get("title") or "",
            "artist": other.get("artist") or "",
            "album": other.get("album") or "",
            "genre": other.get("genre") or "",
            "similarity": round(score, 3),
            "color": point.get("color", "#38e8ff"),
        })
    return {"similar": out}


@APP.get("/api/lyrics/{song_id:path}")
def lyrics(song_id: str, user: Annotated[str, Depends(current_user)]) -> dict[str, Any]:
    state = get_state(user)
    _track_or_404(state, song_id)
    client = get_client(user)
    if client is None:
        return {"lyrics": "", "synced": []}
    try:
        return client.lyrics(song_id)
    except SubsonicError:
        return {"lyrics": "", "synced": []}


@APP.get("/api/cover/{song_id:path}")
def cover(song_id: str, user: Annotated[str, Depends(current_user)]):
    state = get_state(user)
    track_data = _track_or_404(state, song_id)
    client = get_client(user)
    if client is None:
        raise HTTPException(status_code=404, detail="Kein API-Key")
    cover_id = track_data.get("cover") or song_id
    try:
        content, media_type = client.cover(str(cover_id))
    except Exception:
        raise HTTPException(status_code=404, detail="Kein Cover")
    return Response(content=content, media_type=media_type)


@APP.get("/api/audio/{song_id:path}")
def audio(song_id: str, request: Request,
          user: Annotated[str, Depends(current_user)]):
    state = get_state(user)
    _track_or_404(state, song_id)
    client = get_client(user)
    if client is None:
        raise HTTPException(status_code=404, detail="Kein API-Key")

    http_client, upstream = client.stream(
        song_id, range_header=request.headers.get("range"))
    headers: dict[str, str] = {}
    for name in ("content-type", "content-length", "content-range",
                 "accept-ranges", "etag", "last-modified"):
        if name in upstream.headers:
            headers[name] = upstream.headers[name]

    def body():
        try:
            for chunk in upstream.iter_bytes():
                yield chunk
        finally:
            upstream.close()
            http_client.close()

    return StreamingResponse(body(), status_code=upstream.status_code,
                             headers=headers,
                             media_type=headers.get("content-type"))


# -- Favoriten --------------------------------------------------------------

@APP.get("/api/favorites")
def favorites(user: Annotated[str, Depends(current_user)]) -> dict[str, Any]:
    client = get_client(user)
    if client is None:
        return {"ids": []}
    try:
        return {"ids": [str(song.get("id")) for song in client.starred()]}
    except SubsonicError:
        return {"ids": []}


@APP.post("/api/favorites/{song_id:path}")
def set_favorite(song_id: str, user: Annotated[str, Depends(current_user)],
                 payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    client = get_client(user)
    if client is None:
        raise HTTPException(status_code=400, detail="Kein API-Key")
    on = bool(payload.get("on"))
    try:
        client.set_star(song_id, on)
    except SubsonicError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "on": on}


# -- Pfade (im Nextcloud-Betrieb nicht verfügbar) ---------------------------

@APP.get("/api/paths")
def paths(user: Annotated[str, Depends(current_user)]) -> dict[str, Any]:
    return {"root": "Nextcloud Music", "selected": [], "paths": []}


@APP.post("/api/root")
def set_root(user: Annotated[str, Depends(current_user)],
             payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    raise HTTPException(status_code=400,
                        detail="Im Nextcloud-Betrieb nicht verfügbar")


@APP.post("/api/selection")
def set_selection(user: Annotated[str, Depends(current_user)],
                  payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    raise HTTPException(status_code=400,
                        detail="Im Nextcloud-Betrieb nicht verfügbar")


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

def _static_dir() -> Path | None:
    candidates = [
        os.environ.get("ACOUSTIC_STATIC_DIR"),
        "/ex_app/static",
        Path(__file__).resolve().parents[3] / "static",
        Path(__file__).resolve().parents[2] / "static",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    return None


_static = _static_dir()
if _static is not None:
    APP.mount("/", StaticFiles(directory=str(_static), html=True), name="static")


if __name__ == "__main__":
    os.chdir(Path(__file__).parent)
    run_app("main:APP", log_level="info")
