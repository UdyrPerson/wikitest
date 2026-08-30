# Contexte projet — wm-reader

## Objectif

Client de lecture personnel pour WikiMasters (https://www.wiki-masters.com),
un jeu de collection de cartes Wikipédia. Récupérer ma collection, mon
historique, le catalogue et mes échanges en cours, en JSON sur mon disque.

Je pilote un vrai Chrome via Playwright, avec ma propre session ouverte à la
main. Le site n'a pas d'API publique documentée : tout est derrière la page de
connexion, et `/pulls` redirige vers `/login` pour un visiteur anonyme.

## Environnement

- Windows, PowerShell
- Python 3.11 (Microsoft Store), pas encore de venv dédié
- Chrome installé, piloté via `channel="chrome"`
- Dossier de travail : `C:\Users\mathi\Desktop\projetwikimaster`

## Les scripts

| Fichier | Rôle |
|---|---|
| `wm_session.py` | Ouvre Chrome, connexion manuelle, écrit `storage_state.json` |
| `wm_session_cdp.py` | Repli si Cloudflare bloque `wm_session.py` : se branche sur un Chrome déjà lancé à la main (`--remote-debugging-port`) au lieu d'en piloter un directement |
| `wm_discover.py` | Rouvre Chrome avec la session, capture tous les appels réseau pendant que je navigue → `captures/` |
| `wm_map.py` | Regroupe les captures par forme d'URL → `captures/routes.json` |
| `wm_read.py` | Rejoue les routes trouvées et écrit le JSON → `data/` |
| `wm_open_booster.py` | Gère la fenêtre Chrome persistante (port CDP fixe 9224, jamais fermée par le script) et l'ouverture de boosters. Sans option : ouvre/rattache la fenêtre sur `/pulls`, aucune action. `--recon` : + repère le bouton, screenshot, sans cliquer. `--click` : ouvre réellement (clic, défilement des 5 cartes, Continuer). `--api [--count N]` : appelle directement `POST /api/packs/open` sans navigateur, écrit les cartes dans `data/` — plus simple mais plus "bot-like" |
| `wm_ouverture_booster.py` | Outil "ouvertureBooster" : ouvre un seul booster avec l'animation complète, en se rattachant à la fenêtre persistante existante (la relance si besoin) |
| `wm_auto_booster.py` | Automatise l'ouverture en boucle toutes les ~10 min (`--interval`, `--max-runs`), intervalle volontairement randomisé (±20%) pour ne pas avoir un rythme parfaitement régulier. S'arrête tout seul si la session expire |
| `wm_market_scan.py` | Récupère un échantillon d'enchères actives sur `/marketplace` par appel API direct (`--pages`, `--sort` parmi `ending_soon`/`recent`/`price_asc`/`price_desc`) → `data/marketplace-*.json`. Pas d'endpoint pour les ventes conclues : le seul proxy de valeur dispo est la mise actuelle sur les enchères en cours |

## Où j'en suis

Les quatre scripts sont écrits et compilent. `wm_discover.py` a été lancé une
fois et s'est arrêté correctement sur « Pas de storage_state.json », ce qui
est le comportement attendu.

Prochaine étape : lancer `wm_session.py`, puis `wm_discover.py`, puis
`wm_map.py`. Ensuite remplir `ENDPOINTS` dans `wm_read.py` à partir de
`captures/routes.json`.

## Le point technique qui va décider de la suite

Le site tourne sur Vercel, avec ce qui ressemble à un Next.js App Router. Deux
scénarios possibles à la découverte :

1. De vraies routes `/api/...` renvoyant de l'`application/json` → il suffit de
   remplir `ENDPOINTS`.
2. Uniquement des requêtes `?_rsc=` en `text/x-component` → les données passent
   par des React Server Components. Le format est interne à Next.js, non
   documenté, instable entre versions mineures. Dans ce cas, passer par
   `DOM_PAGES` dans `wm_read.py` : charger la page et lire le rendu.

`wm_map.py` signale le cas 2 avec un avertissement, et repère aussi les server
actions (en-tête `Next-Action`), qui servent aux écritures et ne m'intéressent
pas.

## Règles à garder

- `storage_state.json` vaut un mot de passe. Il est dans le `.gitignore`, il ne
  sort pas du dossier, il ne va dans aucun dépôt.
- `DELAY` reste à 2 secondes minimum, en séquentiel, sans parallélisme. Le
  risque n'est pas juridique, c'est de perdre le compte.
- Sur un 429, ralentir plutôt qu'insister.
- Note : sous Windows, le `os.chmod` de `wm_session.py` n'agit que sur
  l'attribut lecture seule, il ne produit pas un vrai 600.

## Piste alternative

Les cartes viennent de Wikipédia. Si le besoin porte sur le catalogue global
plutôt que sur ma collection, l'API MediaWiki et les dumps Wikimedia sont
ouverts, documentés et stables.
