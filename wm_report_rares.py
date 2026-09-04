"""
Rapport en lecture seule : agrege les cartes rares (UR et L par defaut) de
plusieurs comptes, et affiche le solde de wikibidous d'un compte designe.

N'ecrit rien sur le site -- que des GET. C'est le pendant "informationnel"
des workflows d'action (ouverture, defausse, trade).

    python wm_report_rares.py compte1=state1.json collecteur=state3.json
    python wm_report_rares.py --rarities UR,L,SR --balance-of collecteur ...

Chaque argument positionnel est une paire `label=chemin_session`. Le label
sert uniquement a l'affichage (le site n'expose pas le pseudo du compte
courant sur les endpoints utilises ici).

La sortie est du Markdown, pour etre lisible telle quelle dans le resume
d'un run GitHub Actions ($GITHUB_STEP_SUMMARY) comme dans un terminal.

Un compte dont la session est expiree est signale et saute, sans faire
echouer le rapport des autres -- meme principe que le continue-on-error des
workflows de defausse et de trade (les jetons Supabase expirent en 1h).
"""

import json
import random
import sys
import time
from collections import defaultdict
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

DELAY = (2.0, 3.0)
DEFAULT_RARITIES = ["UR", "L"]

# De la plus rare a la moins rare, pour trier l'affichage.
RARITY_ORDER = ["L", "UR", "SR", "R", "PC", "C"]


def fetch_rarity(req_ctx, rarity: str):
    """Toutes les cartes d'une rarete pour ce compte, en paginant jusqu'a
    une page vide. Leve SystemExit sur session expiree."""
    rows = []
    page = 0
    while True:
        resp = req_ctx.get(f"/api/my-collection?sort=rarity&rarity={rarity}&page={page}&stats=0")
        if resp.status == 401:
            raise SystemExit("401 sur my-collection — session expiree.")
        if resp.status == 403:
            raise SystemExit(f"403 sur my-collection : {resp.text()[:300]}")
        if resp.status == 429:
            print(f"    429 sur {rarity} — pause 60s", file=sys.stderr)
            time.sleep(60)
            continue
        if resp.status >= 400:
            print(f"    {resp.status} sur my-collection ({rarity}) — on arrete cette rarete", file=sys.stderr)
            break

        batch = resp.json().get("collection", [])
        if not batch:
            break
        rows.extend(batch)
        page += 1
        time.sleep(random.uniform(*DELAY))

    return rows


def get_balance(req_ctx):
    resp = req_ctx.get("/api/wikibidous")
    if resp.status >= 400:
        return None
    return resp.json().get("balance")


def emit_fragment(path, label, rows, rarities, balance, error):
    """Ecrit le resultat d'UN compte, pour agregation ulterieure.

    Le workflow traite les comptes un par un et repousse le secret de
    chacun juste apres son passage : charger les cinq secrets, tourner
    plusieurs minutes puis tous les reecrire ecrasait ceux qu'un autre
    workflow avait rafraichis entre-temps (meme bug que discard.yml et
    trade.yml, corrige le 30/08/2026). Ces fragments permettent de garder
    le tableau agrege malgre le decoupage."""
    payload = {
        "label": label,
        "rarities": rarities,
        "balance": balance,
        "error": error,
        "cards": [
            {
                "title": r.get("card", {}).get("wikipedia_title", "?"),
                "rarity": r.get("card", {}).get("rarity"),
                "count": r.get("count", 1),
            }
            for r in rows
        ],
    }
    Path(path).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def merge_fragments(paths):
    """Reconstruit le rapport Markdown a partir des fragments par compte."""
    aggregate, skipped, balance, balance_of, rarities = {}, [], None, None, []
    for p in paths:
        try:
            frag = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception:
            continue
        rarities = frag.get("rarities") or rarities
        label = frag.get("label", "?")
        if frag.get("error"):
            skipped.append((label, frag["error"]))
        if frag.get("balance") is not None:
            balance, balance_of = frag["balance"], label
        for c in frag.get("cards", []):
            entry = aggregate.setdefault(
                c["title"], {"rarity": c.get("rarity"), "holders": defaultdict(int)}
            )
            entry["holders"][label] += c.get("count", 1)
    render(aggregate, skipped, balance, balance_of, rarities, len(paths))


def main():
    accounts = []
    rarities = DEFAULT_RARITIES
    balance_of = None
    json_out = None

    args = sys.argv[1:]
    if args and args[0] == "--merge":
        merge_fragments(args[1:])
        return

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--rarities":
            rarities = args[i + 1].split(",")
            i += 2
        elif a == "--balance-of":
            balance_of = args[i + 1]
            i += 2
        elif a == "--json-out":
            json_out = args[i + 1]
            i += 2
        else:
            if "=" not in a:
                raise SystemExit(f"Argument invalide {a!r} — attendu label=chemin_session.")
            label, path = a.split("=", 1)
            accounts.append((label, Path(path)))
            i += 1

    if not accounts:
        raise SystemExit(
            "Usage: python wm_report_rares.py <label>=<session.json> [...] "
            "[--rarities UR,L] [--balance-of <label>]"
        )

    # title -> {"rarity": str, "holders": {label: count}}
    aggregate = {}
    skipped = []
    balance = None
    raw_rows = []
    err = None

    with sync_playwright() as p:
        for label, path in accounts:
            if not path.exists():
                skipped.append((label, f"{path} introuvable"))
                continue

            req_ctx = ensure_fresh(p, path, BASE)
            try:
                for rarity in rarities:
                    for row in fetch_rarity(req_ctx, rarity):
                        raw_rows.append(row)
                        card = row.get("card", {})
                        title = card.get("wikipedia_title", "?")
                        entry = aggregate.setdefault(
                            title,
                            {"rarity": card.get("rarity", rarity), "holders": defaultdict(int)},
                        )
                        entry["holders"][label] += row.get("count", 1)

                if balance_of is not None and label == balance_of:
                    balance = get_balance(req_ctx)
            except SystemExit as e:
                err = str(e)
                skipped.append((label, str(e)))
            finally:
                # Meme un simple GET peut faire tourner le refresh token
                # cote serveur : sans sauvegarde, ce rapport en lecture
                # seule revoquerait les sessions qu'il consulte
                # (cf wm_session_io).
                persist(req_ctx, path)
                req_ctx.dispose()

    if json_out:
        emit_fragment(json_out, accounts[0][0], raw_rows, rarities, balance, err)
        return

    render(aggregate, skipped, balance, balance_of, rarities, len(accounts))


def render(aggregate, skipped, balance, balance_of, rarities, n_accounts):
    """Rapport Markdown, commun au mode direct et au mode --merge."""
    print(f"## Cartes {'/'.join(rarities)} — {n_accounts} comptes\n")

    if aggregate:
        def sort_key(item):
            title, data = item
            rank = RARITY_ORDER.index(data["rarity"]) if data["rarity"] in RARITY_ORDER else len(RARITY_ORDER)
            return (rank, title.lower())

        total_copies = sum(sum(d["holders"].values()) for d in aggregate.values())
        print(f"**{len(aggregate)} carte(s) distincte(s)**, {total_copies} exemplaire(s) au total.\n")
        print("| Carte | Rarete | Detenue par |")
        print("|---|---|---|")
        for title, data in sorted(aggregate.items(), key=sort_key):
            holders = ", ".join(
                f"{lbl} x{n}" if n > 1 else lbl
                for lbl, n in sorted(data["holders"].items())
            )
            print(f"| {title} | {data['rarity']} | {holders} |")
    else:
        print("_Aucune carte de ces raretes sur les comptes lus._")

    if balance_of is not None:
        print()
        if balance is None:
            print(f"**Solde de {balance_of}** : indisponible (session expiree ou compte non lu).")
        else:
            print(f"**Solde de {balance_of}** : {balance} wb")

    if skipped:
        print("\n### Comptes non lus\n")
        for label, reason in skipped:
            print(f"- **{label}** : {reason}")


if __name__ == "__main__":
    main()
