#!/usr/bin/env python3
"""AcousticGalaxyM16-3 – deine Musikbibliothek als interaktive 3D-Galaxie.

Schnellstart:
    python3 app.py --init           geführtes Setup (Musikordner + Auswahl)
    python3 app.py                  Server auf http://127.0.0.1:8000
    python3 app.py --install-deps   System- und Python-Pakete installieren

Alle Pfade sind relativ zum Projektordner; Musik wird nur gelesen.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}
MEDIA_TYPES = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
    ".ogg": "audio/ogg", ".m4a": "audio/mp4",
}
DEFAULT_ROOT = Path(os.environ.get("ACOUSTI_MATCH_AUDIO_DIR", "/mnt/Audio")).expanduser()
CACHE_DIR = Path(os.environ.get("ACOUSTI_MATCH_CACHE_DIR", BASE_DIR / "cache")).expanduser()
DB_FILE = CACHE_DIR / "database.json"
CONFIG_FILE = CACHE_DIR / "config.json"
STATIC_DIR = BASE_DIR / "static"


# ---------------------------------------------------------------------------
# Hilfsfunktionen (nur Standardbibliothek – auch für --init nutzbar)
# ---------------------------------------------------------------------------

def write_json(path: Path, data: Any) -> None:
    """Schreibt JSON atomar, damit ein Absturz keine Datei zerstört."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def count_audio(directory: Path) -> int:
    return sum(1 for p in directory.rglob("*")
               if p.is_file() and p.suffix.lower() in AUDIO_EXTS)


def cmd_install_deps() -> None:
    system_pkgs = [
        "python3-pip",
        "python3-venv",
        "ffmpeg",
        "libsndfile1",
        "libffi-dev",
        "build-essential",
    ]

    def run(cmd: list[str]) -> None:
        print(f"[+] {' '.join(cmd)}")
        subprocess.check_call(cmd)

    if os.geteuid() != 0:
        print("[!] Systempakete brauchen root: sudo python3 app.py --install-deps")
        return
    run(["apt-get", "update", "-y"])
    run(["apt-get", "install", "-y", *system_pkgs])
    run([sys.executable, "-m", "pip", "install", "-r",
         str(BASE_DIR / "requirements.txt"), "--break-system-packages"])
    print("[+] Abhängigkeiten installiert. Weiter mit: python3 app.py --init")


def cmd_init(root_arg: str | None, paths_arg: str | None, assume_yes: bool) -> None:
    """Geführtes Setup: Musikordner wählen, Ordner-Auswahl speichern."""
    existing: dict[str, Any] = {}
    if CONFIG_FILE.exists():
        try:
            existing = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    root_text = root_arg or existing.get("root") or str(DEFAULT_ROOT)
    interactive = sys.stdin.isatty() and not assume_yes
    if interactive:
        print(f"Musikordner [{root_text}]: ", end="", flush=True)
        answer = input().strip()
        if answer:
            root_text = answer
    root = Path(root_text).expanduser().resolve()

    if not root.is_dir():
        print(f"[!] Ordner nicht gefunden: {root}")
        print("    Aufruf z. B.: python3 app.py --init --root /pfad/zur/musik")
        sys.exit(1)

    dirs = sorted((d for d in root.iterdir() if d.is_dir()), key=lambda p: p.name.lower())
    if not dirs:
        print(f"[!] Keine Unterordner in {root} – bitte Musik in Unterordnern ablegen.")
        sys.exit(1)

    print(f"\nGefunden in {root}:")
    for i, d in enumerate(dirs, 1):
        print(f"  {i:2d}) {d.name}  ({count_audio(d)} Titel)")

    selection = paths_arg
    if selection is None and interactive:
        print("\nAuswahl (z. B. 1,3-5 – Enter = alle): ", end="", flush=True)
        selection = input().strip()

    if selection:
        wanted = {s.strip() for s in selection.split(",") if s.strip()}
        selected = [d.name for d in dirs if d.name in wanted]
        missing = wanted - {d.name for d in dirs}
        if missing:
            print(f"[!] Unbekannte Ordner ignoriert: {', '.join(sorted(missing))}")
    else:
        selected = []

    write_json(CONFIG_FILE, {"root": str(root), "paths": selected})
    print(f"\n[+] Gespeichert in {CONFIG_FILE}")
    print(f"    Musikordner: {root}")
    print(f"    Auswahl:     {', '.join(selected) if selected else 'alle Ordner'}")
    print("\nNächster Schritt: python3 app.py  →  http://127.0.0.1:8000")


def _early_dispatch() -> None:
    """Läuft vor den Drittanbieter-Imports, damit --init ohne Pakete funktioniert."""
    argv = sys.argv[1:]
    if not any(a in ("--init", "--install-deps") for a in argv):
        return
    parser = argparse.ArgumentParser(description="AcousticGalaxyM16-3 Setup")
    parser.add_argument("--init", action="store_true", help="Config anlegen/ändern")
    parser.add_argument("--install-deps", action="store_true", help="Systempakete installieren")
    parser.add_argument("--root", help="Pfad zur Musikbibliothek")
    parser.add_argument("--paths", help="Kommagetrennte Ordner-Auswahl")
    parser.add_argument("--yes", "-y", action="store_true", help="ohne Rückfragen")
    ns = parser.parse_args(argv)
    if ns.install_deps:
        cmd_install_deps()
    if ns.init:
        cmd_init(ns.root, ns.paths, ns.yes)
    sys.exit(0)


_early_dispatch()

# ---------------------------------------------------------------------------
# Drittanbieter-Imports
# ---------------------------------------------------------------------------
import numpy as np  # noqa: E402
import uvicorn  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402

from fastapi import Body, FastAPI, HTTPException, Response  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

try:
    import librosa  # noqa: E402
    from sklearn.cluster import KMeans  # noqa: E402
    from sklearn.decomposition import PCA  # noqa: E402
    from sklearn.preprocessing import StandardScaler  # noqa: E402
    try:
        import umap  # type: ignore  # noqa: E402
        HAS_UMAP = True
    except Exception:
        HAS_UMAP = False
    HAS_LIBS = True
except Exception:
    HAS_LIBS = False
    HAS_UMAP = False

try:
    import mutagen  # noqa: E402
    HAS_MUTAGEN = True
except Exception:
    HAS_MUTAGEN = False

# ---------------------------------------------------------------------------
# Zustand
# ---------------------------------------------------------------------------
CACHE_DIR.mkdir(parents=True, exist_ok=True)

HOST = os.environ.get("ACOUSTI_MATCH_HOST", "127.0.0.1")
PORT = int(os.environ.get("ACOUSTI_MATCH_PORT", "8000"))

analysis_lock = threading.Lock()
pending_rescan = False
landscape_lock = threading.Lock()
pending_landscape = False

# config["root"] = Musikordner, config["paths"] = relativer Ordner; leer = alle
config: dict[str, Any] = {"root": "", "paths": []}
AUDIO_DIR: Path = DEFAULT_ROOT
active_ids: set[str] = set()

database: dict[str, dict[str, Any]] = {}
map_coords: dict[str, tuple[float, float]] = {}
analysis_status = {"running": False, "progress": 0, "total": 0, "done": False}
landscape = {"ready": False, "layout": "basic", "points": {}, "clusters": [], "count": 0}
landscape_status = {"running": False, "done": False, "message": ""}

executor = ThreadPoolExecutor(max_workers=3)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _startup()
    yield


app = FastAPI(title="AcousticGalaxyM16-3", lifespan=lifespan)

# ---------------------------------------------------------------------------
# Audio-Features
# ---------------------------------------------------------------------------

def extract_features(path: Path) -> dict[str, Any] | None:
    if not HAS_LIBS:
        return None
    try:
        y, sr = librosa.load(str(path), sr=22050, mono=True, duration=60)
        if len(y) == 0:
            return None

        tempo_arr, _ = librosa.beat.beat_track(y=y, sr=sr)
        tempo = float(np.mean(np.atleast_1d(tempo_arr))) if np.size(tempo_arr) else 0.0

        chroma = librosa.feature.chroma_stft(y=y, sr=sr)
        key_idx = int(np.argmax(np.mean(chroma, axis=1)))
        key_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        key = key_names[key_idx % 12]

        cent = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
        spectral_centroid = float(np.mean(cent))

        rms = librosa.feature.rms(y=y)[0]
        rms_energy = float(np.mean(rms))

        zcr = librosa.feature.zero_crossing_rate(y=y)[0]
        zero_crossing_rate = float(np.mean(zcr))

        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
        mfcc_mean = [float(x) for x in np.mean(mfcc, axis=1)]

        duration = float(librosa.get_duration(y=y, sr=sr))

        return {
            "tempo": tempo,
            "key": key,
            "spectral_centroid": spectral_centroid,
            "rms_energy": rms_energy,
            "zero_crossing_rate": zero_crossing_rate,
            "mfcc": mfcc_mean,
            "duration": duration,
        }
    except Exception as e:
        print(f"[!] Analyse-Fehler {path.name}: {e}")
        return None


def feature_vector(d: dict[str, Any]) -> np.ndarray:
    mfcc = d.get("mfcc", [0.0] * 13)
    vec = np.array([
        d.get("tempo", 120.0) / 200.0,
        d.get("spectral_centroid", 1000.0) / 4000.0,
        d.get("rms_energy", 0.1) * 5.0,
        d.get("zero_crossing_rate", 0.05) * 10.0,
        d.get("duration", 200.0) / 600.0,
        *mfcc,
    ], dtype=np.float32)
    n = np.linalg.norm(vec)
    if n > 0:
        vec = vec / n
    return vec


def _norm01(v: float, lo: float, hi: float) -> float:
    return max(0.0, min(1.0, (v - lo) / (hi - lo + 1e-9)))


def compute_coord(f: dict[str, Any]) -> tuple[float, float]:
    """Interpretierbare 2D-Achsen:
    x = 0 (langsam) .. 1 (schnell)   -> Tempo
    y = 0 (weich)   .. 1 (rockig)    -> Härte/Spektrum/Energie
    """
    x = _norm01(float(f.get("tempo", 120.0)), 60.0, 180.0)
    hardness = (
        0.50 * _norm01(float(f.get("spectral_centroid", 1500.0)), 500.0, 5000.0)
        + 0.30 * _norm01(float(f.get("rms_energy", 0.10)), 0.02, 0.35)
        + 0.20 * _norm01(float(f.get("zero_crossing_rate", 0.05)), 0.02, 0.20)
    )
    return (x, max(0.0, min(1.0, hardness)))


# ---------------------------------------------------------------------------
# Musik-Landschaft: Embedding -> UMAP -> Cluster -> Regionennamen
# ---------------------------------------------------------------------------

CLUSTER_PALETTE = [
    "#38bdf8", "#f472b6", "#a3e635", "#f59e0b", "#a78bfa",
    "#22d3ee", "#fb7185", "#34d399", "#fbbf24", "#818cf8",
    "#2dd4bf", "#f97316", "#e879f9", "#4ade80", "#60a5fa",
    "#f87171", "#c084fc", "#facc15",
]


def embedding_vector(f: dict[str, Any]) -> list[float]:
    mfcc = f.get("mfcc", [0.0] * 13)
    return [
        float(f.get("tempo", 120.0)),
        float(f.get("spectral_centroid", 1500.0)),
        float(f.get("rms_energy", 0.10)),
        float(f.get("zero_crossing_rate", 0.05)),
        float(f.get("duration", 200.0)),
        *[float(x) for x in mfcc],
    ]


GENERIC_TOP = {
    "alben und singles", "sampler", "playlists", "untermalung",
    "midi", "mod", "noices", "noises",
}

# Sampler-Reihen sehen aus wie Interpreten, sind es aber nicht
SAMPLER_WORDS = ("kuschelrock", "bravo hits", "sampler", "sammlung", "samplerreihe",
                 "best of", "collection", "box", "the hits", "now that's", "vol.",
                 "various", "verschiedene")


def _artist_ok(name: str) -> bool:
    low = name.lower()
    return bool(name) and not any(w in low for w in SAMPLER_WORDS)


def _short_name(name: str, limit: int = 42) -> str:
    """Kürzt lange Interpretnamen für Regions-Labels."""
    if len(name) <= limit:
        return name
    return (name.split(",")[0].strip() or name)[:limit]


def _track_artist(entry: dict[str, Any]) -> str:
    """Interpret aus Tags, sonst aus dem Pfad (nur bei sprechenden Ordnern)."""
    name = (entry.get("artist") or "").strip()
    if name and _artist_ok(name):
        return name
    if not name:
        _, top, path_artist = _path_tags(entry["path"])
        if path_artist and _artist_ok(path_artist) and top.lower() not in GENERIC_TOP:
            return path_artist
    return ""


def read_meta(path: Path) -> dict[str, str]:
    """Liest Genre/Interpret/Titel aus den Datei-Tags (schnell, ohne Audio-Decode)."""
    if not HAS_MUTAGEN:
        return {"meta_done": True}
    try:
        m = mutagen.File(str(path), easy=True)
    except Exception:
        return {"meta_done": True}
    if m is None:
        return {"meta_done": True}

    def first(key: str) -> str:
        v = m.get(key)
        if not v:
            return ""
        if isinstance(v, (list, tuple)):
            return str(v[0]).strip()
        return str(v).strip()

    return {
        "genre": first("genre"),
        "artist": first("artist"),
        "title": first("title"),
        "album": first("album"),
        "meta_done": True,
    }


def read_cover(path: Path) -> tuple[bytes, str] | None:
    """Eingebettetes Cover-Bild aus den Tags lesen (MP3/FLAC/MP4/Ogg)."""
    if not HAS_MUTAGEN:
        return None
    try:
        m = mutagen.File(str(path))
    except Exception:
        return None
    if m is None:
        return None
    pics = getattr(m, "pictures", None)
    if pics:
        return bytes(pics[0].data), pics[0].mime or "image/jpeg"
    tags = getattr(m, "tags", None)
    if tags:
        for key in tags.keys():
            if str(key).startswith("APIC"):
                fr = tags[key]
                return bytes(fr.data), fr.mime or "image/jpeg"
        if "covr" in tags:
            v = tags["covr"]
            raw = bytes(v[0])
            fmt = getattr(v[0], "imageformat", None)
            return raw, ("image/png" if fmt == 14 else "image/jpeg")
        mbp = tags.get("metadata_block_picture")
        if mbp:
            try:
                import base64

                from mutagen.flac import Picture
                pic = Picture(base64.b64decode(mbp[0]))
                return bytes(pic.data), pic.mime or "image/jpeg"
            except Exception:
                pass
    return None


def _path_tags(track_path: str) -> tuple[str, str, str]:
    """Liefert (tag, top_folder, artist) aus dem Dateipfad.
    - Bei echten Genre-Ordnern (z.B. Klassik, Kinderlieder) ist tag=Ordner.
    - Bei generischen Ordnern (Alben und Singles) ist tag=Interpret.
    """
    try:
        rel = Path(track_path).relative_to(AUDIO_DIR)
        parts = rel.parts
    except Exception:
        return ("Unbekannt", "", "")
    if not parts:
        return ("Unbekannt", "", "")
    top = parts[0]
    artist = parts[1] if len(parts) > 1 else ""
    if top.lower() in GENERIC_TOP:
        tag = artist or top
    else:
        tag = top
    return (tag, top, artist)



def compute_landscape() -> None:
    global landscape, landscape_status, pending_landscape
    if not landscape_lock.acquire(blocking=False):
        pending_landscape = True
        print("[*] Landschaft-Berechnung läuft bereits – Nachlauf eingeplant")
        return
    try:
        _compute_landscape_impl()
    finally:
        landscape_lock.release()
    if pending_landscape:
        pending_landscape = False
        compute_landscape()


def _compute_landscape_impl() -> None:
    global landscape, landscape_status
    landscape_status = {"running": True, "done": False, "message": "Berechne Landschaft…"}
    try:
        ids = [fid for fid, d in database.items()
               if "features" in d and (not active_ids or fid in active_ids)]
        n = len(ids)
        if n < 8:
            landscape_status = {"running": False, "done": False, "message": "Zu wenige Stücke"}
            return

        X = np.array([embedding_vector(database[i]["features"]) for i in ids], dtype=np.float32)
        X = np.nan_to_num(X)
        scaler = StandardScaler()
        Xs = scaler.fit_transform(X)
        weights = np.array([1.6, 1.0, 0.8, 0.8, 0.25] + [1.0] * 13, dtype=np.float32)
        Xs = Xs * weights

        if HAS_UMAP:
            reducer = umap.UMAP(n_neighbors=min(25, n - 1), min_dist=0.12,
                                n_components=3, metric="euclidean", random_state=42)
            coords = np.asarray(reducer.fit_transform(Xs), dtype=np.float32)
        else:
            coords = PCA(n_components=3).fit_transform(Xs)

        labels = np.zeros(n, dtype=int)  # Fallback, falls KMeans scheitert

        # Normalisieren auf -1..1 (3D-Welt)
        def norm3(a: np.ndarray) -> np.ndarray:
            c = float(a.mean())
            m = float(np.abs(a - c).max()) or 1.0
            return (a - c) / m
        nx = norm3(coords[:, 0])
        ny = norm3(coords[:, 1])
        nz = norm3(coords[:, 2])

        # Regionen im 3D-Layout
        k = int(max(4, min(26, round(n / 600))))
        try:
            coords3 = np.column_stack([nx, ny, nz]).astype(np.float32)
            labels = KMeans(n_clusters=min(k, max(1, n // 8)), n_init=8,
                            random_state=42).fit_predict(coords3)
        except Exception:
            pass

        # Gesamt-Bibliothek für charakteristische Merkmale (Lift)
        all_artist: Counter = Counter()
        for _id in ids:
            artist = _track_artist(database[_id])
            if artist:
                all_artist[artist] += 1

        clusters = []
        names_data: list[dict[str, Any]] = []
        uniq = [c for c in sorted(set(int(x) for x in labels)) if c != -1]
        for ci, lab in enumerate(uniq):
            idx = [k for k in range(n) if int(labels[k]) == lab]
            cx = float(np.mean(nx[idx]))
            cy = float(np.mean(ny[idx]))
            cz = float(np.mean(nz[idx]))

            tag_counts: Counter = Counter()
            folder_counts: Counter = Counter()
            artist_counts: Counter = Counter()
            for k in idx:
                entry = database[ids[k]]
                tag, top, _ = _path_tags(entry["path"])
                tag_counts[tag] += 1
                if top:
                    folder_counts[top] += 1
                artist = _track_artist(entry)
                if artist:
                    artist_counts[artist] += 1

            folder = next((fo for fo, _ in folder_counts.most_common()
                           if fo.lower() not in GENERIC_TOP), "")
            top_tag = tag_counts.most_common(1)[0][0] if tag_counts else ""
            top_artist = artist_counts.most_common(1)[0][0] if artist_counts else ""
            size = len(idx)

            # Name = Interpret(en): bevorzugt überrepräsentierte "charakteristische"
            # Interpreten, sonst die häufigsten der Region.
            chars: list[tuple[float, str]] = []
            for a, c in artist_counts.items():
                if c < 8:
                    continue
                rate = all_artist.get(a, 0) / max(1, n)
                lift = (c / size) / max(rate, 1e-6)
                if lift >= 1.8:
                    chars.append((lift * float(np.log2(2 + c)), a))
            chars.sort(key=lambda t: -t[0])

            order: list[str] = [a for _, a in chars]
            for a, _c in artist_counts.most_common(10):
                if a not in order:
                    order.append(a)

            base = order[0] if order else (top_artist or top_tag or "Gemischt")

            clusters.append({
                "id": ci,
                "label": lab,
                "color": CLUSTER_PALETTE[ci % len(CLUSTER_PALETTE)],
                "name": base,
                "folder": folder,
                "top_artist": top_artist,
                "genre": "",
                "subtitle": "",
                "x": cx,
                "y": cy,
                "z": cz,
                "size": size,
                "_ac": artist_counts.copy(),
                "_chars": order,
            })
            names_data.append({
                "cands": list(order),
                "ac": artist_counts.copy(),
                "fallback": base,
                "last": -1,
            })

        # Gleichnamige Regionen mit charakteristischen Interpreten unterscheiden
        name_count = Counter(c["name"] for c in clusters)
        taken = {c["name"] for c in clusters if name_count[c["name"]] == 1}
        for c in clusters:
            if name_count[c["name"]] <= 1:
                continue
            for a in c.pop("_chars", []):
                cand = f"{c['name']}, {_short_name(a)}"
                if cand not in taken:
                    c["name"] = cand
                    taken.add(cand)
                    break

        # Reicht das nicht, bleibt die Nummerierung als letzte Option
        seen: Counter = Counter()
        for c in clusters:
            seen[c["name"]] += 1
            k = seen[c["name"]]
            if k > 1:
                c["name"] = c["name"] + (ROMAN[k - 1] if k <= 10 else f" {k}")

        # Untertitel: weitere prägende Interpreten der Region
        for c in clusters:
            c.pop("_gc", None)
            ac = c.pop("_ac")
            c.pop("_chars", None)
            parts = {p.strip() for p in re.split(r"[&·,]", c["name"]) if p.strip()}
            size = c["size"]
            artists = [a for a, cnt in ac.most_common(6)
                       if a not in parts and cnt / size >= 0.06][:3]
            c["subtitle"] = ", ".join(artists)

        points = {}
        lab_to_cluster = {c["label"]: c for c in clusters}
        for k in range(n):
            lab = int(labels[k])
            cl = lab_to_cluster.get(lab)
            points[ids[k]] = {
                "x": float(nx[k]),
                "y": float(ny[k]),
                "z": float(nz[k]),
                "cluster": cl["id"] if cl else -1,
                "color": cl["color"] if cl else "#475569",
            }

        landscape = {
            "ready": True,
            "layout": "umap" if HAS_UMAP else "pca",
            "points": points,
            "clusters": clusters,
            "names": names_data,
            "count": n,
        }
        landscape_status = {"running": False, "done": True,
                            "message": f"{len(clusters)} Regionen, {n} Stücke"}
        print(f"[+] Landschaft berechnet: {len(clusters)} Cluster, {n} Punkte")
    except Exception as e:
        print(f"[!] Landschaft-Fehler: {e}")
        landscape_status = {"running": False, "done": False, "message": f"Fehler: {e}"}


ROMAN = ["", " II", " III", " IV", " V", " VI", " VII", " VIII", " IX", " X"]


def _rotated_clusters() -> list[dict[str, Any]]:
    """Wählt pro Region einen neuen repräsentativen Namen; bei jedem Aufruf
    (also jedem Reload) wechseln die Titel, die die Regionen darstellen."""
    clusters = landscape.get("clusters", [])
    data = landscape.get("names", [])
    if not clusters or not data:
        return clusters
    named = []
    for c, nd in zip(clusters, data):
        cands = nd["cands"][:6]
        if cands:
            pool = range(len(cands))
            idx = random.choice([i for i in pool if i != nd.get("last", -1)]) \
                if len(cands) > 1 else 0
            nd["last"] = idx
            base = cands[idx]
        else:
            base = nd["fallback"]
        named.append({**c, "name": base})

    # Gleichnamige Regionen mit charakteristischen Interpreten unterscheiden
    name_count = Counter(c["name"] for c in named)
    taken = {c["name"] for c in named if name_count[c["name"]] == 1}
    for c, nd in zip(named, data):
        if name_count[c["name"]] <= 1:
            continue
        for a in nd["cands"]:
            cand = f"{c['name']}, {_short_name(a)}"
            if cand not in taken:
                c["name"] = cand
                taken.add(cand)
                break

    seen: Counter = Counter()
    for c in named:
        seen[c["name"]] += 1
        k = seen[c["name"]]
        if k > 1:
            c["name"] += ROMAN[k - 1] if k <= 10 else f" {k}"

    for c, nd in zip(named, data):
        ac: Counter = nd["ac"]
        size = c["size"]
        parts = {p.strip() for p in re.split(r"[&·,]", c["name"]) if p.strip()}
        artists = [a for a, cnt in ac.most_common(6)
                   if a not in parts and cnt / size >= 0.06][:3]
        c["subtitle"] = ", ".join(artists)
    return named


# ---------------------------------------------------------------------------
# Datenbank
# ---------------------------------------------------------------------------

def load_db() -> None:
    global database
    if DB_FILE.exists():
        try:
            database = json.loads(DB_FILE.read_text(encoding="utf-8"))
        except Exception:
            database = {}


def save_db() -> None:
    write_json(DB_FILE, database)


def load_config() -> None:
    """Liest cache/config.json und setzt daraus AUDIO_DIR."""
    global config, AUDIO_DIR
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            config = {"root": "", "paths": []}
    config.setdefault("root", "")
    config.setdefault("paths", [])
    if config["root"]:
        root = Path(config["root"]).expanduser()
        if root.is_dir():
            AUDIO_DIR = root.resolve()
    if not AUDIO_DIR.is_dir():
        AUDIO_DIR = DEFAULT_ROOT


def save_config() -> None:
    write_json(CONFIG_FILE, config)


def _selected_roots() -> list[Path]:
    if config.get("paths"):
        roots = [AUDIO_DIR / p for p in config["paths"]]
        return [r for r in roots if r.exists()]
    return [AUDIO_DIR]


def scan_audio() -> list[Path]:
    seen: set[str] = set()
    files: list[Path] = []
    for root in _selected_roots():
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
                sp = str(p)
                if sp not in seen:
                    seen.add(sp)
                    files.append(p)
    return sorted(files)


def refresh_active() -> None:
    global active_ids
    active_ids = {str(p) for p in scan_audio()}


# ---------------------------------------------------------------------------
# Hintergrund-Analyse
# ---------------------------------------------------------------------------

def request_rescan() -> None:
    """Analyse anstossen; läuft bereits eine, wird danach erneut gelaufen."""
    global pending_rescan
    if analysis_status.get("running"):
        pending_rescan = True
        return
    executor.submit(analyse_all)


def analyse_all() -> None:
    global analysis_status, database, map_coords, active_ids, pending_rescan
    if not analysis_lock.acquire(blocking=False):
        pending_rescan = True
        print("[*] Analyse läuft bereits – Nachlauf eingeplant")
        return
    try:
        files = scan_audio()
        active_ids = {str(p) for p in files}
        analysis_status.update({"running": True, "progress": 0, "total": len(files), "done": False})

        done = 0
        for path in files:
            fid = str(path)
            if fid not in database or "features" not in database[fid]:
                feats = extract_features(path)
                if feats is not None:
                    database[fid] = {
                        "id": fid,
                        "path": str(path),
                        "name": path.name,
                        "features": feats,
                    }
            if fid in database:
                if not database[fid].get("meta_done"):
                    database[fid].update(read_meta(path))
                map_coords[fid] = compute_coord(database[fid]["features"])
            done += 1
            analysis_status["progress"] = done
            if done % 100 == 0:
                save_db()

        save_db()
        analysis_status.update({"running": False, "progress": done, "total": len(files), "done": True})
        print(f"[+] Analyse abgeschlossen: {done} Stücke, {len(database)} in DB")
    finally:
        analysis_lock.release()

    compute_landscape()

    if pending_rescan:
        pending_rescan = False
        print("[*] Übernehme geänderte Auswahl …")
        analyse_all()


def rebuild_coords() -> None:
    global map_coords
    map_coords = {fid: compute_coord(d["features"]) for fid, d in database.items() if "features" in d}


def _startup() -> None:
    load_config()
    load_db()
    rebuild_coords()
    # Immer (weiter-)analysieren: bereits analysierte Tracks werden übersprungen.
    executor.submit(analyse_all)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/api/status")
def status() -> dict[str, Any]:
    out = dict(analysis_status)
    out["landscape_running"] = bool(landscape_status.get("running"))
    out["landscape_done"] = bool(landscape_status.get("done"))
    return out


@app.post("/api/scan")
def scan() -> dict[str, Any]:
    queued = bool(analysis_status.get("running"))
    request_rescan()
    return {"status": "queued" if queued else "started"}


_path_counts_cache: dict[str, int] = {}


def _top_dirs() -> list[Path]:
    try:
        return sorted([d for d in AUDIO_DIR.iterdir() if d.is_dir()], key=lambda p: p.name.lower())
    except Exception:
        return []


@app.get("/api/paths")
def api_paths() -> dict[str, Any]:
    dirs = _top_dirs()
    paths = []
    for d in dirs:
        if d.name not in _path_counts_cache:
            cnt = 0
            for p in d.rglob("*"):
                if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
                    cnt += 1
            _path_counts_cache[d.name] = cnt
        paths.append({"name": d.name, "count": _path_counts_cache[d.name]})
    return {
        "root": str(AUDIO_DIR),
        "paths": paths,
        "selected": config.get("paths", []),  # leer = alle
    }


@app.post("/api/root")
def api_root(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Musikwurzel zur Laufzeit ändern; wird in cache/config.json gespeichert."""
    global AUDIO_DIR, pending_landscape
    raw = str(payload.get("root", "")).strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Kein Pfad angegeben")
    root = Path(raw).expanduser()
    if not root.is_absolute():
        root = (BASE_DIR / root).resolve()
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Ordner nicht gefunden: {root}")
    AUDIO_DIR = root.resolve()
    config["root"] = str(AUDIO_DIR)
    config["paths"] = []
    save_config()
    _path_counts_cache.clear()
    landscape["ready"] = False
    if landscape_status.get("running"):
        pending_landscape = True
    request_rescan()
    note = ("Wurzel geändert – Auswahl wird nach dem laufenden Durchlauf übernommen"
            if analysis_status.get("running") else "Wurzel geändert – Analyse läuft")
    return {"status": "started", "root": str(AUDIO_DIR), "note": note}


@app.get("/api/cover/{track_id:path}")
def cover(track_id: str):
    if track_id not in database:
        raise HTTPException(status_code=404, detail="Track nicht gefunden")
    path = Path(database[track_id]["path"])
    got = read_cover(path) if path.exists() else None
    if got:
        data, mime = got
        return Response(content=data, media_type=mime,
                        headers={"Cache-Control": "public, max-age=86400"})
    for name in ("cover.jpg", "folder.jpg", "front.jpg", "album.jpg",
                 "cover.png", "folder.png", "front.png"):
        candidate = path.parent / name
        if candidate.exists():
            return FileResponse(candidate, headers={"Cache-Control": "public, max-age=86400"})
    raise HTTPException(status_code=404, detail="Kein Cover")


@app.post("/api/selection")
def api_selection(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    global pending_landscape
    wanted = payload.get("paths", [])
    valid = {d.name for d in _top_dirs()}
    selected = [p for p in wanted if p in valid]
    changed = selected != config.get("paths", [])
    config["paths"] = selected
    save_config()
    _path_counts_cache.clear()
    landscape["ready"] = False
    if landscape_status.get("running"):
        pending_landscape = True
    request_rescan()
    note = "Auswahl wird nach dem laufenden Durchlauf übernommen" if analysis_status.get("running") else ""
    return {"status": "started", "selected": selected, "changed": changed, "note": note}


@app.get("/api/map")
def get_map() -> dict[str, Any]:
    use_land = landscape.get("ready", False)
    lpoints = landscape.get("points", {}) if use_land else {}
    items = []
    for fid, data in database.items():
        if active_ids and fid not in active_ids:
            continue
        if use_land and fid in lpoints:
            p = lpoints[fid]
            x, y, z = p["x"], p["y"], p.get("z", 0.0)
            cluster = p.get("cluster", -1)
            color = p.get("color", "#475569")
        else:
            bx, by = map_coords.get(fid, (0.5, 0.5))
            x, y, z = bx * 2 - 1, by * 2 - 1, 0.0
            cluster = -1
            color = None
        items.append({
            "id": fid,
            "name": data["name"],
            "artist": (data.get("artist") or "").strip(),
            "title": (data.get("title") or "").strip() or data["name"],
            "album": (data.get("album") or "").strip(),
            "x": x,
            "y": y,
            "z": z,
            "cluster": cluster,
            "color": color,
            "tempo": float(data["features"].get("tempo", 0.0)),
            "genre": data.get("genre", "") or "",
        })
    return {
        "items": items,
        "count": len(items),
        "layout": "landscape" if use_land else "basic",
        "clusters": _rotated_clusters() if use_land else [],
    }


@app.get("/api/landscape")
def landscape_state() -> dict[str, Any]:
    return landscape_status


@app.post("/api/landscape")
def landscape_start() -> dict[str, Any]:
    if landscape_status.get("running"):
        return {"status": "running"}
    executor.submit(compute_landscape)
    return {"status": "started"}



@app.get("/api/similar/{track_id:path}")
def similar(track_id: str, limit: int = 10) -> dict[str, Any]:
    if track_id not in database:
        raise HTTPException(status_code=404, detail="Track nicht gefunden")
    base = feature_vector(database[track_id]["features"])
    use_land = landscape.get("ready", False)
    lpoints = landscape.get("points", {}) if use_land else {}

    def info_of(fid: str) -> dict[str, Any]:
        if use_land and fid in lpoints:
            p = lpoints[fid]
            return {"x": p["x"], "y": p["y"], "z": p.get("z", 0.0),
                    "cluster": p.get("cluster", -1), "color": p.get("color", "#475569")}
        bx, by = map_coords.get(fid, (0.5, 0.5))
        return {"x": bx*2-1, "y": by*2-1, "z": 0.0, "cluster": -1, "color": "#475569"}

    scored = []
    for fid, data in database.items():
        if fid == track_id:
            continue
        if active_ids and fid not in active_ids:
            continue
        v = feature_vector(data["features"])
        sim = float(np.dot(base, v) / (np.linalg.norm(base) * np.linalg.norm(v) + 1e-9))
        scored.append((sim, fid, data["name"], info_of(fid)))
    scored.sort(key=lambda t: t[0], reverse=True)
    results = [
        {
            "id": fid,
            "name": name,
            "similarity": round(sim, 3),
            "x": info["x"],
            "y": info["y"],
            "z": info["z"],
            "cluster": info["cluster"],
            "color": info["color"],
            "genre": database[fid].get("genre", ""),
            "features": database[fid]["features"],
        }
        for sim, fid, name, info in scored[:limit]
    ]
    return {"track": database[track_id]["name"], "similar": results}


LRC_TIME = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")


def parse_lrc(text: str) -> list[dict[str, Any]]:
    """LRC-Zeitmarken ([mm:ss.xx]) in eine sortierte Zeilenliste wandeln."""
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        stamps = list(LRC_TIME.finditer(line))
        if not stamps:
            continue
        body = LRC_TIME.sub("", line).strip()
        if not body:
            continue
        for m in stamps:
            secs = int(m.group(1)) * 60 + int(m.group(2))
            frac = m.group(3) or "0"
            secs += int(frac) / (10 ** len(frac))
            out.append({"t": round(secs, 2), "text": body})
    out.sort(key=lambda x: x["t"])
    return out


@app.get("/api/lyrics/{track_id:path}")
def lyrics(track_id: str) -> dict[str, Any]:
    if track_id not in database:
        raise HTTPException(status_code=404, detail="Track nicht gefunden")
    path = Path(database[track_id]["path"])
    text = ""
    synced: list[dict[str, Any]] = []
    if HAS_MUTAGEN and path.exists():
        try:
            m = mutagen.File(str(path))
        except Exception:
            m = None
        tags = getattr(m, "tags", None) if m is not None else None
        if tags:
            # SYLT = synchronisierte Lyrics (Zeile + Zeitstempel)
            try:
                for key in tags.keys():
                    if str(key).startswith("SYLT"):
                        frame = tags[key]
                        for item in getattr(frame, "text", None) or []:
                            try:
                                line, ms = item[0], item[1]
                            except Exception:
                                continue
                            line = str(line).strip()
                            if line:
                                synced.append({"t": round(float(ms) / 1000.0, 2),
                                               "text": line})
                        if synced:
                            break
            except Exception:
                synced = []
            try:
                for key in tags.keys():
                    if str(key).startswith("USLT"):
                        frame = tags[key]
                        text = getattr(frame, "text", "") or str(frame)
                        break
            except Exception:
                text = ""
            if not text:
                for key in ("\xa9lyr", "LYRICS", "UNSYNCEDLYRICS", "lyrics"):
                    try:
                        if key in tags:
                            v = tags[key]
                            text = v[0] if isinstance(v, (list, tuple)) else v
                            text = str(text).strip()
                            if text:
                                break
                    except Exception:
                        continue
    if not synced and text:
        synced = parse_lrc(text)
    if synced:
        synced.sort(key=lambda x: x["t"])
        plain = LRC_TIME.sub("", text)
        plain = "\n".join(l.strip() for l in plain.splitlines() if l.strip())
        if not plain:
            plain = "\n".join(s["text"] for s in synced)
    else:
        plain = text
    return {"lyrics": plain.strip()[:20000], "synced": synced[:2000]}


@app.get("/api/track/{track_id:path}")
def track(track_id: str) -> dict[str, Any]:
    if track_id not in database:
        raise HTTPException(status_code=404, detail="Track nicht gefunden")
    return database[track_id]


@app.get("/api/audio/{track_id:path}")
def audio(track_id: str):
    if track_id not in database:
        raise HTTPException(status_code=404, detail="Track nicht gefunden")
    path = Path(database[track_id]["path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    return FileResponse(path, media_type=MEDIA_TYPES.get(path.suffix.lower(),
                             "application/octet-stream"))


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="AcousticGalaxyM16-3")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()

    if not HAS_LIBS:
        print("[!] Bibliotheken fehlen. Führe erst: sudo python3 app.py --install-deps")
        sys.exit(1)

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
