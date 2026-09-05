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
    python wm_trade_accept_all.py storage_state_3.json
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
        # timeout a 90s comme la creation d'echange (wm_trade_gift_wb.py) :
        # les endpoints /api/trades sont lents et le defaut de 30s a fait
        # echouer l'acceptation le 30/08/2026 alors que les offres avaient
        # bien ete envoyees. Un timeout ici est le pire cas -- l'echange
        # aboutit peut-etre cote serveur, mais on abandonne la reponse (et
        # les cookies eventuellement tournes qu'elle portait).
        resp = req.patch(
            f"/api/trades/{t['id']}", data={"action": "accept"}, timeout=90000
        )

        # Limite quotidienne d'echanges : 50/jour sur un compte gratuit,
        # envois ET acceptations confondus. Une fois atteinte, toutes les
        # offres suivantes prendront le meme 429 -- insister, c'est 147
        # appels perdus a deux secondes d'intervalle (constate le
        # 05/09/2026). Meme traitement que la limite de paquets dans
        # wm_open_booster.py : on s'arrete net et on dit ce qui reste.
        if resp.status == 429:
            try:
                payload = resp.json()
            except Exception:
                payload = {}
            if payload.get("code") == "trade_daily_limit":
                reste = len(pending) - i + 1
                print(f"  [{i}] limite quotidienne d'echanges atteinte "
                      f"({payload.get('error', '')[:80]})")
                print(f"  {reste} offre(s) laissee(s) en attente — reprise au prochain passage.")
                return

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
