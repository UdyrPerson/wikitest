"""
Gere la fenetre Chrome persistante utilisee pour ouvrir des boosters sur
/pulls, en reutilisant la session sauvegardee par wm_session.py.

La page /pulls est tres dynamique cote client (cf CLAUDE.md) : la capture
HTML statique ne montre aucun bouton "Ouvrir" exploitable, l'ouverture d'un
pack declenche une animation JS (les sons pack-rip.mp3 et card-flip.mp3
sont precharges des l'arrivee sur la page).

    python wm_open_booster.py            # ouvre/rattache la fenetre, sans agir
    python wm_open_booster.py --recon     # + repere les elements, screenshot
    python wm_open_booster.py --click     # + ouvre un booster (voir plus bas)
    python wm_open_booster.py --api [--count N]   # sans navigateur, voir plus bas

Fenetre persistante
--------------------
Ce script ne lance jamais un Chrome directement via Playwright
(chromium.launch()) : un Chrome lance ainsi est tue par Playwright des que
le script se termine, meme sans appel explicite a browser.close(). A la
place, meme pattern que wm_session_cdp.py : Chrome est lance comme un
processus independant, avec --remote-debugging-port fixe (CDP_PORT), et
Playwright s'y branche seulement pour piloter la page. Un Chrome branche de
cette facon n'est jamais ferme par Playwright, quoi qu'il arrive.

Le port etant fixe, chaque lancement de ce script (ou de l'outil
wm_ouverture_booster.py) commence par essayer de s'y brancher : si un
Chrome tourne deja dessus (lance par un appel precedent), on reutilise sa
fenetre et son onglet WikiMasters existants au lieu d'en ouvrir un nouveau.
Sinon, on le lance.

Modes
-----
  (aucune option)  Ouvre/rattache la fenetre, va sur /pulls si besoin, et
                    s'arrete la. Aucune action de jeu.

  --recon           Comme ci-dessus, + repere les elements cliquables
                    plausibles (image du pack, bouton "ouvrir"...), prend
                    une capture d'ecran dans captures/screenshots/. Toujours
                    sans risque : ne clique rien.

  --click           Ouvre reellement un booster (consomme un vrai booster
                    du compte) : clique sur le paquet, defile les 5 cartes
                    via le bouton "suivant", clique "Continuer". Capture le
                    trafic reseau declenche dans captures/index.jsonl (meme
                    format que wm_discover.py, donc wm_map.py peut le
                    reprendre). C'est la meme sequence que l'outil
                    ouvertureBooster (wm_ouverture_booster.py), qui se
                    contente d'attacher cette fenetre et d'appeler la
                    fonction open_and_reveal() ci-dessous.

  --api [--count N] Saute entierement l'interface : appelle
                    POST /api/packs/open, qui renvoie deja les 5 cartes
                    revelees et packs_remaining en une seule reponse JSON
                    (verifie par capture : le defilement chevron/Continuer
                    ne declenche aucune requete, c'est de la navigation
                    purement client-side sur des donnees deja recues). Pas
                    de navigateur du tout, juste un client HTTP avec les
                    cookies de storage_state.json. Ecrit le resultat dans
                    data/. Plus simple et plus robuste que --click, mais
                    aussi plus "bot-like" (requete nue, sans navigation de
                    page ni delai naturel) : a garder pour un usage
                    reflechi, pas pour vider tous les paquets d'un coup
                    (cf CLAUDE.md : le risque est de perdre le compte, pas
                    juridique).

En mode --click, chaque lancement n'ouvre qu'un seul pack. Pas de boucle :
relance (ou rappelle l'outil ouvertureBooster) si tu veux en ouvrir un
autre, avec au moins DELAY entre deux.
"""

import hashlib
import json
import random
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from wm_session_io import ensure_fresh, persist


def _detach_flags():
    """Flags Windows necessaires pour que Chrome survive a la fin de CE
    script. Sans eux, un enfant lance par subprocess.Popen reste rattache
    au job Windows du processus courant et se fait tuer avec lui des que
    ce processus Python se termine (verifie empiriquement : le Chrome
    persistant disparaissait des la fin du script alors que
    connect_over_cdp avait reussi pendant son execution).
    CREATE_BREAKAWAY_FROM_JOB en sort ; les deux autres evitent d'heriter
    la console/le groupe de process du parent.

    Calcule a la demande (pas au niveau module) et uniquement sur Windows :
    ces constantes n'existent pas sous Linux/macOS, et ce module doit
    pouvoir etre importe partout -- le mode --api (utilise par le workflow
    GitHub Actions, qui tourne sur un runner Linux) n'a jamais besoin de
    lancer Chrome, donc ne doit jamais toucher a ces attributs."""
    if not hasattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB"):
        raise SystemExit(
            "Lancement de Chrome persistant demande (--click/--recon ou par "
            "defaut) sur une plateforme non-Windows : non supporte. Utilise "
            "--api sur cette machine."
        )
    return (
        subprocess.CREATE_BREAKAWAY_FROM_JOB
        | subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP
    )

BASE = "https://www.wiki-masters.com"
HOST = urlparse(BASE).netloc
STATE = Path("storage_state.json")
OUT = Path("captures")
BODIES = OUT / "bodies"
SCREENSHOTS = OUT / "screenshots"
DATA = Path("data")

# Port de debogage distant fixe et dedie a la fenetre "booster" de ce
# projet (distinct du 9222 utilise ponctuellement par wm_session_cdp.py).
# Fixe expres : wm_ouverture_booster.py doit pouvoir retrouver la meme
# fenetre d'un lancement a l'autre.
CDP_PORT = 9224

# Delai entre deux ouvertures en mode --api. Ne descends pas sous 2s, meme
# ici (cf CLAUDE.md) : c'est justement le mode qui ressemble le plus a un
# bot, donc celui ou ce garde-fou compte le plus. 5-10s decide le
# 30/08/2026 pour l'usage "ouvre tout ce qui est disponible d'un coup"
# (workflow GitHub Actions toutes les 90 min), plus prudent que l'ancien
# 2-4s vu que ce mode enchaine potentiellement jusqu'a 10 ouvertures
# d'affilee dans le meme run.
API_DELAY = (5.0, 10.0)

# Chemin standard de Chrome sur Windows (meme hypothese que le docstring de
# wm_session_cdp.py). Si ton install est ailleurs, ajuste cette constante.
CHROME_EXE = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")

# Delai avant de considerer que l'animation de reveal est terminee et de
# prendre la capture "apres". Ajuste si l'animation est plus longue.
REVEAL_WAIT = 4.0

# Motifs pour reperer le bouton/l'image d'ouverture sans connaitre le DOM
# exact. Ordre = priorite : image du pack d'abord (vue dans les captures
# _next/image?url=%2Fcard_pack.png), puis un bouton au texte evocateur.
CANDIDATE_SELECTORS = [
    ('img[src*="card_pack" i]', "image du pack (card_pack.png)"),
    ('[alt*="pack" i]', "element avec alt contenant 'pack'"),
]
TEXT_PATTERN = re.compile(r"ouvrir|tirer|pack|booster|pioche", re.I)
CONTINUE_PATTERN = re.compile(r"continuer", re.I)

# Le defilement des cartes se fait par un bouton rond "suivant" (chevron)
# a cote des points de pagination, pas au clavier (ArrowRight ne fait
# rien, verifie empiriquement). Ce n'est pas une icone de librairie (pas de
# classe lucide-*, pas d'aria-label) : un SVG maison avec juste une
# <polyline>. Inspecte sur le DOM reel (Chrome persistant, sans consommer
# de booster) : les boutons "precedent" et "suivant" partagent exactement
# les memes classes Tailwind, dans cet ordre (precedent avant suivant) ;
# seul l'attribut disabled (precedent desactive sur la 1re carte) et le
# sens de la polyline different. Donc : parmi les boutons ronds de ce type
# non desactives, le "suivant" est toujours le dernier dans le DOM. Jamais
# de coordonnees pixel fixes : la position ecran depend de la taille de
# fenetre, un clic a coordonnees fixes casserait des que le viewport change.
CHEVRON_BUTTON_SELECTOR = 'button.rounded-full.w-12.h-12:not([disabled])'

# Nombre de cartes reveelees par paquet (vu sur la page : "Decouvrez 5
# nouvelles cartes") : il faut donc 4 clics "suivant" pour passer de la
# carte 1 a la carte 5, + marge de securite pour ne pas boucler
# indefiniment si le bouton "Continuer" tarde a apparaitre.
CARDS_PER_PACK = 5
MAX_NEXT_CLICKS = CARDS_PER_PACK - 1 + 3

# Temps laisse a chaque flip de carte pour s'animer avant le prochain clic
# sur "suivant" / la prochaine verification du bouton Continuer.
CARD_FLIP_WAIT = 1.8


def find_candidates(page):
    found = []
    for selector, label in CANDIDATE_SELECTORS:
        try:
            loc = page.locator(selector)
            count = loc.count()
        except Exception:
            count = 0
        for i in range(count):
            el = loc.nth(i)
            try:
                if el.is_visible():
                    found.append((el, f"{label} [{i}]"))
            except Exception:
                continue

    try:
        by_text = page.get_by_role("button", name=TEXT_PATTERN)
        for i in range(by_text.count()):
            el = by_text.nth(i)
            try:
                if el.is_visible():
                    found.append((el, f"bouton au texte evocateur [{i}]"))
            except Exception:
                continue
    except Exception:
        pass

    return found


def click_next_card(page) -> bool:
    """Clique le bouton rond 'carte suivante' : le dernier bouton chevron
    actif du DOM (cf commentaire de CHEVRON_BUTTON_SELECTOR). Aucun repli
    en coordonnees pixel : si rien ne matche, on l'affiche clairement
    plutot que de cliquer a l'aveugle a un endroit qui casserait avec une
    autre taille de fenetre."""
    try:
        loc = page.locator(CHEVRON_BUTTON_SELECTOR)
        visible = [loc.nth(i) for i in range(loc.count()) if loc.nth(i).is_visible()]
        if visible:
            visible[-1].click(timeout=3000)
            return True
    except Exception:
        pass

    print("  CHEVRON_BUTTON_SELECTOR ne matche rien. Boutons visibles sur la page :")
    try:
        buttons = page.locator("button")
        for i in range(min(buttons.count(), 15)):
            b = buttons.nth(i)
            if b.is_visible():
                html = b.evaluate("el => el.outerHTML.slice(0, 200)")
                print(f"    [{i}] {html}")
    except Exception:
        pass
    return False


def describe(el):
    try:
        box = el.bounding_box()
        text = (el.inner_text() or "").strip()[:60]
        alt = el.get_attribute("alt") or ""
        return f"pos={box} texte={text!r} alt={alt!r}"
    except Exception:
        return "(details indisponibles)"


def capture_network(ctx, page):
    """Meme format que wm_discover.py : permet a wm_map.py de reprendre
    telle quelle une ouverture de pack capturee ici."""
    BODIES.mkdir(parents=True, exist_ok=True)
    index = OUT / "index.jsonl"
    log = index.open("a", encoding="utf-8")
    seen = 0

    def on_response(response):
        nonlocal seen
        url = response.url
        if urlparse(url).netloc != HOST:
            return
        ctype = (response.headers.get("content-type") or "").lower()
        method = response.request.method
        interesting = method != "GET" or any(
            t in ctype for t in ("application/json", "text/x-component", "text/plain")
        )
        if not interesting:
            return

        req = response.request
        try:
            body = response.body()
        except Exception:
            body = b""

        key = hashlib.sha1(f"{method}{url}{seen}".encode()).hexdigest()[:12]
        (BODIES / f"{key}.txt").write_bytes(body)

        entry = {
            "n": seen,
            "method": method,
            "url": url,
            "status": response.status,
            "content_type": response.headers.get("content-type", ""),
            "bytes": len(body),
            "body_file": f"bodies/{key}.txt",
            "req_headers": {
                k: v for k, v in req.headers.items()
                if k.lower() in ("next-action", "next-router-state-tree",
                                 "content-type", "accept", "rsc")
            },
            "post_data": (req.post_data or "")[:2000],
        }
        log.write(json.dumps(entry, ensure_ascii=False) + "\n")
        log.flush()
        seen += 1
        print(f"  [{entry['status']}] {method:6} {entry['bytes']:>7}o  {url[:110]}")

    ctx.on("response", on_response)
    return log, lambda: ctx.remove_listener("response", on_response)


def _find_cf_frame(page):
    for f in page.frames:
        if "challenges.cloudflare.com" in f.url:
            return f
    return None


def handle_cloudflare_challenge(page, wait_after_ms: int = 2500) -> bool:
    """Detecte un challenge Cloudflare Turnstile (le meme mecanisme que sur
    /login, verifie le 30/08/2026 dans wm_session_auto.py ; sur /pulls il
    se presente juste comme une case a cocher, pas la pleine page dediee du
    login) et tente de le resoudre. Souvent resolu tout seul en quelques
    secondes avec les flags anti-detection deja utilises pour lancer
    Chrome (observe empiriquement : "Succes !" sans clic) ; on tente un
    clic explicite sur la case a cocher en repli si elle est visible.

    Log prefixe "[CLOUDFLARE]" pour reperer facilement ces evenements dans
    les logs (utile pour un suivi en tail -f / Get-Content -Wait). Si le
    challenge est toujours present apres la tentative de resolution, une
    capture d'ecran horodatee est sauvegardee dans captures/screenshots/
    pour revue a posteriori, meme si personne ne regardait la fenetre au
    moment ou ca s'est produit.

    Retourne True si un challenge a ete detecte (resolu ou non) -- appelant
    peut alors reessayer l'action qui a echoue."""
    cf_frame = _find_cf_frame(page)
    if cf_frame is None:
        return False

    print("  [CLOUDFLARE] challenge detecte (verification humaine) — tentative de resolution...")
    page.wait_for_timeout(wait_after_ms)
    try:
        checkbox = cf_frame.locator("input[type='checkbox']")
        if checkbox.count() > 0 and checkbox.first.is_visible():
            checkbox.first.click(timeout=3000)
            print("  [CLOUDFLARE] clic sur la case a cocher.")
            page.wait_for_timeout(wait_after_ms)
    except Exception as e:
        print(f"  [CLOUDFLARE] pas de case a cliquer ou deja resolu ({e.__class__.__name__}).")

    if _find_cf_frame(page) is not None:
        SCREENSHOTS.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shot = SCREENSHOTS / f"cloudflare-unresolved-{stamp}.png"
        try:
            page.screenshot(path=str(shot))
        except Exception:
            shot = None
        print(f"  [CLOUDFLARE] TOUJOURS PRESENT apres tentative de resolution."
              + (f" Capture : {shot.resolve()}" if shot else ""))
    else:
        print("  [CLOUDFLARE] resolu.")

    return True


# Ecran propre a WikiMasters (pas Cloudflare) vu le 30/08/2026 sur /pulls :
# "Verification rapide -- Pour continuer a ouvrir des paquets, confirme
# que tu utilises l'application manuellement (pas de script ni bot)",
# avec une case "Je ne suis pas un robot". Delibegrement PAS automatise :
# cocher cette case avec un script attesterait faussement l'inverse de ce
# qu'elle verifie. Cf discussion du 30/08/2026 -- probablement declenche
# par l'usage soutenu du mode --api (le plus "bot-like") via le cron
# GitHub Actions pendant plusieurs heures d'affilee.
MANUAL_VERIFICATION_TEXT = "Je ne suis pas un robot"


def detect_manual_verification_gate(page) -> bool:
    """Detecte cet ecran. Ne clique jamais dessus. Retourne True s'il est
    present, pour que l'appelant s'arrete proprement (cf run_ouverture qui
    leve SystemExit dans ce cas, capte par la boucle de wm_auto_booster.py
    exactement comme une session expiree) plutot que de rester bloque en
    boucle silencieuse ou de mentir a ce controle."""
    try:
        gate = page.get_by_text(MANUAL_VERIFICATION_TEXT, exact=False)
        return gate.count() > 0 and gate.first.is_visible()
    except Exception:
        return False


def _screenshot_verification_gate(page, tag: str) -> Path:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    shot = SCREENSHOTS / f"verification-gate-{tag}-{time.strftime('%Y%m%d-%H%M%S')}.png"
    try:
        page.screenshot(path=str(shot))
    except Exception:
        pass
    return shot


def recon(page, stamp):
    """Repere les candidats a l'ouverture, prend une capture d'ecran, les
    affiche. Ne clique jamais rien. Retourne la liste des candidats."""
    if detect_manual_verification_gate(page):
        shot = _screenshot_verification_gate(page, "recon")
        raise SystemExit(
            "Ecran 'Verification rapide / Je ne suis pas un robot' detecte sur /pulls. "
            "Confirmation manuelle necessaire (non automatisee volontairement) — "
            f"va cocher la case toi-meme dans la fenetre Chrome. Capture : {shot.resolve()}"
        )
    handle_cloudflare_challenge(page)
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    shot = SCREENSHOTS / f"before-{stamp}.png"
    page.screenshot(path=str(shot))

    candidates = find_candidates(page)
    if not candidates:
        print("Aucun candidat trouve automatiquement.")
        print(f"Capture d'ecran : {shot.resolve()}")
        return []

    print(f"{len(candidates)} candidat(s) trouve(s) :")
    for el, label in candidates:
        print(f"  - {label} : {describe(el)}")
    print(f"Capture d'ecran : {shot.resolve()}")
    return candidates


def open_and_reveal(ctx, page, candidates, stamp) -> bool:
    """Sequence complete d'ouverture manuelle avec animation : clic sur le
    paquet, defilement des 5 cartes, clic sur Continuer. C'est le coeur de
    l'outil ouvertureBooster (wm_ouverture_booster.py). Retourne True si le
    flux est alle jusqu'au bout (Continuer trouve et clique)."""
    if not candidates:
        print("Rien a cliquer (aucun candidat).")
        return False

    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    target, label = candidates[0]
    print(f"\nOn clique sur '{label}'. Ceci consomme un vrai booster.")

    log, stop_logging = capture_network(ctx, page)
    try:
        target.click(timeout=5000)
    except Exception as e:
        print(f"Echec du clic ({e.__class__.__name__}) — rien n'a ete ouvert.")
        stop_logging()
        log.close()
        return False

    # Le clic d'ouverture peut declencher une verification "je suis
    # humain" (cf CLAUDE.md / retour d'usage) : si elle apparait, on la
    # laisse se resoudre puis on reclique sur le paquet (le premier clic a
    # pu etre absorbe par le challenge plutot que par le paquet).
    if handle_cloudflare_challenge(page):
        try:
            target.click(timeout=5000)
            print("  Reclique sur le paquet apres resolution du challenge.")
        except Exception:
            pass

    if detect_manual_verification_gate(page):
        shot = _screenshot_verification_gate(page, "after-click")
        stop_logging()
        log.close()
        raise SystemExit(
            "Ecran 'Verification rapide / Je ne suis pas un robot' detecte apres le clic. "
            "Confirmation manuelle necessaire (non automatisee volontairement) — "
            f"va cocher la case toi-meme dans la fenetre Chrome. Capture : {shot.resolve()}"
        )

    print(f"Clique. Attente {REVEAL_WAIT}s pour l'animation d'ouverture...")
    time.sleep(REVEAL_WAIT)

    shot = SCREENSHOTS / f"after-open-{stamp}.png"
    page.screenshot(path=str(shot))
    print(f"Capture apres ouverture : {shot.resolve()}")

    continued = False
    for i in range(1, MAX_NEXT_CLICKS + 1):
        if detect_manual_verification_gate(page):
            shot = _screenshot_verification_gate(page, f"loop-{i}")
            stop_logging()
            log.close()
            raise SystemExit(
                "Ecran 'Verification rapide / Je ne suis pas un robot' detecte pendant le "
                "defilement. Confirmation manuelle necessaire (non automatisee volontairement) — "
                f"va cocher la case toi-meme dans la fenetre Chrome. Capture : {shot.resolve()}"
            )
        handle_cloudflare_challenge(page)
        try:
            continue_btn = page.get_by_role("button", name=CONTINUE_PATTERN).first
            if continue_btn.is_visible(timeout=500):
                print(f"Bouton 'Continuer' trouve apres {i - 1} carte(s) — on clique.")
                continue_btn.click(timeout=3000)
                continued = True
                time.sleep(1.5)
                shot = SCREENSHOTS / f"after-continue-{stamp}.png"
                page.screenshot(path=str(shot))
                print(f"Capture apres Continuer : {shot.resolve()}")
                break
        except Exception:
            pass

        print(f"Carte {i} : clic sur le bouton suivant...")
        if not click_next_card(page):
            print("  bouton suivant introuvable — abandon de la boucle.")
            break
        time.sleep(CARD_FLIP_WAIT)
        shot = SCREENSHOTS / f"after-card-{i}-{stamp}.png"
        page.screenshot(path=str(shot))

    if not continued:
        print(f"\nBouton 'Continuer' non trouve apres {MAX_NEXT_CLICKS} clics.")
        print("La fenetre Chrome reste ouverte : termine a la main si besoin.")

    stop_logging()
    time.sleep(1.0)
    log.close()
    return continued


def open_via_api(req_ctx, count: int):
    """Ouvre 'count' paquets par appel direct a l'API, sans navigateur.
    S'arrete plus tot sur session expiree (401), erreur, ou plus de paquets
    disponibles -- ce dernier cas etant signale de DEUX facons par l'API :
    packs_remaining <= 0 dans une reponse 200, ou un 403 avec
    {"error":"Plus de paquets disponibles"} si le compteur etait deja a
    zero. Les deux sont des fins normales, pas des erreurs."""
    results = []
    for i in range(1, count + 1):
        resp = req_ctx.post("/api/packs/open")

        if resp.status == 401:
            raise SystemExit(
                "401 sur /api/packs/open — session expiree. Relance wm_session_auto.py."
            )
        # 403 ne veut PAS dire "session expiree" : c'est la reponse normale
        # quand le compteur de paquets est a zero
        # ({"error":"Plus de paquets disponibles","packs_remaining":0,
        #   "next_regen_at":"..."}). Confondre les deux (ce que faisait ce
        # code jusqu'au 30/08/2026) faisait echouer bruyamment un workflow
        # qui n'avait simplement rien a ouvrir, et envoyait chercher un
        # probleme de session inexistant -- d'ou des echecs qui semblaient
        # tourner d'un compte a l'autre, les paquets se regenerant a des
        # heures differentes selon les comptes.
        if resp.status == 403:
            try:
                payload = resp.json()
            except Exception:
                payload = {}
            if payload.get("packs_remaining") == 0 or "paquet" in str(payload.get("error", "")).lower():
                regen = payload.get("next_regen_at")
                suffix = f" — prochaine regeneration : {regen}" if regen else ""
                print(f"    plus de paquets disponibles{suffix}")
                break
            raise SystemExit(f"403 sur /api/packs/open : {resp.text()[:300]}")
        if resp.status == 429:
            print("    429 : on ralentit franchement (60s)")
            time.sleep(60)
            continue
        if resp.status >= 400:
            print(f"    {resp.status} sur /api/packs/open — on arrete")
            break

        payload = resp.json()
        cards = payload.get("cards", [])
        remaining = payload.get("packs_remaining")
        titles = ", ".join(c.get("wikipedia_title", "?") for c in cards)
        print(f"    paquet {i}/{count} : {len(cards)} carte(s) [{titles}] — packs_remaining={remaining}")
        results.append(payload)

        if remaining is not None and remaining <= 0:
            print("    plus de paquets disponibles — on arrete")
            break
        if i < count:
            time.sleep(random.uniform(*API_DELAY))

    return results


def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def launch_independent_chrome(port: int) -> None:
    """Lance Chrome comme processus a part entiere (pas via Playwright), un
    profil temporaire dedie et le port de debogage distant ouvert. Une
    fenetre lancee ainsi survit a la fin de ce script, exactement comme
    dans wm_session_cdp.py."""
    if not CHROME_EXE.exists():
        raise SystemExit(
            f"Chrome introuvable a {CHROME_EXE}. Ajuste CHROME_EXE dans le script, "
            "ou lance Chrome toi-meme (voir le docstring de wm_session_cdp.py) et "
            "adapte ce script pour reprendre CDP_URL comme lui."
        )
    profile_dir = Path(tempfile.mkdtemp(prefix="wm-booster-chrome-"))
    subprocess.Popen(
        [
            str(CHROME_EXE),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        creationflags=_detach_flags(),
        close_fds=True,
    )


def connect_with_retry(p, port: int, attempts: int = 20, delay: float = 0.5):
    last_err = None
    for _ in range(attempts):
        try:
            return p.chromium.connect_over_cdp(f"http://localhost:{port}")
        except Exception as e:
            last_err = e
            time.sleep(delay)
    raise SystemExit(f"Impossible de se connecter au Chrome lance (port {port}) : {last_err}")


def attach_chrome(p, port: int = CDP_PORT):
    """Se branche sur une fenetre persistante (port CDP donne, CDP_PORT
    par defaut pour rester compatible avec les appels existants). La
    lance si elle n'existe pas encore. Utiliser un port different par
    compte permet plusieurs fenetres persistantes independantes (voir
    wm_open_all_sessions.py)."""
    try:
        return p.chromium.connect_over_cdp(f"http://localhost:{port}")
    except Exception:
        print(f"Pas de Chrome sur le port {port} — on en lance un nouveau.")
        launch_independent_chrome(port)
        return connect_with_retry(p, port)


def get_page(browser, state_path: Path = None):
    """Reutilise l'onglet WikiMasters deja ouvert s'il existe (meme
    contexte, memes cookies, meme fenetre a l'ecran). Sinon, injecte les
    cookies du fichier de session donne (storage_state.json par defaut)
    dans le contexte PAR DEFAUT du Chrome connecte (browser.contexts[0])
    plutot que d'appeler browser.new_context() : un contexte cree ainsi
    via CDP est ferme par Playwright des que la connexion se termine
    (constate empiriquement — la fenetre disparaissait juste apres la fin
    du script), alors que le contexte par defaut d'un Chrome deja lance
    survit. Meme principe que wm_session_cdp.py, qui reutilise toujours
    browser.contexts[0]."""
    state_path = state_path or STATE
    for ctx in browser.contexts:
        for pg in ctx.pages:
            if urlparse(pg.url).netloc == HOST:
                return ctx, pg

    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("cookies"):
        ctx.add_cookies(state["cookies"])

    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.set_viewport_size({"width": 1280, "height": 900})
    return ctx, page


def run_ouverture(p) -> bool:
    """Sequence complete sur la fenetre persistante : attache, va sur
    /pulls si besoin, verifie que la session est toujours valide, repere
    le paquet, l'ouvre avec l'animation complete. Reutilisee par l'outil
    ouvertureBooster (wm_ouverture_booster.py) et par l'automatisation
    (wm_auto_booster.py) pour ne pas dupliquer cette orchestration."""
    browser = attach_chrome(p)
    ctx, page = get_page(browser)

    if "/pulls" not in page.url:
        page.goto(f"{BASE}/pulls")
        page.wait_for_load_state("networkidle", timeout=8000)

    if "/login" in page.url:
        raise SystemExit("Redirige vers /login — session expiree, relance wm_session.py")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidates = recon(page, stamp)
    try:
        return open_and_reveal(ctx, page, candidates, stamp)
    finally:
        # Recopie les cookies de la fenetre dans le fichier de session : la
        # navigation a pu faire tourner le jeton cote serveur, et une copie
        # perimee fait tomber le prochain usage en 401 (cf wm_session_io).
        #
        # ATTENTION, ca ne protege que jusqu'a la fin de ce script : une
        # fenetre Chrome laissee OUVERTE continue de rafraichir la session
        # de son cote, et le fichier (comme le secret GitHub) redevient
        # perime sans prevenir. Si tu gardes une fenetre ouverte, repousse
        # le secret apres l'avoir fermee.
        try:
            ctx.storage_state(path=str(STATE))
        except Exception as e:
            print(f"    (sauvegarde de la session impossible : {e.__class__.__name__})")


def main():
    do_click = "--click" in sys.argv
    do_recon = "--recon" in sys.argv
    do_api = "--api" in sys.argv

    # --state permet de traiter un compte autre que storage_state.json dans
    # le meme run : boosters.yml enchaine les cinq comptes sequentiellement
    # (fusion du 03/09/2026), chacun avec son propre fichier de session.
    state = STATE
    if "--state" in sys.argv:
        idx = sys.argv.index("--state")
        try:
            state = Path(sys.argv[idx + 1])
        except IndexError:
            raise SystemExit("--state attend un chemin, ex: --state state1.json")

    if not state.exists():
        raise SystemExit(f"{state} introuvable — lance d'abord wm_session_auto.py")

    if do_api:
        count = 1
        if "--count" in sys.argv:
            idx = sys.argv.index("--count")
            try:
                count = int(sys.argv[idx + 1])
            except (IndexError, ValueError):
                raise SystemExit("--count attend un entier, ex: --count 5")

        with sync_playwright() as p:
            req_ctx = ensure_fresh(p, state, BASE)
            # try/finally : open_via_api leve SystemExit sur 401/403, et le
            # serveur a pu faire tourner le refresh token AVANT cette
            # erreur. Sans sauvegarde, la copie stockee devient perimee et
            # la session sera revoquee au prochain usage (cf wm_session_io).
            try:
                results = open_via_api(req_ctx, count)
            finally:
                persist(req_ctx, state)
                req_ctx.dispose()

        DATA.mkdir(exist_ok=True)
        stamp = time.strftime("%Y-%m-%d_%H%M%S")
        out = DATA / f"boosters-{stamp}.json"
        out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n{len(results)}/{count} paquet(s) ouvert(s), ecrit dans {out.resolve()}")
        return

    stamp = time.strftime("%Y%m%d-%H%M%S")

    with sync_playwright() as p:
        browser = attach_chrome(p)
        ctx, page = get_page(browser)

        if "/pulls" not in page.url:
            page.goto(f"{BASE}/pulls")
            page.wait_for_load_state("networkidle", timeout=8000)

        if "/login" in page.url:
            raise SystemExit("Redirige vers /login — session expiree, relance wm_session.py")

        if not do_click and not do_recon:
            print(f"Fenetre prete sur {page.url}")
            print("Aucune action effectuee. Options : --recon (repere sans cliquer),")
            print("--click (ouvre un booster), ou l'outil wm_ouverture_booster.py.")
            return

        candidates = recon(page, stamp)

        if not do_click:
            print("\nMode reconnaissance seulement (pas de --click) — rien clique.")
            return

        open_and_reveal(ctx, page, candidates, stamp)

    print("\nTermine. Le navigateur reste ouvert (ferme-le toi-meme).")
    print("Verifie captures/index.jsonl (python wm_map.py) pour voir si une")
    print("route d'ouverture de pack a ete capturee.")


if __name__ == "__main__":
    main()
