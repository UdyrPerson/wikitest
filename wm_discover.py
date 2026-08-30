"""
Rouvre Chrome avec ta session et enregistre tout ce que le front appelle
pendant que tu navigues a la main.

C'est l'etape de reconnaissance : elle repond a "quelles routes existent
et que renvoient-elles", sans avoir a deviner.

    python wm_discover.py

Navigue vers /collection, /pulls, /trades, une fiche de carte, une recherche,
la pagination. Chaque page visitee revele ses appels. Puis Entree pour arreter.

Sortie :
    captures/index.jsonl   une ligne par requete (methode, url, statut, taille)
    captures/bodies/       le corps de chaque reponse, un fichier par appel
"""

import hashlib
import json
import os
import random
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

BASE = "https://www.wiki-masters.com"
HOST = urlparse(BASE).netloc
STATE = Path("storage_state.json")
OUT = Path("captures")
BODIES = OUT / "bodies"

# Ce qui nous interesse : JSON classique, et les payloads React Server
# Components de Next.js (text/x-component), qui transportent souvent les
# donnees sans qu'aucune route /api ne soit visible.
KEEP_TYPES = ("application/json", "text/x-component", "text/plain")

# Bruit a ignorer.
SKIP_EXT = (".js", ".css", ".woff", ".woff2", ".png", ".jpg", ".jpeg",
            ".webp", ".svg", ".ico", ".map", ".avif")

# WM_DEBUG_ALL=1 desactive le filtre content-type pour voir tout le trafic
# brut (y compris les pages HTML) quand KEEP_TYPES ne capture rien : ca sert
# a diagnostiquer la forme reelle des reponses avant d'ajuster le filtre.
DEBUG_ALL = os.environ.get("WM_DEBUG_ALL") == "1"


def interesting(response) -> bool:
    url = response.url
    if urlparse(url).netloc != HOST:
        return False
    path = urlparse(url).path.lower()
    if path.endswith(SKIP_EXT):
        return False
    if "/_next/static/" in path:
        return False
    if DEBUG_ALL:
        return True
    ctype = (response.headers.get("content-type") or "").lower()
    if response.request.method != "GET":
        return True  # tout POST/PATCH/DELETE est notable
    return any(t in ctype for t in KEEP_TYPES)


# Intitules de menu courants a essayer automatiquement. Ceux qui n'existent
# pas sur le site sont simplement ignores (timeout court, on passe au suivant).
# list_links() ci-dessous affiche les vrais liens de la page : ajuste cette
# liste avec les libelles exacts qu'elle montre plutot que de deviner.
NAV_GUESSES = [
    "Collection", "Pulls", "Échanges", "Trades", "Catalogue",
    "Boutique", "Cartes", "Classement", "Profil",
]


def list_links(page):
    """Affiche tous les liens <a href> presents sur la page : sert a savoir
    quels libelles exacts mettre dans NAV_GUESSES au lieu de deviner."""
    links = page.eval_on_selector_all(
        "a[href]",
        "els => els.map(e => ({text: e.innerText.trim(), href: e.getAttribute('href')}))",
    )
    seen = set()
    print(f"Liens trouves sur {page.url} :")
    for l in links:
        key = (l["text"], l["href"])
        if key in seen:
            continue
        seen.add(key)
        if l["text"]:
            print(f"    {l['text']!r:30} -> {l['href']}")
    print()


def auto_explore(page):
    """Clique automatiquement sur les liens de menu qui existent parmi
    NAV_GUESSES, revient a la page de depart entre deux, et laisse
    on_response capturer ce que chaque navigation declenche."""
    print("Passe automatique : clic sur les liens de menu courants.\n")
    start_url = page.url

    for label in NAV_GUESSES:
        try:
            link = page.get_by_role("link", name=label).first
            link.wait_for(state="visible", timeout=2000)
        except Exception:
            continue  # ce lien n'existe pas sur le site, on passe

        try:
            link.click(timeout=3000)
            page.wait_for_load_state("networkidle", timeout=8000)
            print(f"  -> clique sur '{label}' : {page.url}")
            time.sleep(random.uniform(1.0, 2.0))  # laisse la place a une navigation humaine
            page.goto(start_url)
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception as e:
            print(f"  -> '{label}' : echec ({e.__class__.__name__})")

    print("\nPasse automatique terminee.\n")


def main():
    if not STATE.exists():
        raise SystemExit("Pas de storage_state.json — lance d'abord wm_session.py")

    BODIES.mkdir(parents=True, exist_ok=True)
    index = OUT / "index.jsonl"
    seen = 0

    with sync_playwright() as p:
        # Memes flags que wm_session.py : on retire les marqueurs d'automatisation
        # les plus visibles pour Cloudflare. Le cookie cf_clearance deja present
        # dans storage_state.json devrait de toute facon eviter un nouveau
        # challenge tant qu'on reste sur le meme Chrome reel.
        browser = p.chromium.launch(
            headless=False,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        ctx = browser.new_context(
            storage_state=str(STATE),
            viewport={"width": 1280, "height": 900},
        )

        log = index.open("a", encoding="utf-8")

        def on_response(response):
            nonlocal seen
            try:
                if not interesting(response):
                    return
            except Exception:
                return

            req = response.request
            try:
                body = response.body()
            except Exception:
                body = b""

            key = hashlib.sha1(
                f"{req.method}{response.url}{seen}".encode()
            ).hexdigest()[:12]
            (BODIES / f"{key}.txt").write_bytes(body)

            entry = {
                "n": seen,
                "method": req.method,
                "url": response.url,
                "status": response.status,
                "content_type": response.headers.get("content-type", ""),
                "bytes": len(body),
                "body_file": f"bodies/{key}.txt",
                # Utile pour reperer les server actions Next.js et les
                # en-tetes reellement necessaires a la requete.
                "req_headers": {
                    k: v for k, v in req.headers.items()
                    if k.lower() in ("next-action", "next-router-state-tree",
                                     "content-type", "accept", "rsc")
                },
                "post_data": (req.post_data or "")[:2000],
            }
            try:
                log.write(json.dumps(entry, ensure_ascii=False) + "\n")
                log.flush()
            except ValueError:
                return  # log deja ferme (capture en cours d'arret)
            seen += 1
            print(f"  [{entry['status']}] {req.method:6} {entry['bytes']:>7}o  {response.url[:110]}")

        ctx.on("response", on_response)

        page = ctx.new_page()
        page.goto(BASE)
        page.wait_for_load_state("networkidle", timeout=8000)

        list_links(page)
        auto_explore(page)

        print("Continue a la main si besoin (fiche de carte, pagination...).")
        print("Chaque appel apparait ci-dessous. Entree pour arreter.\n")
        input()

        # Laisse les requetes encore en vol au moment ou tu appuies sur Entree
        # se terminer avant de fermer le fichier et le navigateur.
        ctx.remove_listener("response", on_response)
        time.sleep(1.5)
        log.close()
        browser.close()

    print(f"\n{seen} appels captures dans {index.resolve()}")


if __name__ == "__main__":
    main()
