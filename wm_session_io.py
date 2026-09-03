"""
Persistance de la session apres usage — a appeler par TOUT script qui ouvre
un contexte de requetes authentifie.

Pourquoi c'est indispensable (cause racine trouvee le 30/08/2026, apres une
serie de "session expiree" incomprehensibles) :

Le site tourne sur Next.js + @supabase/ssr. Le jeton d'acces Supabase dure
une heure, mais ce n'est PAS une limite dure : quand une requete arrive avec
un jeton expire, le serveur le renouvelle lui-meme a partir du refresh token
et renvoie de NOUVEAUX cookies, avec un refresh token tourne. C'est pour ca
qu'une session "expiree depuis 170 min" repondait encore 200.

Le piege : si on jette ces nouveaux cookies (ce que fait un contexte
Playwright dont on ne sauvegarde pas l'etat), la copie stockee garde un
refresh token deja consomme. Supabase fait tourner ses refresh tokens et
detecte leur reutilisation : rejouer un jeton perime ne renvoie pas une
simple erreur, ca REVOQUE toute la famille de jetons. La session meurt
alors definitivement (401), pas seulement pour l'appel en cours.

Concretement, sans cet appel, chaque execution tuait la session de la
suivante — d'ou des expirations apparemment aleatoires, sans lien avec
l'heure de derniere connexion.

Verification : deux GET successifs sur une session dont le jeton a expire
changent les deux cookies sb-<ref>-auth-token.0/.1.

    from wm_session_io import persist
    ...
    persist(req_ctx, state_path)   # AVANT req_ctx.dispose()
"""

import base64
import json
import time
from pathlib import Path

# Marge avant l'expiration du jeton en dessous de laquelle on force une
# rotation AVANT de commencer un traitement long. 15 min couvre largement
# le pire cas observe (defausse de ~7 min).
FRESH_MARGIN_S = 15 * 60


def token_expires_in(state_path) -> float | None:
    """Secondes restantes avant expiration du jeton d'acces, ou None si on
    n'arrive pas a le lire (format inattendu, fichier absent)."""
    try:
        data = json.loads(Path(state_path).read_text(encoding="utf-8"))
        chunks = {c["name"]: c["value"] for c in data.get("cookies", []) if "auth-token" in c["name"]}
        if not chunks:
            return None
        raw = "".join(chunks[k] for k in sorted(chunks))
        if raw.startswith("base64-"):
            raw = raw[7:]
        payload = json.loads(base64.b64decode(raw + "=" * (-len(raw) % 4)))
        return payload["expires_at"] - time.time()
    except Exception:
        return None


def identite(state_path):
    """Pseudo du compte porte par un fichier de session, ou None.

    Lu hors ligne dans le jeton (user.user_metadata.username), sans le
    moindre appel reseau.

    A appeler AVANT d'ecrire une session dans un secret : le 03/09/2026,
    une session de tigrewiki (compte 4) a ete poussee dans le secret du
    compte 5, ecrasant celle d'oursours -- irrecuperable, un secret GitHub
    ne se relit pas. Les deux comptes ont alors tourne sous la meme
    identite, chacun ecrivant le jeton tourne de l'autre : exactement le
    motif de reutilisation de refresh token qui revoque les sessions."""
    try:
        data = json.loads(Path(state_path).read_text(encoding="utf-8"))
        chunks = {c["name"]: c["value"] for c in data.get("cookies", []) if "auth-token" in c["name"]}
        if not chunks:
            return None
        raw = "".join(chunks[k] for k in sorted(chunks))
        if raw.startswith("base64-"):
            raw = raw[7:]
        payload = json.loads(base64.b64decode(raw + "=" * (-len(raw) % 4)))
        return ((payload.get("user") or {}).get("user_metadata") or {}).get("username")
    except Exception:
        return None


def ensure_fresh(playwright, state_path, base_url):
    """Rend un contexte de requetes dont le jeton a au moins FRESH_MARGIN_S
    de duree de vie, et le chemin de session a jour.

    Pourquoi : les scripts qui bouclent (defausse surtout) enchainent des
    centaines d'appels sur plusieurs minutes. Si le jeton expire EN COURS
    de boucle, le serveur le fait tourner et la requete suivante, partie
    avec l'ancien cookie, declenche la detection de reutilisation de
    Supabase -- qui revoque toute la famille de jetons. Le run tombe alors
    en 401 en plein milieu et la session est morte pour de bon (constate le
    30/08/2026 : compte 1 defausse des dizaines de cartes puis 401 net).

    On force donc la rotation AVANT de commencer, avec un simple GET, puis
    on reconstruit le contexte a partir de l'etat sauvegarde. La boucle
    demarre ainsi avec pres d'une heure devant elle, largement de quoi
    finir sans croiser la frontiere.
    """
    ctx = playwright.request.new_context(storage_state=str(state_path), base_url=base_url)
    remaining = token_expires_in(state_path)

    if remaining is not None and remaining > FRESH_MARGIN_S:
        return ctx  # encore largement valide, rien a faire

    # Un appel anodin suffit a declencher le renouvellement cote serveur.
    try:
        ctx.get("/api/wikibidous")
    except Exception as e:
        print(f"    (pre-chauffage du jeton impossible : {e.__class__.__name__})")
        return ctx

    if not persist(ctx, state_path):
        return ctx

    # Repart d'un contexte propre bati sur les cookies fraichement tournes.
    ctx.dispose()
    now = token_expires_in(state_path)
    if now is not None:
        print(f"    jeton renouvele avant traitement (valide {round(now / 60)} min)")
    return playwright.request.new_context(storage_state=str(state_path), base_url=base_url)


def persist(req_ctx, state_path) -> bool:
    """Reecrit le fichier de session avec les cookies courants du contexte,
    pour conserver un eventuel refresh token tourne par le serveur.

    Ne leve jamais : la sauvegarde est un filet de securite, elle ne doit
    pas faire echouer l'action metier qui vient de reussir. Retourne True
    si l'ecriture a eu lieu.
    """
    try:
        req_ctx.storage_state(path=str(Path(state_path)))
        return True
    except Exception as e:  # contexte deja ferme, disque en lecture seule...
        print(f"    (sauvegarde de la session impossible : {e.__class__.__name__})")
        return False
