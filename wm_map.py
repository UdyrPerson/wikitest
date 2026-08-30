"""
Transforme captures/index.jsonl en cartographie lisible : regroupe les appels
par forme d'URL, normalise les identifiants, liste les parametres vus.

    python wm_map.py

Sortie a l'ecran + captures/routes.json
"""

import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qs, urlparse

OUT = Path("captures")
INDEX = OUT / "index.jsonl"

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
NUM = re.compile(r"^\d+$")
HEXID = re.compile(r"^[0-9a-z]{16,}$", re.I)


def normalize(path: str) -> str:
    parts = []
    for seg in path.split("/"):
        if UUID.match(seg):
            parts.append("{uuid}")
        elif NUM.match(seg):
            parts.append("{id}")
        elif HEXID.match(seg):
            parts.append("{hash}")
        else:
            parts.append(seg)
    return "/".join(parts)


def peek(body_file: str, limit: int = 400) -> str:
    f = OUT / body_file
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    text = text.strip()
    try:
        parsed = json.loads(text)
        return json.dumps(parsed, ensure_ascii=False)[:limit]
    except Exception:
        return text[:limit].replace("\n", " ")


def shape(body_file: str):
    """Cles de premier niveau d'une reponse JSON, pour voir la structure."""
    try:
        data = json.loads((OUT / body_file).read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(data, dict):
        return sorted(data.keys())[:15]
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return ["[]"] + sorted(data[0].keys())[:15]
    return type(data).__name__


def main():
    if not INDEX.exists():
        raise SystemExit("Pas de captures/index.jsonl — lance d'abord wm_discover.py")

    routes = defaultdict(lambda: {
        "count": 0, "statuses": set(), "params": set(),
        "content_types": set(), "shapes": [], "sample": "", "server_action": False,
    })

    for line in INDEX.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        u = urlparse(e["url"])
        key = f"{e['method']} {normalize(u.path)}"
        r = routes[key]
        r["count"] += 1
        r["statuses"].add(e["status"])
        r["content_types"].add(e["content_type"].split(";")[0])
        for k in parse_qs(u.query):
            r["params"].add(k)
        if e.get("req_headers", {}).get("next-action"):
            r["server_action"] = True
        s = shape(e["body_file"])
        if s and s not in r["shapes"]:
            r["shapes"].append(s)
        if not r["sample"]:
            r["sample"] = peek(e["body_file"])

    print(f"{len(routes)} routes distinctes\n")
    export = {}
    for key in sorted(routes, key=lambda k: -routes[k]["count"]):
        r = routes[key]
        print(f"{key}")
        print(f"    appels     {r['count']}   statuts {sorted(r['statuses'])}")
        if r["params"]:
            print(f"    params     {sorted(r['params'])}")
        print(f"    type       {sorted(r['content_types'])}")
        if r["server_action"]:
            print("    /!\\ server action Next.js (en-tete Next-Action) — pas une route REST")
        for s in r["shapes"][:3]:
            print(f"    cles       {s}")
        if r["sample"]:
            print(f"    extrait    {r['sample'][:200]}")
        print()
        export[key] = {
            "count": r["count"],
            "statuses": sorted(r["statuses"]),
            "params": sorted(r["params"]),
            "content_types": sorted(r["content_types"]),
            "shapes": r["shapes"],
            "server_action": r["server_action"],
        }

    (OUT / "routes.json").write_text(
        json.dumps(export, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Ecrit dans {(OUT / 'routes.json').resolve()}")


if __name__ == "__main__":
    main()
