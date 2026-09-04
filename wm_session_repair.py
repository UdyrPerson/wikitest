"""
Refait la session d'un compte de test et la repousse dans son secret
GitHub, en une commande.

    python wm_session_repair.py compte2
    python wm_session_repair.py compte2 collecteur
    python wm_session_repair.py --all
    python wm_session_repair.py compte2 --no-push   # s'arrete avant le secret

POURQUOI EN LOCAL ET PAS SUR UN RUNNER

Le projet a deja essaye : refresh-sessions.yml, supprime le 03/09/2026
(commit 423102a) parce qu'il « se connectait pour de vrai, ce qui peut
invalider la session en cours, mais echouait systematiquement sur
Cloudflare avant d'avoir sauvegarde un remplacement ». Un runner GitHub
sort d'une IP de datacenter en Chrome headless : Turnstile le refuse.
En local, dans un vrai Chrome sur une IP residentielle, la meme connexion
passe (verifie le 04/09/2026). La reparation vit donc ici, et les
identifiants n'ont aucune raison d'aller dans des secrets GitHub.

VERIFICATION D'IDENTITE, NON NEGOCIABLE

Le 04/09/2026, une session de tigrewiki a ete poussee dans le secret du
compte 5 en croyant tenir oursours : la session d'oursours a ete perdue
definitivement (un secret GitHub ne se relit pas), et les deux comptes ont
tourne sous la meme identite. Cet outil compare donc le pseudo REELLEMENT
connecte au pseudo attendu, et refuse de pousser au moindre ecart.

IDENTIFIANTS

Dans wm_comptes.json (couvert par le .gitignore), ou a defaut dans
l'environnement, WM_<LABEL>_EMAIL / WM_<LABEL>_PASSWORD :

    {"compte2": {"email": "...", "password": "..."}}

UNE RECONNEXION N'EST PAS ANODINE : elle peut invalider la session en
cours du compte. L'outil repousse le nouvel etat dans la foulee, donc la
fenetre de casse est de quelques secondes -- mais ne le lance pas en
boucle « au cas ou ». On repare ce qui est casse.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from wm_session_io import identite, token_expires_in

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEPOT = "UdyrPerson/wikitest"
IDENTIFIANTS = Path("wm_comptes.json")

# label -> (fichier de session, secret GitHub, pseudo attendu)
COMPTES = {
    "compte1":    ("storage_state.json",   "WM_TEST_STORAGE_STATE",  "ululuminadeL"),
    "compte2":    ("storage_state_2.json", "WM_TEST2_STORAGE_STATE", "ululuminadel1"),
    "collecteur": ("storage_state_3.json", "WM_TEST3_STORAGE_STATE", "wikilover12"),
    "compte4":    ("storage_state_4.json", "WM_TEST4_STORAGE_STATE", "tigrewiki"),
    "compte5":    ("storage_state_5.json", "WM_TEST5_STORAGE_STATE", "oursours"),
}


def charger_identifiants(label: str):
    """(email, mot de passe) depuis wm_comptes.json ou l'environnement."""
    if IDENTIFIANTS.exists():
        try:
            fiche = json.loads(IDENTIFIANTS.read_text(encoding="utf-8")).get(label) or {}
        except Exception as e:
            raise SystemExit(f"{IDENTIFIANTS} illisible : {e.__class__.__name__}")
        if fiche.get("email") and fiche.get("password"):
            return fiche["email"], fiche["password"]

    cle = label.upper()
    email = os.environ.get(f"WM_{cle}_EMAIL")
    mdp = os.environ.get(f"WM_{cle}_PASSWORD")
    if email and mdp:
        return email, mdp
    return None, None


def reconnecter(label: str, pousser: bool = True) -> bool:
    etat, secret, pseudo_attendu = COMPTES[label]
    etat = Path(etat)
    email, mdp = charger_identifiants(label)
    if not email:
        print(f"[{label}] identifiants absents de {IDENTIFIANTS} et de l'environnement — ignore")
        return False

    print(f"\n=== {label} ({pseudo_attendu}) ===")

    # On ecrit d'abord dans un fichier temporaire : tant que l'identite
    # n'est pas confirmee, la session en place n'est pas touchee.
    provisoire = Path(f".repair-{label}.json")
    env = dict(os.environ,
               WM_TEST_EMAIL=email,
               WM_TEST_PASSWORD=mdp,
               WM_TEST_STATE_PATH=str(provisoire))

    r = subprocess.run([sys.executable, "wm_session_auto.py"], env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    for ligne in (r.stdout or "").splitlines():
        if ligne.strip():
            print(f"    {ligne}")

    if r.returncode != 0 or not provisoire.exists():
        print(f"[{label}] connexion echouee — rien n'a ete modifie")
        provisoire.unlink(missing_ok=True)
        return False

    pseudo = identite(provisoire)
    if pseudo is None or pseudo.lower() != pseudo_attendu.lower():
        provisoire.unlink(missing_ok=True)
        print(f"[{label}] REFUS : connecte en tant que {pseudo!r}, attendu {pseudo_attendu!r}.")
        print("           Fichier supprime, secret intact.")
        return False

    reste = token_expires_in(provisoire)
    print(f"    identite confirmee : {pseudo} (jeton valide {round((reste or 0) / 60)} min)")

    provisoire.replace(etat)

    if not pousser:
        print(f"[{label}] session ecrite dans {etat} — secret NON pousse (--no-push)")
        return True

    p = subprocess.run(["gh", "secret", "set", secret, "--repo", DEPOT],
                       stdin=etat.open("rb"), capture_output=True, text=True)
    if p.returncode != 0:
        print(f"[{label}] echec de la poussee du secret : {(p.stderr or '').strip()[:200]}")
        return False

    print(f"[{label}] OK — {secret} mis a jour")
    return True


def main():
    argv = sys.argv[1:]
    pousser = "--no-push" not in argv
    labels = [a for a in argv if not a.startswith("--")]

    if "--all" in argv:
        labels = list(COMPTES)
    if not labels:
        raise SystemExit(
            "Usage: python wm_session_repair.py <compte> [...] | --all [--no-push]\n"
            f"Comptes connus : {', '.join(COMPTES)}"
        )

    inconnus = [l for l in labels if l not in COMPTES]
    if inconnus:
        raise SystemExit(f"Compte(s) inconnu(s) : {inconnus}. Connus : {', '.join(COMPTES)}")

    ok = [l for l in labels if reconnecter(l, pousser)]
    rates = [l for l in labels if l not in ok]

    print(f"\n{len(ok)}/{len(labels)} compte(s) repare(s)"
          + (f" — echec : {', '.join(rates)}" if rates else ""))
    if rates:
        sys.exit(1)


if __name__ == "__main__":
    main()
