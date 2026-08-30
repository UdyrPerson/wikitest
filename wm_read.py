"""
Lit tes propres donnees WikiMasters et les ecrit sur disque en JSON.

A remplir apres wm_discover.py + wm_map.py : les routes reelles vont dans
ENDPOINTS ci-dessous.

    python wm_read.py

Deux modes selon ce que la reconnaissance a montre :

  MODE API   le front appelle de vraies routes JSON.
             On les rejoue via ctx.request, qui partage les cookies du
             navigateur sans rendre les pages. Rapide et stable.

  MODE DOM   tout passe par des payloads RSC (text/x-component) ou des
             server actions. Les rejouer a la main est fragile : on charge
             la page et on lit le DOM rendu. Plus lent, plus robuste.
"""

import json
import random
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "https://www.wiki-masters.com"
STATE = Path("storage_state.json")
DATA = Path("data")

# Delai entre deux requetes, en secondes. Ne descends pas sous 2s.
# Un humain qui consulte sa collection ne genere pas 20 req/s.
DELAY = (2.0, 4.0)

# --- MODE API -------------------------------------------------------------
# A remplir depuis captures/routes.json. Exemples de forme attendue :
#
# ENDPOINTS = {
#     "collection": "/api/collection?page={page}",
#     "catalog":    "/api/cards?page={page}",
#     "trades":     "/api/trades",
# }
ENDPOINTS: dict[str, str] = {}

# Nom du parametre de pagination et cle de la liste dans la reponse.
PAGE_PARAM = "page"
MAX_PAGES = 50

# --- MODE DOM -------------------------------------------------------------
# Pages a charger et selecteur a attendre avant de lire le DOM.
DOM_PAGES = {
    # "collection": ("/collection", "[data-card-id], article, .card"),
}


def pause():
    time.sleep(random.uniform(*DELAY))


def fetch_api(ctx, name: str, template: str):
    """Rejoue une route JSON, en paginant tant qu'il reste des resultats."""
    out = []
    for page in range(1, MAX_PAGES + 1):
        url = BASE + template.format(page=page, **{PAGE_PARAM: page})
        resp = ctx.request.get(url)

        if resp.status in (401, 403):
            raise SystemExit(
                f"{resp.status} sur {url} — session expiree. Relance wm_session.py."
            )
        if resp.status == 429:
            print("    429 : on ralentit franchement (60s)")
            time.sleep(60)
            continue
        if resp.status >= 400:
            print(f"    {resp.status} sur {url} — on arrete cette route")
            break

        payload = resp.json()
        items = extract_items(payload)
        print(f"    page {page}: {len(items)} elements")
        if not items:
            break
        out.extend(items)

        if "{page}" not in template and f"{{{PAGE_PARAM}}}" not in template:
            break  # route non paginee
        pause()

    return out


def extract_items(payload):
    """Trouve la liste dans la reponse, quel que soit son emballage."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "data", "results", "cards", "collection", "trades"):
            v = payload.get(key)
            if isinstance(v, list):
                return v
        # dict de dicts : on prend les valeurs
        vals = list(payload.values())
        if vals and all(isinstance(v, dict) for v in vals):
            return vals
    return []


def fetch_dom(ctx, name: str, path: str, selector: str):
    """Repli : on charge la page et on lit ce qui est rendu."""
    page = ctx.new_page()
    page.goto(BASE + path, wait_until="networkidle")
    try:
        page.wait_for_selector(selector, timeout=15000)
    except Exception:
        print(f"    selecteur '{selector}' absent — ajuste-le")

    # A adapter : ici on sort le texte de chaque element trouve.
    rows = page.eval_on_selector_all(
        selector,
        "els => els.map(e => ({text: e.innerText.trim(), html: e.outerHTML.slice(0,500)}))",
    )
    page.close()
    print(f"    {len(rows)} elements lus dans le DOM")
    return rows


def main():
    if not STATE.exists():
        raise SystemExit("Pas de storage_state.json — lance d'abord wm_session.py")
    if not ENDPOINTS and not DOM_PAGES:
        raise SystemExit(
            "Rien a lire : remplis ENDPOINTS ou DOM_PAGES depuis captures/routes.json"
        )

    DATA.mkdir(exist_ok=True)
    stamp = time.strftime("%Y-%m-%d")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel="chrome")
        ctx = browser.new_context(
            storage_state=str(STATE),
            base_url=BASE,
            viewport={"width": 1280, "height": 900},
        )

        for name, template in ENDPOINTS.items():
            print(f"\n[api] {name}")
            rows = fetch_api(ctx, name, template)
            path = DATA / f"{name}-{stamp}.json"
            path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"    -> {len(rows)} elements dans {path}")
            pause()

        for name, (path_, selector) in DOM_PAGES.items():
            print(f"\n[dom] {name}")
            rows = fetch_dom(ctx, name, path_, selector)
            out = DATA / f"{name}-{stamp}.json"
            out.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"    -> {out}")
            pause()

        browser.close()


if __name__ == "__main__":
    main()
