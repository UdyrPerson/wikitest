"""
Accepte toutes les offres d'echange EN ATTENTE recues sur ce compte. Pure
API, pas de navigateur.

Endpoints decouverts le 30/08/2026 :
    GET   /api/trades                        -> liste des echanges
    PATCH /api/trades/{trade_id}             -> {"action": "accept"}

Ne filtre pas explicitement "recu vs envoye" (pas d'endpoint dedie trouve
pour l'instant) : accepte toute offre status=="pending" retournee par
/api/trades pour ce compte. Adapte au cas d'usage prevu (un compte qui ne
fait que RECEVOIR des dons de wikibidous, jamais en envoyer lui-meme) --
si ce compte envoie aussi des offres, ce script les "accepterait" aussi
sans distinction, donc a ne pas utiliser tel quel dans ce cas.

    python wm_trade_accept_all.py <storage_state.json>

Exemple :
    python wm_trade_accept_all.py storage_state_test3.json
"""

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from wm_session_io import ensure_fresh, persist

BASE = "https://www.wiki-masters.com"
DELAY = 2.0  # entre deux acceptations, cf CLAUDE.md


def accept_pending(req):
    trades_resp = req.get("/api/trades")
    if trades_resp.status == 401:
        raise SystemExit("401 sur /api/trades — session expiree.")
    if trades_resp.status == 403:
        raise SystemExit(f"403 sur /api/trades : {trades_resp.text()[:300]}")
    trades = trades_resp.json().get("trades", [])
    pending = [t for t in trades if t.get("status") == "pending"]

    print(f"{len(pending)} offre(s) en attente sur {len(trades)} au total.")

    for i, t in enumerate(pending, 1):
        initiator = t.get("initiator", {}).get("username", "?")
        wb = t.get("initiator_wikibidous", 0)
        resp = req.patch(f"/api/trades/{t['id']}", data={"action": "accept"})
        if resp.status >= 400:
            print(f"  [{i}] echec sur offre de {initiator} ({wb} wb) : {resp.status} {resp.text()[:200]}")
        else:
            print(f"  [{i}] acceptee : {initiator} -> {wb} wb (status={resp.json().get('status')})")
        if i < len(pending):
            time.sleep(DELAY)


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python wm_trade_accept_all.py <storage_state.json>")

    state_path = Path(sys.argv[1])
    if not state_path.exists():
        raise SystemExit(f"{state_path} introuvable — lance d'abord wm_session.py ou wm_session_auto.py.")

    with sync_playwright() as p:
        req = ensure_fresh(p, state_path, BASE)
        try:
            accept_pending(req)
        finally:
            # Le serveur a pu faire tourner le refresh token pendant ces
            # appels : sans sauvegarde, la session est revoquee au prochain
            # usage (cf wm_session_io).
            persist(req, state_path)
            req.dispose()


if __name__ == "__main__":
    main()
