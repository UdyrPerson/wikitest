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

from pathlib import Path


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
