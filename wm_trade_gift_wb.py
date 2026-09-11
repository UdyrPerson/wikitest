"""
Propose un echange offrant TOUT le solde de wikibidous courant a un ami,
sans carte en retour. Pure API, pas de navigateur.

Endpoints decouverts le 30/08/2026 :
    GET  /api/friends              -> resout un pseudo en recipient_id
    GET  /api/wikibidous            -> solde courant
    POST /api/trades                -> cree l'offre
         {"recipient_id": ..., "items": [], "initiator_wikibidous": N, "recipient_wikibidous": 0}

Le destinataire doit deja etre "ami" (visible dans /api/friends) --
verifie manuellement au prealable dans l'interface si ce n'est pas encore
le cas (le formulaire "Proposer un echange" suggere automatiquement les
amis existants).

    python wm_trade_gift_wb.py <storage_state.json> <pseudo_destinataire>

Exemple :
    python wm_trade_gift_wb.py storage_state.json collecteur
    python wm_trade_gift_wb.py storage_state_2.json collecteur
"""

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

# La console Windows est en cp1252 : afficher un titre de carte contenant un
# caractere hors de cette table leve un UnicodeEncodeError qui tue le script
# en plein milieu. Invisible sur un runner GitHub (UTF-8), fatal en local.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


from wm_session_io import ensure_fresh, persist

BASE = "https://www.wiki-masters.com"

# Le jeu refuse un echange de plus de 10 000 wikibidous entre deux amis.
# Le compte 8 en portait 19 000 le 12/09/2026 : l'offre unique partait donc
# au-dessus du plafond, et son solde ne bougeait plus.
MAX_PAR_ECHANGE = 10_000

# On decoupe, mais pas indefiniment : chaque offre consomme le quota du
# COLLECTEUR, qui plafonne a 50 echanges par jour et qui est de tous les
# echanges (envois + acceptations, cf CLAUDE.md). A huit emetteurs, trois
# offres chacun lui font 24 acceptations -- large. Le surplus eventuel
# attend le passage du lendemain.
# ponytail: plafond fixe a 3 offres, a relever si un compte se met a gagner
# plus de 30 000 wb par jour.
MAX_OFFRES = 3

DELAY = 2.0  # entre deux offres, cf CLAUDE.md


def main():
    if len(sys.argv) < 3:
        raise SystemExit("Usage: python wm_trade_gift_wb.py <storage_state.json> <pseudo_destinataire>")

    state_path = Path(sys.argv[1])
    target_username = sys.argv[2]

    if not state_path.exists():
        raise SystemExit(f"{state_path} introuvable — lance d'abord wm_session.py ou wm_session_auto.py.")

    with sync_playwright() as p:
        req = ensure_fresh(p, state_path, BASE)
        try:
            gift(req, target_username)
        finally:
            # Le serveur a pu faire tourner le refresh token pendant ces
            # appels : sans sauvegarde, la session est revoquee au prochain
            # usage (cf wm_session_io).
            persist(req, state_path)
            req.dispose()


def gift(req, target_username):
    friends_resp = req.get("/api/friends")
    if friends_resp.status == 401:
        raise SystemExit("401 sur /api/friends — session expiree.")
    if friends_resp.status == 403:
        raise SystemExit(f"403 sur /api/friends : {friends_resp.text()[:300]}")
    friendships = friends_resp.json().get("friendships", [])

    recipient_id = None
    for f in friendships:
        if f.get("status") != "accepted":
            continue
        for side in ("requester", "addressee"):
            user = f.get(side, {})
            if user.get("username") == target_username:
                recipient_id = user.get("id")
                break
        if recipient_id:
            break

    if recipient_id is None:
        raise SystemExit(
            f"'{target_username}' non trouve parmi les amis acceptes de ce compte. "
            "Il doit deja etre ami (verifie dans l'interface d'abord)."
        )

    balance_resp = req.get("/api/wikibidous")
    balance = balance_resp.json().get("balance", 0)
    print(f"Solde actuel : {balance} wb")

    if balance <= 0:
        print("Rien a envoyer (solde nul).")
        return

    reste = balance
    for i in range(1, MAX_OFFRES + 1):
        if reste <= 0:
            break
        montant = min(reste, MAX_PAR_ECHANGE)

        # timeout releve a 90s : la valeur par defaut de Playwright (30s) a
        # provoque un TimeoutError sur cet appel le 30/08/2026 alors que la
        # creation de l'echange avait probablement abouti cote serveur. Un
        # timeout ici est le pire cas -- on ignore une reponse qui existe, donc
        # on ne sauvegarde pas les cookies tournes qu'elle transportait.
        create_resp = req.post(
            "/api/trades",
            data={
                "recipient_id": recipient_id,
                "items": [],
                "initiator_wikibidous": montant,
                "recipient_wikibidous": 0,
            },
            timeout=90000,
        )
        if create_resp.status >= 400:
            print(f"Echec ({create_resp.status}) : {create_resp.text()[:500]}")
            return
        trade = create_resp.json().get("trade", {})
        reste -= montant
        print(f"Offre {i} envoyee a {target_username} : {montant} wb "
              f"(reste {reste}) — trade id {trade.get('id')}, "
              f"status={trade.get('status')}")
        if reste > 0:
            time.sleep(DELAY)

    if reste > 0:
        print(f"{reste} wb non envoyes : plafond de {MAX_OFFRES} offres par "
              f"passage atteint. Le reste partira au prochain.")



if __name__ == "__main__":
    main()
