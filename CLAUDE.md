# Contexte projet — wm-reader

## Objectif

WikiMasters (https://www.wiki-masters.com) est un jeu de collection de cartes
Wikipédia. Le projet a commencé comme un client de **lecture** personnel ; il
est devenu une **automatisation multi-comptes** qui tourne toute seule sur
GitHub Actions.

Ce qu'il fait aujourd'hui :

- ouvre les boosters de 9 comptes, en continu, sans machine allumée ;
- défausse les cartes communes (C/PC/R/SR) pour les convertir en wikibidous ;
- consolide les wikibidous des 8 comptes émetteurs vers un compte collecteur,
  via le système d'échange du jeu ;
- lit le marché depuis le compte premium et en tire une table de référence
  des prix de vente réels (**terminée** : 1798 L et 12 245 UR) ;
- met automatiquement aux enchères les meilleures L et UR de chaque compte,
  et repositionne le prix des invendues.

Le nom `wm-reader` est donc un reste historique : les scripts écrivent
maintenant sur le site (ouverture, défausse, échanges, mises en vente).

## Environnement

- Windows, PowerShell
- Python 3.11 (Microsoft Store) ; un `.venv/` existe dans le dossier
- Chrome installé, piloté via `channel="chrome"`
- Dossier de travail : `C:\Users\mathi\Desktop\projetwikimaster`
- Dépôt : `github.com/UdyrPerson/wikitest`, **public** (les pseudos ont été
  anonymisés dans le code à cette fin — commit `3f90fbf`). Les minutes Actions
  y sont donc gratuites et illimitées

## Les deux plans d'exécution

Le projet vit sur deux plans qu'il ne faut pas confondre :

**1. GitHub Actions — pure API, aucun navigateur.** C'est ce qui tourne en
production, toutes les 50 min. Les sessions des 9 comptes de test vivent dans
des secrets GitHub (`WM_TEST_STORAGE_STATE` … `WM_TEST9_STORAGE_STATE`), sont
écrites en `state1.json` … `state9.json` au début du job, et **repoussées dans
leur secret juste après le passage de leur compte** (voir « Le piège central »
plus bas). Playwright n'est installé que pour son client HTTP :
`pip install playwright`, sans `playwright install`.

**2. Local — Chrome persistant via CDP.** Pour ce qui demande un vrai
navigateur : ouvrir une session à la main, regarder une animation d'ouverture,
travailler sur le compte premium. Le pattern est toujours le même : Chrome est
lancé en **processus indépendant** (`CREATE_BREAKAWAY_FROM_JOB` +
`DETACHED_PROCESS`) avec un port de débogage fixe, et Playwright s'y branche
seulement pour piloter la page. Un Chrome lancé par `chromium.launch()` est tué
dès que le script se termine — d'où le détour.

Ports CDP : 9224–9229 puis 9231–9233 pour les comptes de test (un par
compte, cf `wm_open_all_sessions.py`), **9230 pour le premium** — d'où le
trou dans la série.

## Les comptes

| | Rôle |
|---|---|
| Comptes 1, 2, 4, 5, 6, 7, 8, 9 | Émetteurs : ouvrent des boosters, défaussent, offrent leur solde |
| Compte 3 | **Collecteur** : ouvre et défausse aussi, mais reçoit les échanges au lieu d'en envoyer |
| Compte premium | Compte principal, séparé de l'automatisation. Seul à voir l'historique des ventes conclues. Session dans `storage_state_premium.json` |

Le pseudo du collecteur est dans le secret `WM_TRADE_RECIPIENT`, pas en dur
dans le code. Le collecteur doit être « ami » avec chaque émetteur pour que
l'échange passe.

Les quatre derniers comptes ont été ajoutés le 04/09/2026. Aucun endpoint de
**recherche d'utilisateur** n'existe : `POST /api/friends` exige l'UUID du
destinataire, qu'on lit hors ligne dans le jeton de sa session
(`user.id`). `/api/friends/requests` renvoie 405 sur GET comme sur POST —
c'est la route dynamique `/api/friends/{id}` qui capte le mot « requests ».

**`storage_state.json` porte le compte de test 1**, pas le compte principal.
L'écraser casse l'automatisation — c'est pour ça que le premium a son propre
fichier.

### Créer un compte se fait en local, pas sur un runner

`wm_signup.py` enchaîne les quatre étapes : adresse jetable sur
temp-mail.org, `/signup`, récupération du code, activation. Vérifié de bout
en bout le 07/09/2026 (compte `jipise8650`).

Trois choses apprises en le construisant :

- le site n'envoie **pas un lien mais un code à six chiffres**, à saisir
  dans `#signup-otp-code`. Le mail met une à deux minutes à arriver ;
- le formulaire a **deux cases à cocher** obligatoires (majorité,
  conditions d'utilisation) en plus des trois champs ;
- dans la boîte temp-mail, plusieurs `a.viewLink` pointent le même
  message et **le premier porte `href="javascript:void(0)"`** — naviguer
  dessus lève `ERR_ABORTED`. Ne garder que les `href` en `http`.

`.github/workflows/signup.yml` existe, en déclenchement manuel, mais
**échoue sur un runner GitHub** — mesuré, pas supposé, le 07/09/2026 :
temp-mail.org ne délivre jamais d'adresse depuis une IP de datacenter, et
le script s'arrête à l'étape 1 sur 4. Turnstile sur `/signup` serait
l'obstacle suivant, celui-là même qui a fait supprimer
`refresh-sessions.yml`. Le workflow ne devient utile que sur un **runner
auto-hébergé** ; sinon, la commande locale fait le travail.

Les identifiants créés vont dans `comptes_crees.txt`, **couvert par le
`.gitignore`** — dépôt public. Le mot de passe n'est jamais imprimé sur la
sortie standard pour la même raison : les logs de run d'un dépôt public
sont lisibles par tout le monde.

## L'API du site

La question ouverte des débuts (vraies routes JSON ou payloads RSC ?) est
**tranchée : le site expose de vraies routes REST `/api/...` en
`application/json`**. La phase de découverte (`wm_discover.py` → `wm_map.py`)
a fait son travail et n'a plus besoin d'être rejouée sauf changement du site.

Endpoints connus et utilisés :

| Route | Usage |
|---|---|
| `POST /api/packs/open` | Ouvre un booster, renvoie les 5 cartes **déjà révélées** + `packs_remaining`. **429 = limite quotidienne**, voir plus bas |
| `GET /api/my-collection?sort=rarity&rarity=X&page=N&stats=0` | Ma collection |
| `POST /api/user-cards/bulk-discard` | Défausse par lots de 50 (`{"card_ids": [...]}`) |
| `POST /api/user-cards/{user_card_id}/discard` | Défausse à l'unité (ancien chemin) |
| `GET /api/cards?rarity=X&page=N` | Catalogue global, 50/page |
| `GET /api/marketplace?page=N&limit=50&sort=...` | Enchères **actives** uniquement (16 793 le 03/09/2026) |
| `POST /api/marketplace` | **Met une carte aux enchères.** `{"card_id": <user_card_id>, "base_amount": N, "duration_minutes": M}` → 201 `{"auction_id": ...}` |
| `GET /api/marketplace/mine` | `{"sellingCount": N, "maxConcurrentAuctions": M}` — **le plafond dépend du compte** : 5 sur un compte de test, 10 sur le premium |
| `GET /api/marketplace/{auction_id}` | L'enchère complète : carte, mises, `end_at`, `status`, `final_price`, `base_repriced_at` |
| `GET /api/marketplace/cards/{card_id}/sales` | **Ventes conclues** (`final_price`, `settled_at`) — **premium seulement** |
| `POST /api/marketplace/{auction_id}/bid` | **Mise sur une enchère.** `{"amount": N}` → renvoie `bidder_balance`. Refus en 409 `{"code":"bid_too_low","min":N}` |
| `POST /api/marketplace/{auction_id}/reprice` | Repositionne le prix d'une annonce (vue dans le JS, pas encore utilisée) |
| `POST /api/marketplace/{auction_id}/settle` | Règle une enchère (vue dans le JS, pas encore utilisée) |
| `GET /api/friends` | `{"friendships": [...], "counts": {...}}` — résout aussi un pseudo en `recipient_id` |
| `POST /api/friends` | **Demande d'amitié.** `{"addressee_id": <uuid>}` → 201. Attend l'UUID, **pas** le pseudo (`{"error":"addressee_id requis"}` sinon) |
| `PATCH /api/friends/{friendship_id}` | `{"action":"accept"}` → 200 `{"status":"accepted"}` |
| `GET /api/wikibidous` | Solde courant |
| `GET /api/trades`, `POST /api/trades`, `PATCH /api/trades/{id}` | Échanges (`{"action":"accept"}`). **50 échanges/jour et par compte**, voir plus bas |

Trois pièges :

- `user-cards/{id}` désigne **la possession**, pas la carte (`card_id` est
  l'identifiant global).
- **`POST /api/marketplace` attend lui aussi l'identifiant de possession**,
  bien que le champ s'appelle `card_id`. Preuve dans la capture du
  29/08/2026 : le POST envoie `0ec8e44d…` et l'enchère créée renvoie
  `card_id: e6617907…` — deux valeurs différentes.
- La pagination de `/api/cards` n'a **pas d'ordre stable** : la même carte
  revient sur plusieurs pages, il faut dédoublonner (776 doublons sur 1800
  cartes L, le 03/09/2026).

Et une nuance de nommage : la collection renvoie ses lignes sous la clé
`collection`, alors que le catalogue les renvoie sous `cards`.

**Il existe une limite QUOTIDIENNE d'ouverture de paquets, distincte du
stock.** Elle se manifeste par un 429 sur `/api/packs/open` (relevé le
04/09/2026) :

```json
{"error": "Limite quotidienne de paquets atteinte. Réessayez plus tard.",
 "rate_limited": true, "rate_limit_daily": true,
 "retry_after": "2026-09-04T19:47:16Z", "packs_remaining": 10}
```

avec un en-tête `retry-after` de 44 092 s, soit **12 h**. Trois choses à en
retenir :

- ce n'est **ni une sanction ni une détection de bot** : c'est une règle de
  jeu ordinaire ;
- le **stock est conservé** (`packs_remaining: 10`) — les paquets ne sont
  pas perdus, seul le droit de les ouvrir est suspendu ;
- attendre est inutile à cette échelle. On s'arrête net et on affiche
  l'heure de reprise, plutôt que de boucler.

Le stock, lui, semble plafonner autour de 9–10 : pendant une ouverture en
rafale, `packs_remaining` reste à 9 sur les premiers paquets avant de
décroître, la régénération compensant au fil de l'eau.

### Le plafond d'ouverture vaut 144 paquets par 24 h glissantes

Établi le 06/09/2026 par deux chemins indépendants qui concordent.

**Ce que le site publie.** Ses constantes client donnent la régénération,
pas le plafond : `PACK_REGEN_PERIOD_MINUTES = 10` (PRO : 3),
`MAX_CONCURRENT_AUCTIONS_REGULAR = 5` (PRO : 10),
`BATTLE_CHALLENGES_PER_DAY_FREE = 3` (PRO : 20). Le nombre de paquets
n'apparaît nulle part — ni dans le JS, ni dans le message du 429, qui
reste vague là où celui des échanges chiffre « 50/jour · PRO : 200/jour ».

**Ce que nos journaux prouvent.** Sur 96 runs d'ouverture (03→06/09) et
**190 événements de limite** : le nombre de paquets ouverts dans les 24 h
précédant le refus vaut **144, en médiane comme en maximum**. Les seules
valeurs plus basses (114) tombent au bord de la fenêtre de journaux, où
la donnée manque.

**C'est une fenêtre glissante, pas une remise à zéro.** `retry_after`
vaut l'instant du 144ᵉ paquet le plus récent, plus 24 h : `reprise − 24 h`
tombe à **30 s en médiane** d'un paquet réellement ouvert, sur 182 des
190 événements. Une remise à zéro à heure fixe donnerait au contraire des
heures de reprise identiques d'un jour à l'autre ; les nôtres dérivent
toujours vers l'avant (compte 2 : 19:43 → 19:45 → 20:30 sur trois jours).

**144 = 1440 / 10.** Le plafond vaut donc exactement la régénération d'une
journée. Ce n'est pas une règle anti-triche distincte : **on ne peut pas
ouvrir plus qu'on ne régénère**. Trois conséquences pratiques :

- **être limité n'est pas un problème, c'est la preuve du rendement
  maximal.** Le 06/09, huit comptes sur neuf étaient à **144/144**, le
  neuvième à 100 (session révoquée le matin) ;
- **`--count` n'influe pas sur le débit.** Tant que les runs sont assez
  fréquents pour que le stock (plafonné à 10) ne déborde pas, le total
  journalier vaut 144 quoi qu'on demande. À 50 min de cadence on
  accumule 5 paquets entre deux passages : aucune perte ;
- **la seule façon d'aller plus haut est PRO**, dont la régénération de
  3 min prédit un plafond de 480/24 h — non vérifié, faute de compte PRO.

**Le bon indicateur n'est donc pas « suis-je limité ? » mais « suis-je à
144 ? »** Un compte en dessous a un problème de disponibilité (session
morte), pas de quota.

C'est désormais mesuré à chaque run : `wm_open_booster.py --compteur`
journalise ce que chaque compte ouvre, et `wm_pack_report.py` publie le
tableau `n/144` dans le résumé de `boosters.yml`. L'état vit dans la
**variable de dépôt `WM_PACK_LOG`** — pas dans un commit, ce workflow
passant une trentaine de fois par jour ; format compact (une entrée par
compte et par passage), 3,6 Ko dans le pire cas contre 48 Ko de limite.
C'est un détecteur plus fin que le code de sortie du job : une session
qui meurt en milieu de journée laisse tous les runs verts et ne se
trahit que par un rendement qui s'effondre.

**Il existe aussi une limite quotidienne d'ÉCHANGES**, distincte de celle
des paquets. Elle se manifeste par un 429 sur `PATCH /api/trades/{id}`
(relevé le 05/09/2026) :

```json
{"error": "Limite quotidienne d'échanges atteinte (offres envoyées et
 acceptations). Comptes gratuits : 50/jour · PRO : 200/jour.",
 "code": "trade_daily_limit"}
```

Le point important est entre parenthèses : **envois et acceptations
partagent le même compteur**. Un émetteur n'en consommait qu'un par
passage, soit ~29/jour à la cadence de 50 min — sous le plafond. Le
**collecteur**, lui, est de tous les échanges : huit émetteurs lui
adressaient ~230 offres par jour, plus de quatre fois son quota. Le surplus
ne disparaît pas, il s'empile en `pending` — **99 offres en attente sur 199
échanges au total** le 05/09/2026 à 11:15.

D'où la cadence **journalière** de `trade.yml`, seule exception aux 50 min
du projet : 1 envoi par émetteur, 8 acceptations pour le collecteur. Rien
n'est perdu, `wm_trade_gift_wb.py` offrant tout le solde du moment.

Deux leçons de l'incident, au-delà du quota :

- **le collecteur n'a pas de filet.** Un émetteur qui rate son tour
  rattrape au suivant, son solde étant cumulatif ; le collecteur, non —
  ce qu'il n'accepte pas reste en attente. Son étape n'avait pourtant ni
  `id` ni `continue-on-error`, donc elle ne figurait pas dans le bilan de
  fin. Sa session est morte le 05/09 à 11:29 et chaque run a affiché
  « Tous les comptes ont ete traites » pendant six heures ;
- **on s'arrête au premier `trade_daily_limit`.** Une fois le quota
  atteint, toutes les offres suivantes prendront le même 429 : insister,
  c'était 147 appels perdus à deux secondes d'intervalle. Même traitement
  que la limite de paquets — on s'arrête net et on annonce ce qui reste
  en attente. Un 429 ne fait toujours pas échouer le job.

**`mine=1` ne filtre pas.** Le paramètre laisse `auctions` sur le marché
entier et **ajoute** à côté les tableaux `selling`, `bidding`, `history`,
`won` — ce sont eux qui concernent le compte. Lire `auctions` en croyant
avoir ses propres annonces revient à s'interdire 50 cartes prises au hasard
(bug réel, corrigé le 03/09/2026). `selling` est aussi la bonne source pour
repérer les annonces à repositionner.

**Trois limites du marché en lecture, relevées le 06/09/2026 en construisant
le rachat.**

- **`limit` est plafonné à 50.** `limit=100` et `limit=200` renvoient
  **zéro** enchère, sans erreur ni message. Demander plus grand ne
  raccourcit pas le balayage, il le rend aveugle — un script conclurait
  « rien trouvé » sur un marché plein. `wm_market_buy.py` borne donc la
  valeur au lieu de se contenter de la documenter.
- **Aucun filtre par vendeur n'existe.** `seller_id`, `seller`, `user_id`,
  `sellerId` et `search` sont tous ignorés : le `total` reste identique et
  les résultats ne sont pas filtrés. Le JS du site ne connaît aucune route
  « enchères d'un joueur ». Pour trouver les annonces de quelqu'un, il faut
  parcourir les ~15 500 enchères actives.
- **La pagination n'est pas exhaustive**, pour la même raison que
  `/api/cards` : un balayage complet a vu **15 223 enchères distinctes pour
  un `total` annoncé à 15 336**. Les enchères qui se règlent en cours de
  route décalent les suivantes vers l'avant, et ce qui passe sous le
  curseur n'est jamais lu. Un balayage « complet » n'est donc pas une
  garantie — mieux vaut un balayage **court** trié par `sort=recent`, qui
  laisse moins de temps à la liste pour bouger.

Tris acceptés : `recent`, `ending_soon`, `price_asc`, `price_desc`.

**Mise minimale**, transcrite du JS du site :

```js
current_bid == null -> base_amount
sinon               -> max(ceil(1.1 * current_bid), current_bid + 1)
```

Conséquence pour le rachat : **le prix demandé n'est atteignable que sur une
enchère vierge.** Dès qu'un tiers mise, le minimum saute de 10 % et dépasse
le prix fixé par le vendeur.

D'où la stratégie de `wm_market_buy.py` : **ne racheter que les enchères qui
arrivent à terme** (`sort=ending_soon`, fenêtre de 10 min). Une mise posée
tôt affiche un meneur pendant des heures et invite la surenchère ; posée
dans les dernières minutes, elle laisse beaucoup moins de temps à un tiers
pour réagir. Effet de bord appréciable : le balayage tombe de ~180 pages à
quelques-unes, donc la perte de pagination décrite plus haut devient
négligeable. En contrepartie, une annonce longue n'est vue qu'en fin de vie.

Aucun endpoint de **retrait** d'enchère n'a été observé : une annonce postée
va à son terme.

**Cycle d'une annonce invendue** (vérifié de bout en bout le 03/09/2026) :

1. à `end_at`, le statut passe à **`settled_unsold`** et `settled_at` est
   renseigné dans les 5 secondes ; `winner_id` et `final_price` restent nuls ;
2. l'annonce quitte `selling` pour **`history`**, en conservant son
   `base_amount` — c'est ce qui permet de retrouver le dernier prix demandé
   sans stocker d'état localement ;
3. la carte **revient dans la collection**, mais **pas immédiatement** :
   absente ~2 min après le règlement, présente quelques minutes plus tard. Un
   traitement qui passerait juste après l'expiration ne la verrait pas encore.

**Durées d'enchère : 10 min, 30 min, 1 h, 3 h, 6 h, 12 h ou 24 h**, rien
d'autre — ce sont les sept paliers proposés par l'interface du jeu. Le serveur, lui, est plus permissif : il
accepte toute valeur entre 10 min et 24 h (vérifié le 03/09/2026, 15 min
accepté, 5 min refusé avec « Durée invalide (entre 10 minutes et 24
heures) »). On s'en tient aux paliers de l'interface : une annonce d'une
durée que le jeu ne propose pas se signale comme automatisée.

Une carte mise en vente **quitte la collection** le temps de l'enchère
(vérifié le 03/09/2026 : compte 5, unique L mise en vente, `/api/my-collection`
la renvoie ensuite vide et la carte apparaît dans `selling`). L'exclusion des
cartes déjà en vente est donc une ceinture-bretelles : elle ne sert que si le
compte détient plusieurs exemplaires de la même carte, cas où l'on ne veut pas
les mettre en concurrence.

## Les scripts

### Sessions

| Fichier | Rôle |
|---|---|
| `wm_session_io.py` | **Cœur du projet.** `ensure_fresh()` / `persist()` / `token_expires_in()` : tout script authentifié doit passer par là (voir « Le piège central ») |
| `wm_session.py` | Connexion manuelle → `storage_state.json`. Le mot de passe ne passe pas par le code |
| `wm_session_auto.py` | Connexion **automatique** pour les comptes de test : `WM_TEST_EMAIL` / `WM_TEST_PASSWORD` en variables d'environnement, `WM_TEST_STATE_PATH` pour choisir le fichier de sortie. Gère le widget Cloudflare Turnstile |
| `wm_session_premium.py` | Session du compte principal, en **deux temps** : un appel ouvre la fenêtre (port 9230), `--save` récupère les cookies une fois connecté. Le découpage existe parce qu'un « appuie sur Entrée » ne marche pas quand un agent lance le script sans terminal interactif |
| `wm_session_window.py` | Généralisation du précédent à **n'importe quel compte** : `--state` + `--port`, même découpage en deux temps. C'est la seule façon de refaire une session de compte de test, la connexion automatisée échouant sur Cloudflare depuis un runner |
| `wm_session_repair.py` | **Refait la session d'un compte et la pousse dans son secret, en une commande.** `python wm_session_repair.py compte2` ou `--all`. Écrit d'abord dans un fichier temporaire et ne remplace la session qu'une fois `identite()` confirmé — sur écart, il supprime et laisse le secret intact. Identifiants dans `wm_comptes.json` (couvert par le `.gitignore`) ou dans l'environnement |
| `wm_session_cdp.py` | Repli si Cloudflare bloque le Chrome piloté : Chrome lancé à la main, Playwright branché seulement après connexion |
| `wm_open_all_sessions.py` | Ouvre une fenêtre par compte, côte à côte. **Attention** : une fenêtre laissée ouverte fait tourner le jeton et périme le secret GitHub |

### Actions de jeu

| Fichier | Rôle |
|---|---|
| `wm_open_booster.py` | Le gros morceau (33 Ko). Gère la fenêtre persistante et l'ouverture. Sans option : ouvre/rattache, aucune action. `--recon` : repère + screenshot, sans cliquer. `--click` : animation complète. `--api [--count N] [--state F]` : `POST /api/packs/open` en direct, **c'est ce mode qu'utilisent les workflows** |
| `wm_discard.py` | Défausse par lots de 50 via `bulk-discard`. Liste blanche stricte de raretés — refuse tout ce qui n'y est pas. Relit la page 0 après chaque lot au lieu de paginer. `--label`/`--json-out` écrivent un fragment, `--merge` en fait le tableau du résumé de run |
| `wm_sell_auto.py` | **Le moteur de vente.** Choisit et met en vente les meilleures cartes d'un compte : prix = moyenne × 1,10, durée 6 h, priorisation par espérance de gain. La stratégie complète et ses justifications chiffrées sont dans son docstring. `--rarities L,UR` quand UR sera prêt |
| `wm_reference_build.py` | Condense `data/sales-{rareté}.jsonl` (5,6 Mo, non versionné) en `reference/{rareté}.json` (479 Ko, **versionné**). Sans ce fichier commité, le workflow de vente n'a aucun prix de référence sur un runner |
| `wm_sell.py` | **Met une carte aux enchères, à la main.** Simulation par défaut, `--go` obligatoire pour écrire, et `--go` exige une possession désignée (`--card`) dont la rareté est revérifiée dans la collection avant l'appel. Lit le plafond d'emplacements au lieu de le supposer, et refuse de vendre depuis la session premium. Suggère un prix depuis `data/sales-{rareté}.jsonl` |
| `wm_trade_gift_wb.py` | Offre **tout** le solde de wikibidous à un ami, sans carte en retour |
| `wm_trade_accept_all.py` | Accepte toutes les offres `pending`. Ne distingue pas reçu/envoyé — à n'utiliser que sur un compte qui ne fait que recevoir |
| `wm_market_buy.py` | **Rachète au prix demandé les enchères d'un vendeur qui arrivent à terme.** Le vendeur vient de `WM_MARKET_SELLER` (UUID ou pseudo) et **n'est jamais imprimé** — dépôt public. Balaie en `sort=ending_soon` et s'arrête à `--fenetre-min` (10 min) : miser tard limite la surenchère et réduit le balayage à quelques pages. Saute ce sur quoi un tiers a déjà misé, et ce dont on est déjà le meneur (idempotent) |
| `wm_ouverture_booster.py` | Outil « ouvertureBooster » : un booster avec l'animation, en réutilisant la fenêtre persistante |
| `wm_auto_booster.py` | Boucle locale toutes les ~10 min (±20 % de variation). Largement remplacé par les workflows |

### Lecture et analyse

| Fichier | Rôle |
|---|---|
| `wm_pack_report.py` | **Rendement d'ouverture sur 24 h glissantes**, en Markdown pour `$GITHUB_STEP_SUMMARY`. Lit le journal alimenté par `wm_open_booster.py --compteur`. Signale les comptes sous 80 % du plafond de 144 — et se tait tant que le journal a moins de 23 h de recul, pour ne pas crier au loup au démarrage |
| `wm_report_rares.py` | Rapport UR/L agrégé sur plusieurs comptes + solde du collecteur. Sortie Markdown, lisible telle quelle dans `$GITHUB_STEP_SUMMARY`. Arguments en `label=chemin_session`, `--json-out` pour un fragment, `--merge` pour agréger |
| `wm_sales_reference.py` | **Chantier en cours.** Table de référence des prix par rareté depuis le compte premium. Sortie JSONL écrite au fil de l'eau, **reprenable** : relancer saute ce qui est déjà connu. Rafraîchit le jeton en cours de boucle. `--stats-only` pour le rapport sans appel réseau |
| `wm_scrape_launch.py` | Lance le scrape ci-dessus en **processus vraiment indépendant** (sinon un scrape de 45 min meurt avec l'outil qui l'a lancé). Journal dans `data/scrape-{rareté}.log` |
| `wm_sales_scan.py` | Ancêtre du précédent : mêmes ventes conclues, mais sur une liste de cartes, avec vitesse adaptative |
| `wm_market_scan.py` | Échantillon d'enchères **actives** (`--pages`, `--sort`, `--only-bid`). Utile avant de disposer des vraies ventes |
| `wm_discover.py`, `wm_map.py`, `wm_read.py` | Outillage de découverte. Ont rempli leur rôle ; à ressortir si le site change |

## Les workflows

| Fichier | Cadence | Ce qu'il fait |
|---|---|---|
| `boosters.yml` | 50 min, minutes 0/10/20/30/40 | Les 9 comptes en **séquentiel dans un seul job**, `--count 10` chacun (voir la limite quotidienne). Publie le **rendement sur 24 h** (n/144) dans le résumé du run |
| `discard.yml` | 50 min, +5 min | Défausse `C,PC,R,SR` sur les 9 comptes. Publie le **récapitulatif des cartes défaussées** par compte et par rareté dans le résumé du run |
| `trade.yml` | **1 jour**, 00:58 UTC | Les 8 émetteurs offrent leur solde, puis le collecteur accepte tout (voir la limite de 50 échanges/jour) |
| `sell.yml` | 50 min, +15 min | Met en vente les meilleures **UR et L** des 9 comptes. Entrée `dry_run` (vraie par défaut en manuel), `rarities` pour restreindre |
| `market-buy.yml` | **manuel** | Le collecteur rachète au prix demandé les enchères du vendeur suivi (`WM_MARKET_SELLER`), **parmi celles qui finissent dans moins de 10 min**. `dry_run` **faux** par défaut, plus `complet`, `fenetre_min`, `max_depense` |
| `report-rares.yml` | manuel | Lecture seule, rapport dans le résumé du run |

Quatre choses à savoir avant d'y toucher :

**`market-buy.yml` est manuel, et ce n'est pas un oubli.** Il a d'abord
tourné en `*/10`. Mesure sur 8 h le 06/09/2026, en comptant les
événements `schedule` réellement reçus :

| Workflow | Motif | Obtenus | Attendus | Taux |
|---|---|---|---|---|
| `boosters` | 6 lignes | 11 | 10 | **115 %** |
| `discard` | 6 lignes | 10 | 10 | **104 %** |
| `sell` | 6 lignes | 4 | 10 | 42 % |
| `market-buy` | `*/10` | **1** | **48** | **2 %** |

**GitHub déprioritise massivement les crons courts** : le rachat partait
une fois toutes les huit heures, pas toutes les dix minutes. Aucune
cadence de 10 min n'est atteignable, quoi qu'on écrive — une enchère de
10 minutes ne sera donc jamais rattrapée par un cron. Plutôt que
d'entretenir un automatisme qui ne part presque jamais, on le lance à la
main quand on sait qu'il y a quelque chose à racheter ; un déclenchement
manuel part immédiatement, lui.

Noter aussi que le motif à 6 lignes n'est pas une garantie en soi :
`sell` l'utilise et n'obtient que 42 %, sans explication vérifiable de
l'écart avec `boosters` et `discard`. Et un **trou global de 94 min**
(09:43 → 11:17 UTC) a touché tous les workflows le même jour : c'est
l'ordonnanceur de GitHub, pas le dépôt.

**Un aléa réseau n'est pas une session morte.** Le 07/09/2026, un
`read ETIMEDOUT` sur `POST /api/packs/open` a fait échouer les comptes 2
et 4 et rougir le job, sous le libellé « session probablement révoquée » —
alors que les deux tournaient à 100 % de leur plafond. `open_via_api`
s'arrête désormais proprement pour ce compte au lieu de propager, **sans
réessayer** : la route n'est pas idempotente, et insister ouvrirait un
second paquet dont on perdrait aussi les cartes. Les quatre workflows
distinguent maintenant, dans leur résumé, le 401 (vraie révocation) du
timeout — une reconnexion inutile invalide une session saine.

**Le défaut de timeout de Playwright est de 30 s, et c'est trop court.**
La défausse du compte 4 est morte le 06/09/2026 sur un `TimeoutError` en
plein `POST /api/user-cards/bulk-discard` — pas une session révoquée,
contrairement à ce qu'annonçait le résumé du run. Tous les appels
d'écriture sont désormais à 90 s. Le pire cas est `/api/packs/open` : un
timeout y ouvre le paquet côté serveur, révèle ses cartes et décrémente
le stock, mais on perd la réponse qui les portait — rien ne les rattrape.


**La cadence de 50 min n'est pas un chiffre rond par hasard.** Le jeton
Supabase dure exactement 1 h. Un cycle plus court que l'heure évite de tomber
systématiquement après l'expiration. Comme 50 ne divise pas 60, il faut
**six lignes cron** pour couvrir le motif, qui ne se referme qu'au bout de 5 h
(`*/50` est invalide sur le champ des minutes).

**Un seul job, pas cinq.** GitHub facture à la minute entamée **et par job** :
cinq jobs de 30 s coûtaient 5 minutes. C'est ce qui a fait exploser le quota
(2 000 min/mois) et bloqué tous les workflows le 03/09/2026. La fusion coûte
2–3 min par run. Contrepartie assumée : les comptes ne sont plus décorrélés
dans le temps.

**`continue-on-error` sur chaque compte.** Sans ça, le premier 401 arrêtait le
job et les comptes suivants n'étaient jamais traités.

**Un cron demandé n'est pas un cron obtenu.** GitHub documente que
l'événement `schedule` peut être retardé ou abandonné en période de charge,
et l'écart est mesurable. Sur 13 h, du 05/09 18:00Z au 06/09 07:00Z :

| Workflow | Lignes cron | Runs obtenus |
|---|---|---|
| `boosters` | 6 | 12 |
| `discard` | 6 | 12 |
| `sell` (`50 * * * *`) | 1 | **5** sur 13 attendus |

Et les cinq runs de vente sont tombés à 06:30, 01:20, 23:20, 21:43, 19:32 —
jamais à la minute demandée, avec des trous de 2 à 5 h. Conséquence directe :
le 06/09 à 06:38, **sept comptes sur huit étaient à 0 annonce sur 5**, faute
de passage depuis 01:20. La leçon est de s'aligner sur le motif à six lignes
qui marche, pas de demander plus souvent — `sell.yml` l'a adopté avec un
décalage de +15 min sur l'ouverture.

Corollaire pour la durée des enchères : **une annonce plus courte que
l'intervalle entre deux passages ne liquide pas plus vite, elle crée du
temps mort.** `DUREE_DESCENTE` valait 3 h pour accélérer les descentes ;
c'était supposer qu'un emplacement libéré est repris aussitôt. Repassée à
6 h le 06/09.

**Tous les workflows partagent le groupe de concurrence `wm-sessions`**, et
c'est vital. Ils chargent les mêmes neuf secrets au démarrage et les
repoussent après chaque compte : deux qui tournent en même temps repoussent
des jetons issus de copies chargées à des instants différents, ce qui rejoue
un refresh token déjà consommé et **révoque la session**.

C'était la cause des morts de sessions apparemment aléatoires (trouvée le
04/09/2026). Le déclencheur : un compte recevant un **429** sur
`/api/packs/open` faisait boucler `open_via_api()` sur des pauses de 60 s,
une par tentative restante — soit **30 minutes par compte limité**, et des
runs de boosters de 60 à 93 min. La défausse (+5 min), le trade (+25 min) et
la vente passaient tous pendant ce temps. `MAX_429` plafonne désormais ces
pauses à deux.

## Le piège central : la rotation des jetons Supabase

C'est la cause racine d'une série de « sessions expirées » incompréhensibles
(trouvée le 30/08/2026) et la chose à comprendre avant de toucher à quoi que
ce soit d'authentifié.

Le site tourne sur Next.js + `@supabase/ssr`. Le jeton d'accès dure une heure,
mais **ce n'est pas une limite dure** : quand une requête arrive avec un jeton
expiré, le serveur le renouvelle lui-même depuis le refresh token et renvoie
de **nouveaux cookies, avec un refresh token tourné**. D'où des sessions
« expirées depuis 170 min » qui répondaient encore 200.

Le piège : si on jette ces nouveaux cookies — ce que fait tout contexte
Playwright dont on ne sauvegarde pas l'état — la copie stockée garde un refresh
token **déjà consommé**. Supabase détecte la réutilisation et ne renvoie pas
une simple erreur : il **révoque toute la famille de jetons**. La session meurt
pour de bon.

Les conséquences pratiques, toutes déjà payées une fois :

- **Tout script authentifié appelle `persist()`**, y compris les scripts en
  lecture seule : un simple GET suffit à déclencher la rotation.
- **Chaque compte repousse son secret juste après son passage**, jamais tous à
  la fin — sinon on écrase un secret qu'un autre workflow a rafraîchi
  entre-temps (constaté sur les comptes 4 et 5, traités en dernier).
- **On rafraîchit avant les boucles longues**, pas au milieu
  (`FRESH_MARGIN_S` = 15 min).
- **Un 403 n'est pas une session expirée.** Ne pas le traiter comme tel.
- **Une fenêtre Chrome laissée ouverte périme le secret GitHub** du compte
  concerné.
- **Une session révoquée revient avec `cookies: []`.** Quand le serveur
  rejette le refresh token, `@supabase/ssr` ne se contente pas de répondre
  401 : il **efface les cookies d'authentification** dans la réponse.
  Vérifié le 04/09/2026 sur la session morte du compte 9 —
  `GET /api/wikibidous` → 401 puis `cookies: []`. Deux conséquences :
  `token_expires_in()` renvoie `None` (donc `ensure_fresh()` n'affiche
  aucune ligne de renouvellement — c'est la signature à reconnaître dans un
  log), et `persist()` écrivait un état vide que l'étape
  `if: always()` poussait ensuite dans le secret. La session était déjà
  perdue, mais on effaçait en prime la seule trace exploitable. `persist()`
  **refuse désormais d'écraser un fichier qui portait des cookies d'auth par
  un état qui n'en a plus**.

## Où j'en suis (03/09/2026)

**Objectif en cours : vendre automatiquement les UR/L aux enchères.** La forme
visée est un workflow qui lit les UR/L de chaque compte, estime leur valeur de
marché, en déduit une mise à prix, poste l'enchère, et repositionne le prix si
l'annonce expire sans acheteur.

Table de référence des prix (produite depuis le compte premium) :

- rareté **L** : **terminée**, 1800/1800 cartes, dont 1798 avec des ventes
  réelles. Médiane des médianes : **688,75 wb** ;
- rareté **UR** : en cours, 12 257 cartes au catalogue.

**La vente tourne en production sur les 9 comptes** (04/09/2026), 5
emplacements chacun, cycles de 6 h.

Premier bilan réel, après un cycle :

| | |
|---|---|
| Vendues | **4 cartes, 4779 wb** |
| Dont au-dessus du prix demandé | 3 sur 4 (il y a donc de vraies enchères) |
| `Lupita Nyong'o` | demandée 1210 (niveau 2, au maximum historique) → **vendue 1500** |

Ce dernier cas valide l'enchère dégressive : partir du maximum sur une
carte bimodale a rapporté plus que son propre record. Et le
repositionnement fonctionne tout seul — `Chris Hemsworth` 5000 → 2500,
`Empire ottoman` 5500 → 2750, sans état stocké localement.

Table de référence **UR terminée** : 12 245 cartes avec ventes, médiane des
médianes **80 wb** contre 688,75 pour les L, dispersion médiane 1,30 contre
0,75. Seules 13 % des UR passent le filtre de fiabilité (45 % pour les L).
C'est pourquoi **la rareté prime sur le niveau** dans le classement : sans
ça, une UR fiable à 85 wb passerait devant une L non fiable à 2750 et
stopperait sa descente.

**Trois niveaux de priorité, pas deux** (06/09/2026). Le niveau 1 est la
carte dont la moyenne est fiable, le niveau 2 l'enchère dégressive. Le
**niveau 3 est le repêchage** : les cartes que `PRIX_ABANDON` (200 wb)
écartait au motif qu'« un emplacement vaut mieux qu'une vente à 150 ». Ce
raisonnement ne tient que si un meilleur candidat attend l'emplacement — à
06:38 le compte 7 avait 5 emplacements libres et **0** candidat, le compte
8 en avait 4 pour 1. Un emplacement vide rapporte zéro, ce qui est pire que
n'importe quelle vente. Ces cartes sont donc servies en dernier, seulement
s'il reste de la place, au plancher **relatif** (`FRACTION_PLANCHER` ×
médiane) qui continue de protéger les cartes chères. Deux plafonds : on ne
demande jamais plus que le dernier prix refusé, ni plus que le maximum
jamais atteint — sans quoi une carte invendue à 210 serait remise à 300.

Effet mesuré immédiatement : **40 annonces actives sur 45 emplacements**
contre 23 au passage précédent. Les 5 manquantes sont sur les comptes
récents, qui ne possèdent pas encore assez d'UR/L — un problème de stock,
pas de stratégie.

Historique : la vente a d'abord été testée à la main (03/09/2026, deux
annonces) :

- `duration_minutes: 360` est accepté — 6 h confirmées sur `end_at` ;
- l'identifiant de possession est bien le bon : la carte mise en vente est
  celle visée ;
- `sell.yml` tourne depuis le 04/09 ; il exige que `reference/L.json` et
  `reference/UR.json` soient commités.

Trois points d'état de la machine :

- **Les sessions locales sont périmées** dès qu'un workflow est passé : les
  vraies vivent dans les secrets GitHub. Ne jamais les rejouer telles
  quelles — c'est le cas de révocation.
- **Les cinq comptes sont bien distincts** : `ululuminadeL`, `ululuminadel1`,
  `wikilover12` (collecteur), `tigrewiki`, `oursours`. Les deux premiers se
  ressemblent mais ne sont pas le même compte.
- **Incident du 04/09/2026 à ne pas reproduire** : une session de `tigrewiki`
  a été poussée dans `WM_TEST5_STORAGE_STATE` en croyant tenir le compte 5.
  La session d'`oursours` a été perdue (un secret ne se relit pas), les deux
  comptes ont tourné sous la même identité, et il a fallu reconnecter
  `oursours` pour réparer. D'où `wm_session_io.identite()` et l'option
  `--expect` : **vérifier le pseudo avant d'écrire dans un secret**.
- **Les workflows tournent normalement**, à la cadence de 50 min prévue.
- **Les comptes 2 et 3 ont été révoqués puis réparés** le 04/09 : ils étaient
  les plus exposés aux chevauchements de workflows (ceux qui bouclaient sur
  les 429). Un job qui reste vert alors qu'un compte est en 401 étant
  invisible, chaque workflow **signale désormais les comptes en échec** dans
  le résumé du run et donne la commande de réparation. Les trois workflows
  planifiés (`boosters`, `discard`, `trade`) **font en plus échouer le job**,
  parce qu'ils tournent sans personne devant et n'ont que ça pour notifier.
  `report-rares` non : il est en déclenchement manuel, on est déjà devant
  l'écran, et le rougir pendant un incident qu'on connaît déjà n'apprend qu'à
  ignorer le rouge. Il a en revanche un contrôle que les autres n'ont pas —
  la liste des comptes **sans fragment**. Un 401 est toujours visible (le
  script l'attrape et l'écrit dans son fragment) ; c'est le plantage
  *précoce*, avant l'écriture, qui passait sous le radar, `merge_fragments`
  n'ayant aucune liste des comptes attendus.
- **Résolu : les 429 des comptes 2 et 3 étaient la limite quotidienne.**
  `--count` est passé de 30 à 10 le 04/09. Attention toutefois : la
  justification d'alors — « ouvrir en rafale consomme le quota en
  quelques heures puis bloque douze heures, soit ~70 paquets perdus par
  compte et par jour » — **est fausse**, et la mesure du 06/09 la réfute
  (voir « Le plafond d'ouverture » plus haut). On ne peut pas consommer
  le quota en rafale, puisqu'il vaut exactement la régénération et que le
  stock plafonne à 10. Baisser `--count` n'a rien changé au débit ; ça a
  seulement arrêté de gaspiller des appels en 429.
- **Le quota Actions n'est plus une contrainte : le dépôt est public**, donc
  les minutes sont gratuites et illimitées. L'arithmétique reste bonne à
  connaître si le dépôt redevenait privé — à 50 min de cadence, la défausse
  coûte ~98 min/jour et le trade ~20 min/jour, soit ~3540 min/mois hors
  boosters, contre 2000 min gratuites en dépôt privé. Le dépassement serait
  structurel.
- Dépôt public et secrets : sans danger ici parce qu'**aucun workflow ne se
  déclenche sur `pull_request`**. Un workflow déclenché par une PR venue d'un
  fork exposerait `GH_ACTIONS_PAT` et les sessions. À ne pas oublier en
  ajoutant un futur workflow.

Tout est commité et poussé sur `main`.
`wm_session_premium.py`, `wm_sell.py`, `wm_sell_auto.py`,
`wm_reference_build.py`, `wm_session_window.py`, `reference/L.json` et
`.github/workflows/sell.yml`.

## Règles à garder

- **`storage_state*.json` vaut un mot de passe.** Couvert par le `.gitignore`,
  ne sort pas du dossier.
- **Ne jamais écraser `storage_state.json`** (compte de test 1) avec la session
  d'un autre compte : ça casse GitHub Actions.
- **Le mot de passe du compte principal ne passe jamais par un script.**
  Principe posé au début, toujours valable. Les comptes de test sont
  l'exception explicite (jetables, conséquence d'une fuite nulle).
- **`DELAY` : 2 secondes, en séquentiel, sans parallélisme.** Le risque n'est
  pas juridique, c'est de perdre le compte. **Exception assumée** : le scrape
  de référence tourne à 0,2 s sur décision explicite du 03/09/2026 — lecture
  ponctuelle et bornée, pas une activité de fond. `MIN_DELAY` refuse en dessous.
- **Le compte premium sert au scrape de référence, à rien d'autre.** Les essais
  d'action (vente, défausse, échange) passent par un compte de test — règle
  posée le 03/09/2026. `wm_sell.py` refuse `--go` sur la session premium.
- **Un seul processus à la fois par session.** Deux scripts qui font tourner le
  même jeton en parallèle, c'est la révocation assurée : à surveiller quand
  plusieurs agents travaillent sur le projet en même temps.
- **Une vraie connexion peut invalider la session déjà en cours** du compte,
  y compris celle du secret GitHub. Utiliser `wm_session_repair.py`, qui
  reconnecte, vérifie l'identité et repousse le secret dans la foulée.
- **La reconnexion ne peut pas tourner sur un runner.** Cloudflare Turnstile
  refuse une IP de datacenter en Chrome headless — c'est pourquoi
  `refresh-sessions.yml` a été supprimé le 03/09 (commit `423102a`). La même
  connexion passe en local dans un vrai Chrome (vérifié le 04/09). Corollaire
  utile : **les identifiants n'ont rien à faire dans les secrets GitHub**,
  ils restent en local.
- **Sur un 429, ralentir plutôt qu'insister** (pause de 60 s).
- **Jamais deux comptes en parallèle**, et 5–10 s entre deux ouvertures.
- **Le rythme n'est jamais parfaitement régulier** : jitter en tête de run,
  ±20 % sur les boucles locales. Un cron au métronome est la signature la plus
  facile à repérer.
- **La console Windows est en cp1252** : afficher un titre de carte contenant
  un caractère hors de cette table lève un `UnicodeEncodeError` qui **tue le
  script en plein scrape**, sans rien écrire dans le journal (plusieurs
  lancements morts silencieusement le 03/09/2026). Tout script à sortie longue
  force `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`.
- Sous Windows, le `os.chmod` de `wm_session.py` n'agit que sur l'attribut
  lecture seule : ce n'est pas un vrai 600.

## Piste alternative

Les cartes viennent de Wikipédia. Si le besoin porte sur le catalogue global
plutôt que sur mes comptes, l'API MediaWiki et les dumps Wikimedia sont
ouverts, documentés et stables.
