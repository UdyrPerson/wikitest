# wm-reader

Client de lecture personnel pour WikiMasters : Chrome piloté par Playwright,
session ouverte à la main, données récupérées en JSON sur ton disque.

**Ce dépôt n'est plus en lecture seule.** Il a commencé comme un simple
lecteur, mais il agit maintenant sur le site : ouverture de boosters, défausse
de cartes, échanges de wikibidous entre comptes, le tout automatisé sur GitHub
Actions. Les scripts purement informationnels (`wm_report_rares.py`,
`wm_market_scan.py`, `wm_sales_reference.py`) restent en lecture, mais ils sont
la minorité.

Ce README décrit la phase de découverte initiale. Pour l'état réel du projet —
comptes, workflows, endpoints, et le piège des jetons Supabase qui a coûté
plusieurs sessions — voir `CLAUDE.md`.

## Installation

```bash
pip install -r requirements.txt
playwright install chromedriver   # inutile si tu utilises ton Chrome (channel="chrome")
```

Les scripts lancent ton Chrome installé via `channel="chrome"`. Si tu n'as pas
Chrome, remplace par `p.chromium.launch(headless=False)` et lance
`playwright install chromium`.

## Déroulé

### 1. Ouvrir une session

```bash
python wm_session.py
```

Chrome s'ouvre sur la page de connexion. Tu te connectes à la main, tu reviens
au terminal, tu appuies sur Entrée. Le script écrit `storage_state.json`
(cookies + localStorage) en chmod 600.

Ton mot de passe ne touche jamais le code. Quand la session expire, tu relances
ce script.

### 2. Découvrir les routes

```bash
python wm_discover.py
```

Chrome se rouvre avec ta session. Navigue vers ta collection, le catalogue, tes
échanges, une fiche de carte, la page 2 d'une liste. Chaque appel réseau
s'affiche dans le terminal et se sauvegarde dans `captures/`.

Puis :

```bash
python wm_map.py
```

Ça regroupe les appels par forme d'URL, normalise les identifiants, liste les
paramètres et montre les clés JSON de chaque réponse. Résultat lisible à
l'écran et dans `captures/routes.json`.

### 3. Lire

Ouvre `wm_read.py`, remplis `ENDPOINTS` avec ce que l'étape 2 a révélé, lance :

```bash
python wm_read.py
```

Sortie dans `data/collection-2026-08-29.json` et compagnie.

## Le cas Next.js

WikiMasters tourne sur Vercel avec ce qui ressemble à un Next.js App Router.
Deux scénarios sortiront de l'étape 2.

Si tu vois des routes du type `GET /api/...` qui renvoient de l'`application/json`,
tu es dans le cas simple. Remplis `ENDPOINTS`, c'est fini.

Si tu ne vois que des requêtes avec `?_rsc=` et un content-type
`text/x-component`, les données transitent par des React Server Components. Le
format est un flux interne à Next.js, non documenté, qui change entre versions
mineures. Les parser est possible mais pénible et fragile. Passe par `DOM_PAGES`
dans `wm_read.py` : tu charges la page et tu lis ce que le navigateur a rendu.
`wm_map.py` te signale ce cas avec un avertissement.

Les `POST` portant un en-tête `Next-Action` sont des server actions. Elles
servent aux écritures. Tu n'en as pas besoin.

## Précautions

`storage_state.json` équivaut à ton mot de passe. Il est dans le `.gitignore`.
Ne le committe pas, ne le colle nulle part. Si tu as un doute, déconnecte-toi
depuis le site pour invalider la session, puis relance `wm_session.py`.

Sur un 429, le script attend 60 secondes. Si tu en vois plusieurs, augmente
`DELAY` au lieu d'insister.

## Statut

La découverte est faite. Le site expose de vraies routes REST `/api/...` en
`application/json` — le scénario simple des deux décrits ci-dessus. Les
endpoints connus sont listés dans `CLAUDE.md`, il n'y a plus besoin de rejouer
`wm_discover.py` / `wm_map.py` sauf si le site change.

Le projet a largement dépassé ce README depuis : cinq comptes, quatre workflows
GitHub Actions, et une table de référence des prix de vente en cours de
constitution. `CLAUDE.md` fait foi.
