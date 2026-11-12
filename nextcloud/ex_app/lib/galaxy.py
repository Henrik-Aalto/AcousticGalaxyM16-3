"""Audio-Features, 3D-Landschaft und Regionsnamen.

Portiert aus dem Standalone-Backend (app.py), aber ohne Dateisystem- und
Datenbankzugriff: die Funktionen arbeiten auf reinen Track-Dicts.
"""

from __future__ import annotations

import random
import re
from collections import Counter
from typing import Any

import numpy as np

CLUSTER_PALETTE = [
    "#38bdf8", "#f472b6", "#a3e635", "#f59e0b", "#a78bfa",
    "#22d3ee", "#fb7185", "#34d399", "#fbbf24", "#818cf8",
    "#2dd4bf", "#f97316", "#e879f9", "#4ade80", "#60a5fa",
    "#f87171", "#c084fc", "#facc15",
]

SAMPLER_WORDS = (
    "kuschelrock", "bravo hits", "sampler", "sammlung", "samplerreihe",
    "best of", "collection", "box", "the hits", "now that's", "vol.",
    "various", "verschiedene",
)

ROMAN = ["", " II", " III", " IV", " V", " VI", " VII", " VIII", " IX", " X"]

try:  # optionale Abhängigkeiten
    import umap  # noqa: F401
    HAS_UMAP = True
except Exception:  # pragma: no cover - Umgebung ohne umap
    HAS_UMAP = False

try:
    from sklearn.cluster import KMeans
    from sklearn.decomposition import PCA
    from sklearn.preprocessing import StandardScaler
    HAS_SKLEARN = True
except Exception:  # pragma: no cover - Umgebung ohne sklearn
    HAS_SKLEARN = False


# ---------------------------------------------------------------------------
# Feature-Extraktion (librosa)
# ---------------------------------------------------------------------------

def extract_features(path: str) -> dict[str, Any] | None:
    """Analysiert die ersten 60 Sekunden einer Audiodatei."""
    try:
        import librosa

        y, sr = librosa.load(path, sr=22050, mono=True, duration=60)
        if len(y) == 0:
            return None

        tempo_arr, _ = librosa.beat.beat_track(y=y, sr=sr)
        tempo = float(np.mean(np.atleast_1d(tempo_arr))) if np.size(tempo_arr) else 0.0

        chroma = librosa.feature.chroma_stft(y=y, sr=sr)
        key_idx = int(np.argmax(np.mean(chroma, axis=1)))
        key_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        key = key_names[key_idx % 12]

        cent = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
        rms = librosa.feature.rms(y=y)[0]
        zcr = librosa.feature.zero_crossing_rate(y=y)[0]
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)

        return {
            "tempo": tempo,
            "key": key,
            "spectral_centroid": float(np.mean(cent)),
            "rms_energy": float(np.mean(rms)),
            "zero_crossing_rate": float(np.mean(zcr)),
            "mfcc": [float(x) for x in np.mean(mfcc, axis=1)],
            "duration": float(librosa.get_duration(y=y, sr=sr)),
        }
    except Exception:
        return None


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


def feature_vector(f: dict[str, Any]) -> np.ndarray:
    """Normalisierter Vektor für die Ähnlichkeitssuche."""
    vec = np.array(embedding_vector(f), dtype=np.float32)
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec = vec / norm
    return vec


# ---------------------------------------------------------------------------
# Regionsnamen
# ---------------------------------------------------------------------------

def short_name(name: str, limit: int = 42) -> str:
    name = (name or "").strip()
    return name if len(name) <= limit else name[: limit - 1].rstrip() + "…"


def _artist_ok(name: str) -> bool:
    low = (name or "").lower()
    return bool(name) and not any(w in low for w in SAMPLER_WORDS)


def _norm3(a: np.ndarray) -> np.ndarray:
    c = float(a.mean())
    m = float(np.abs(a - c).max()) or 1.0
    return (a - c) / m


def build_landscape(tracks: list[dict[str, Any]]) -> dict[str, Any]:
    """Berechnet Punkte, Cluster und Namenskandidaten.

    ``tracks`` enthält Dicts mit ``id``, ``features``, ``artist``, ``album``,
    ``genre`` und ``title``.
    """
    usable = [t for t in tracks if t.get("features")]
    n = len(usable)
    if n < 8 or not HAS_SKLEARN:
        return {"layout": "basic", "count": n, "points": {}, "clusters": [], "names": []}

    X = np.array([embedding_vector(t["features"]) for t in usable], dtype=np.float32)
    X = np.nan_to_num(X)
    Xs = StandardScaler().fit_transform(X)
    weights = np.array([1.6, 1.0, 0.8, 0.8, 0.25] + [1.0] * 13, dtype=np.float32)
    Xs = Xs * weights

    if HAS_UMAP and n > 10:
        reducer = umap.UMAP(n_neighbors=min(25, n - 1), min_dist=0.12,
                            n_components=3, metric="euclidean", random_state=42)
        coords = np.asarray(reducer.fit_transform(Xs), dtype=np.float32)
        layout = "umap"
    else:
        coords = PCA(n_components=3, random_state=42).fit_transform(Xs)
        layout = "pca"

    labels = np.zeros(n, dtype=int)
    nx, ny, nz = _norm3(coords[:, 0]), _norm3(coords[:, 1]), _norm3(coords[:, 2])

    k = int(max(4, min(26, round(n / 600))))
    try:
        coords3 = np.column_stack([nx, ny, nz]).astype(np.float32)
        labels = KMeans(n_clusters=min(k, max(1, n // 8)), n_init=8,
                        random_state=42).fit_predict(coords3)
    except Exception:
        pass

    all_artist: Counter = Counter()
    for t in usable:
        if t.get("artist"):
            all_artist[t["artist"]] += 1

    clusters: list[dict[str, Any]] = []
    names_data: list[dict[str, Any]] = []
    for ci, lab in enumerate(sorted(set(int(x) for x in labels))):
        idx = [i for i in range(n) if int(labels[i]) == lab]
        if not idx:
            continue
        cx = float(np.mean(nx[idx]))
        cy = float(np.mean(ny[idx]))
        cz = float(np.mean(nz[idx]))
        size = len(idx)

        artist_counts: Counter = Counter()
        genre_counts: Counter = Counter()
        album_counts: Counter = Counter()
        for i in idx:
            t = usable[i]
            if t.get("artist"):
                artist_counts[t["artist"]] += 1
            if t.get("genre"):
                genre_counts[t["genre"]] += 1
            if t.get("album"):
                album_counts[t["album"]] += 1

        top_artist = artist_counts.most_common(1)[0][0] if artist_counts else ""
        top_genre = genre_counts.most_common(1)[0][0] if genre_counts else ""
        album = album_counts.most_common(1)[0][0] if album_counts else ""

        chars: list[tuple[float, str]] = []
        for artist, count in artist_counts.items():
            if count < 8:
                continue
            rate = all_artist.get(artist, 0) / max(1, n)
            lift = (count / size) / max(rate, 1e-6)
            if lift >= 1.8:
                chars.append((lift * float(np.log2(2 + count)), artist))
        chars.sort(key=lambda t: -t[0])

        order = [a for _, a in chars]
        for artist, _count in artist_counts.most_common(10):
            if artist not in order:
                order.append(artist)

        base = order[0] if order else (top_artist or top_genre or "Gemischt")

        clusters.append({
            "id": ci,
            "label": lab,
            "color": CLUSTER_PALETTE[ci % len(CLUSTER_PALETTE)],
            "name": base,
            "folder": album,
            "top_artist": top_artist,
            "genre": top_genre,
            "subtitle": "",
            "x": cx, "y": cy, "z": cz,
            "size": size,
        })
        names_data.append({
            "cands": list(order),
            "ac": dict(artist_counts),
            "fallback": base,
            "last": -1,
        })

    points: dict[str, Any] = {}
    lab_to_cluster = {c["label"]: c for c in clusters}
    for i, t in enumerate(usable):
        cl = lab_to_cluster.get(int(labels[i]))
        points[t["id"]] = {
            "x": float(nx[i]), "y": float(ny[i]), "z": float(nz[i]),
            "cluster": cl["id"] if cl else -1,
            "color": cl["color"] if cl else "#475569",
        }

    result = {"layout": layout, "count": n, "points": points,
              "clusters": clusters, "names": names_data}
    return result


def rotate_clusters(landscape: dict[str, Any]) -> list[dict[str, Any]]:
    """Liefert Regions-Kopien mit pro Aufruf wechselnden Repräsentanten."""
    clusters = landscape.get("clusters", [])
    data = landscape.get("names", [])
    if not clusters or not data:
        return clusters

    named: list[dict[str, Any]] = []
    for c, nd in zip(clusters, data):
        cands = nd["cands"][:6]
        if cands:
            pool = [i for i in range(len(cands)) if i != nd.get("last", -1)]
            idx = random.choice(pool) if pool else 0
            nd["last"] = idx
            base = cands[idx]
        else:
            base = nd["fallback"]
        named.append({**c, "name": base})

    name_count = Counter(c["name"] for c in named)
    taken = {c["name"] for c in named if name_count[c["name"]] == 1}
    for c, nd in zip(named, data):
        if name_count[c["name"]] <= 1:
            continue
        for artist in nd["cands"]:
            cand = f"{c['name']}, {short_name(artist)}"
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
        ac = Counter(nd["ac"])
        size = max(1, c["size"])
        parts = {p.strip() for p in re.split(r"[&·,]", c["name"]) if p.strip()}
        artists = [a for a, cnt in ac.most_common(6)
                   if a not in parts and cnt / size >= 0.06][:3]
        c["subtitle"] = ", ".join(artists)
    return named
