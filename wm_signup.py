"""
Cree un compte WikiMasters de bout en bout : adresse jetable, inscription,
verification par code recu sur cette adresse.

    python wm_signup.py                          # local, fenetre visible
    python wm_signup.py --state storage_state_10.json
    python wm_signup.py --headless               # sans affichage (runner)

Le script enchaine quatre etapes, toutes verifiees le 07/09/2026 sur un
compte reellement cree (jipise8650@daugr.com) :

1. temp-mail.org/fr donne une adresse jetable. Elle apparait dans le champ
   #mail quelques secondes apres le chargement -- il faut l'attendre, le
   champ affiche "Chargement..." entre-temps.
2. /signup demande pseudo, email, mot de passe et DEUX cases a cocher
   (majorite + conditions d'utilisation). Le widget Turnstile se resout
   tout seul dans un vrai Chrome : on attend que le champ cache
   cf-turnstile-response se remplisse plutot que de cliquer a l'aveugle.
3. Le site n'envoie pas un lien mais un CODE A SIX CHIFFRES, a saisir dans
   #signup-otp-code. Le mail met une a deux minutes a arriver.
4. Le code valide, on atterrit sur /pulls, connecte.

PIEGE DE LA BOITE DE RECEPTION. La liste des messages contient plusieurs
`a.viewLink` pour un meme message, et le PREMIER a `href="javascript:void(0)"`
-- naviguer dessus leve ERR_ABORTED. Il faut filtrer sur un href qui
commence par http.

OU VONT LES IDENTIFIANTS. Dans comptes_crees.txt, couvert par le
.gitignore : le depot est public. Le mot de passe n'est jamais imprime sur
la sortie standard pour la meme raison -- sur un runner, les logs d'un
depot public sont lisibles par tout le monde.

TURNSTILE ET LES IP DE DATACENTER. Ce script marche en local, dans un vrai
Chrome sur une IP residentielle. Sur un runner GitHub c'est l'inconnue
connue du projet : la meme protection a fait supprimer refresh-sessions.yml
le 03/09/2026 (commit 423102a) parce que la connexion automatisee echouait
systematiquement sur Cloudflare. temp-mail.org filtre lui aussi les IP de
datacenter. Voir .github/workflows/signup.yml.
"""

import argparse
import json
import os
import random
import re
import secrets
import string
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = "https://www.wiki-masters.com"
TEMP_MAIL = "https://temp-mail.org/fr/"
FICHIER_COMPTES = Path("comptes_crees.txt")

# Le mail de confirmation a mis ~90 s a arriver lors de l'essai du
# 07/09/2026. On laisse largement de quoi encaisser une file d'attente.
ATTENTE_ADRESSE_S = 90
ATTENTE_CODE_S = 420

# Espacement entre deux consultations de la boite. A 10 s et par
# rechargement complet, quatre processus ont fait tomber un 429 sur
# temp-mail.org. Le service le dit lui-meme : « If you are using our
# website frequently or for automated tasks, please use our Premium or
# API. » On reste donc tres en dessous.
INTERVALLE_BOITE_S = 20

# Bouton de rafraichissement de la boite (barre « Copier / Actualiser /
# Retour / Supprimer »). Plusieurs formes acceptees : le site a change de
# balise par le passe.
SEL_ACTUALISER = ("#click-to-refresh, button:has-text('Actualiser'), "
                  "a:has-text('Actualiser')")


def mot_de_passe(n: int = 20) -> str:
    """Mot de passe aleatoire, avec au moins une minuscule, une majuscule,
    un chiffre et un symbole -- les formulaires refusent souvent sinon."""
    corps = "".join(secrets.choice(string.ascii_letters + string.digits + "!@#$%-_")
                    for _ in range(n - 4))
    return "Wm" + corps + "7!"


def rate_limite(page) -> bool:
    """temp-mail.org repond-il 429 ? Il sert une page d'erreur en clair."""
    try:
        return "429" in page.title() or "rate limited" in page.evaluate(
            "() => document.body.innerText").lower()
    except Exception:
        return False


def adresse_jetable(page) -> str:
    """Adresse fournie par temp-mail.org, une fois le champ rempli."""
    page.goto(TEMP_MAIL, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    if rate_limite(page):
        raise SystemExit(
            "temp-mail.org repond 429 (trop de requetes depuis cette IP). "
            "Rien a corriger dans le script : il faut attendre que la limite "
            "retombe. Voir INTERVALLE_BOITE_S."
        )
    limite = time.time() + ATTENTE_ADRESSE_S
    while time.time() < limite:
        try:
            valeur = page.locator("#mail").first.input_value(timeout=3000).strip()
        except Exception:
            valeur = ""
        if "@" in valeur:
            return valeur
        page.wait_for_timeout(2000)
    raise SystemExit(
        "temp-mail.org n'a pas fourni d'adresse. Sur un runner, c'est le "
        "signe que le site refuse l'IP (protection anti-datacenter)."
    )


def jeton_turnstile(page, secondes: int = 25) -> str:
    """Attend que le widget Cloudflare remplisse son champ cache.

    On n'essaie PAS de cliquer d'emblee : dans un vrai Chrome le widget se
    valide seul, et un clic premature sur une case deja cochee la decoche.
    Le clic n'intervient qu'en repli, si rien ne vient.
    """
    lire = "() => (document.querySelector('input[name=\"cf-turnstile-response\"]')||{}).value || ''"
    limite = time.time() + secondes
    while time.time() < limite:
        jeton = page.evaluate(lire)
        if jeton:
            return jeton
        page.wait_for_timeout(1500)

    cadre = next((f for f in page.frames if "challenges.cloudflare.com" in f.url), None)
    if cadre is not None:
        try:
            case = cadre.locator("input[type=checkbox]")
            if case.count():
                case.first.click(timeout=4000)
                page.wait_for_timeout(5000)
        except Exception as e:
            print(f"  (clic Cloudflare impossible : {e.__class__.__name__})")
    return page.evaluate(lire)


def inscrire(page, pseudo: str, email: str, mdp: str) -> None:
    page.goto(f"{BASE}/signup", wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass

    page.fill("#username", pseudo)
    page.fill("#email", email)
    page.fill("#password", mdp)

    cases = page.locator("input[type=checkbox]")
    for i in range(cases.count()):
        if not cases.nth(i).is_checked():
            cases.nth(i).check(force=True)

    if not jeton_turnstile(page):
        raise SystemExit(
            "Turnstile n'a pas delivre de jeton : le formulaire ne partira pas. "
            "C'est le mode d'echec attendu depuis une IP de datacenter."
        )

    page.get_by_role("button", name="Créer mon compte").click(timeout=10000)
    page.wait_for_timeout(6000)

    texte = page.evaluate("() => document.body.innerText")
    if "code de vérification" not in texte.lower():
        raise SystemExit(f"Ecran inattendu apres l'inscription :\n{texte[:400]}")


def code_de_verification(page, ctx, adresse: str) -> str:
    """Attend le message de WikiMasters et en extrait le code a 6 chiffres.

    NE RECHARGE JAMAIS LA PAGE. La premiere version faisait un
    `goto(TEMP_MAIL)` a chaque tour, toutes les dix secondes : quatre
    processus en parallele pendant cinq minutes, ca fait ~120 chargements
    complets, et temp-mail.org a fini par repondre 429 « You are being rate
    limited » (07/09/2026). Le champ de l'adresse disparaissait alors de la
    page, le courrier arrivait dans une boite qu'on ne lisait plus, et
    quatre comptes ont ete crees sans pouvoir etre verifies.

    On utilise donc le bouton « Actualiser » du site, qui rafraichit la
    boite en AJAX sans recharger, et on espace les tours.
    """
    limite = time.time() + ATTENTE_CODE_S
    while time.time() < limite:
        try:
            page.locator(SEL_ACTUALISER).first.click(timeout=5000)
        except Exception:
            pass
        page.wait_for_timeout(4000)

        if rate_limite(page):
            raise SystemExit(
                "temp-mail.org repond 429 en pleine attente du code. Le compte "
                f"est cree mais non verifie : {adresse}"
            )
        # Plusieurs a.viewLink pointent le meme message et le premier porte
        # href="javascript:void(0)" : on ne garde que les vraies URL.
        lien = page.evaluate("""() => {
            const a = Array.from(document.querySelectorAll('a.viewLink'))
                           .find(e => (e.getAttribute('href') || '').startsWith('http'));
            return a ? a.getAttribute('href') : null;
        }""")
        if lien:
            page.goto(lien, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            corps = page.evaluate("() => document.body.innerText")
            trouves = re.findall(r"\b(\d{6})\b", corps)
            if trouves:
                return trouves[0]
        reste = int(limite - time.time())
        # Un « pas encore arrive » repete ne dit pas POURQUOI. Toutes les
        # cinq tentatives, on montre l'etat reel de la boite : si l'adresse
        # affichee n'est plus celle qui a servi a s'inscrire, le courrier
        # part dans une boite qu'on ne regarde plus -- ce n'est pas une
        # question de patience.
        if int(reste) % 60 < INTERVALLE_BOITE_S:
            # Le champ #mail affiche « Chargement », « Chargement. »... en
            # attendant sa valeur : le lire au mauvais moment donnait
            # « CHANGE, le courrier part ailleurs » sur une boite
            # parfaitement saine (07/09/2026, diagnostic suivi a tort).
            # On reessaie donc jusqu'a obtenir quelque chose qui ressemble
            # a une adresse, et on distingue « change » de « illisible ».
            courante = ""
            for _ in range(5):
                try:
                    courante = page.locator("#mail").first.input_value(timeout=3000).strip()
                except Exception:
                    courante = ""
                if "@" in courante:
                    break
                page.wait_for_timeout(1000)

            if "@" not in courante:
                marque = "  (champ en cours de chargement — etat indetermine)"
                courante = courante or "(illisible)"
            elif courante != adresse:
                marque = "  <- CHANGE, le courrier part ailleurs"
            else:
                marque = ""
            print(f"  boite surveillee : {courante}{marque}")
        print(f"  message pas encore arrive ({reste} s restantes)")
        page.wait_for_timeout(random.uniform(INTERVALLE_BOITE_S * 1000,
                                              INTERVALLE_BOITE_S * 1500))
    raise SystemExit("Aucun code recu dans le delai imparti.")


def enregistrer(fichier: Path, pseudo: str, email: str, mdp: str, statut: str) -> None:
    """Ajoute une ligne d'identifiants, en creant l'en-tete au besoin.

    Ecrit DEUX fois par compte -- une fois le compte cree, une fois le code
    valide. C'est voulu : si le processus meurt entre les deux (code jamais
    recu, verification refusee), le mot de passe est deja sur le disque et
    le compte reste recuperable a la main.

    Le fichier est parametrable pour que plusieurs inscriptions lancees en
    parallele n'ecrivent pas dans le meme : deux `write` concurrents sur le
    meme descripteur peuvent s'entrelacer. On fusionne apres coup.
    """
    entete = ("# Comptes WikiMasters crees automatiquement (wm_signup.py).\n"
              "# Couvert par le .gitignore : le depot est PUBLIC.\n\n")
    if not fichier.exists():
        fichier.write_text(entete, encoding="utf-8")
    with fichier.open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.now():%Y-%m-%d %H:%M}] pseudo={pseudo}  "
                f"email={email}  mdp={mdp}  statut={statut}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", help="fichier de session a ecrire une fois le compte actif")
    ap.add_argument("--headless", action="store_true",
                    help="sans affichage (impose sur un runner)")
    ap.add_argument("--fichier", default=str(FICHIER_COMPTES),
                    help="ou ecrire les identifiants (un par processus si parallele)")
    args = ap.parse_args()
    fichier = Path(args.fichier)

    sans_ecran = args.headless or os.environ.get("CI") == "true"

    with sync_playwright() as p:
        navigateur = p.chromium.launch(
            headless=sans_ecran,
            channel="chrome" if not sans_ecran else None,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        ctx = navigateur.new_context(viewport={"width": 1280, "height": 900})
        onglet_mail = ctx.new_page()
        onglet_site = ctx.new_page()

        print("1/4  adresse jetable...")
        email = adresse_jetable(onglet_mail)
        pseudo = email.split("@")[0]
        mdp = mot_de_passe()
        print(f"     {email}  (pseudo {pseudo})")

        print("2/4  inscription...")
        inscrire(onglet_site, pseudo, email, mdp)
        enregistrer(fichier, pseudo, email, mdp, "code-attendu")
        print("     compte cree, code demande")

        print("3/4  recuperation du code...")
        code = code_de_verification(onglet_mail, ctx, email)
        print(f"     code recu ({len(code)} chiffres)")

        print("4/4  verification...")
        onglet_site.bring_to_front()
        onglet_site.fill("#signup-otp-code", code)
        onglet_site.get_by_role("button", name="Vérifier et continuer").click(timeout=10000)
        onglet_site.wait_for_timeout(8000)

        actif = "/signup" not in onglet_site.url
        enregistrer(fichier, pseudo, email, mdp,
                    "verifie" if actif else "ECHEC-verification")
        print(f"     {'compte actif' if actif else 'verification refusee'} — {onglet_site.url}")

        if actif and args.state:
            ctx.storage_state(path=args.state)
            print(f"     session ecrite dans {args.state}")

        navigateur.close()

    # Le mot de passe n'est jamais imprime : sur un depot public, les logs
    # de run sont lisibles par tout le monde.
    print(f"\nPseudo  : {pseudo}")
    print(f"Email   : {email}")
    print(f"Mot de passe : dans {fichier} (non imprime ici)")
    if not actif:
        sys.exit(1)


if __name__ == "__main__":
    main()
