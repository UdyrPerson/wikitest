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
    python wm_trade_gift_wb.py storage_state_test2.json collecteur
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from wm_session_io import persist

BASE = "https://www.wiki-masters.com"


def main():
    if len(sys.argv) < 3:
        raise SystemExit("Usage: python wm_trade_gift_wb.py <storage_state.json> <pseudo_destinataire>")

    state_path = Path(sys.argv[1])
    target_username = sys.argv[2]

    if not state_path.exists():
        raise SystemExit(f"{state_path} introuvable — lance d'abord wm_session.py ou wm_session_auto.py.")

    with sync_playwright() as p:
        req = p.request.new_context(storage_state=str(state_path), base_url=BASE)
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
    if friends_resp.status in (401, 403):
        raise SystemExit(f"{friends_resp.status} sur /api/friends — session expiree.")
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

    create_resp = req.post(
        "/api/trades",
        data={
            "recipient_id": recipient_id,
            "items": [],
            "initiator_wikibidous": balance,
            "recipient_wikibidous": 0,
        },
    )
    if create_resp.status >= 400:
        print(f"Echec ({create_resp.status}) : {create_resp.text()[:500]}")
    else:
        trade = create_resp.json().get("trade", {})
        print(f"Offre envoyee a {target_username} : {balance} wb — trade id {trade.get('id')}, "
              f"status={trade.get('status')}")



if __name__ == "__main__":
    main()
