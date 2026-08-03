# Job Radar

Automatise la recherche d'offres d'emploi en France, Belgique et au
Luxembourg, selon des filtres précis, et affiche le résultat dans un petit
tableau de bord web. 100 % gratuit : GitHub Actions (planification) +
GitHub Pages (dashboard).

## Comment ça marche

```
GitHub Actions (cron, toutes les 4h)
        │
        ▼
  fetch_jobs.py  ──►  France Travail (API officielle)      [France]
        │        ──►  Jooble (API officielle)               [France]
        │        ──►  APEC (données structurées)             [France]
        │        ──►  HelloWork (données structurées)        [France]
        │        ──►  LinkedIn (page publique)               [multi-pays]
        │        ──►  Forem (API open data officielle)      [Wallonie]
        │        ──►  JobsWallonie (données structurées)     [Wallonie]
        │        ──►  VDAB (recherche publique)             [Flandre/Belgique]
        │        ──►  Actiris (données structurées)          [Bruxelles]
        │        ──►  Moovijob (données structurées)         [Luxembourg]
        │        ──►  Talent.com (données structurées)       [BE + LU]
        ▼
  data/jobs.json  (dédupliqué, historisé, committé dans le repo)
        │
        ▼
  dashboard/index.html  (lu tel quel via GitHub Pages)
```

## Mise en place (10-15 minutes)

### 1. Créer le dépôt GitHub
Crée un nouveau dépôt (public ou privé) et pousse-y ces fichiers.

### 2. Obtenir des identifiants France Travail (gratuit)
1. Va sur https://francetravail.io, crée un compte.
2. Crée une "application", active l'API **"Offres d'emploi v2"**.
3. Note ton `client_id` et `client_secret`.
4. Dans le dépôt GitHub : **Settings → Secrets and variables → Actions**, ajoute :
   - `FT_CLIENT_ID`
   - `FT_CLIENT_SECRET`

### 3. Obtenir une clé Jooble (gratuit)
Jooble est un agrégateur d'offres présent dans 69 pays (dont la France), avec
une vraie API gratuite — contrairement à Indeed, elle est faite pour être
appelée automatiquement et ne bloque donc rien.
1. Va sur https://jooble.org/api/about, remplis le formulaire (nom, email,
   site web).
2. Une clé est générée immédiatement, aucune carte bancaire requise.
3. Ajoute-la en secret GitHub : `JOOBLE_API_KEY`.

Si tu ne renseignes pas cette clé, le script l'indique simplement dans les
logs et continue sans cette source (comme pour France Travail).

### 4. LinkedIn, HelloWork, APEC (France) — sans clé
Aucune clé nécessaire pour ces trois-là.
⚠️ Il n'existe pas d'API gratuite officielle pour la recherche d'offres côté
particulier sur ces sites — ces modules restent donc plus fragiles (structure
HTML/JSON-LD pouvant changer, ou requêtes bloquées si trop fréquentes) que
France Travail et Jooble :
- **HelloWork** : recherche libre par mot-clé
  (`/emploi/recherche.html?k=<mot-clé>`).
- **APEC** : jobboard de référence pour les postes cadres en France,
  recherche libre par mot-clé (`motsCles`) — best-effort comme HelloWork.
- **LinkedIn** : page de recherche publique "guest", sans login.

Le workflow reste à un rythme modéré (toutes les 4h, 1-2 pages max) pour ces
trois sources. Si l'une d'elles bloque les requêtes, désactive-la dans
`config.json` et garde France Travail + Jooble, qui sont robustes.

**Indeed a été retiré du projet** : les tests réels montraient un blocage
systématique (HTTP 403) — c'est une mesure anti-bot volontaire de leur part,
sans moyen propre de la contourner pour un usage personnel. Jooble et APEC
le remplacent.

### 5. Belgique et Luxembourg
Aucune clé nécessaire pour aucune de ces sources, mais elles ne fonctionnent
pas toutes de la même façon :

- **Forem** (Wallonie) : vraie API open data officielle et gratuite
  (plateforme OpenDataSoft), aucune inscription requise. Recherche libre par
  mot-clé (`q`). Son jeu de données inclut aussi une partie des offres VDAB
  traduites en français — un bon complément côté flamand.
- **JobsWallonie** (Wallonie) : jobboard commercial spécifiquement centré sur
  la Wallonie, lu via ses données structurées (recherche par mot-clé `q`,
  best-effort comme les sources ci-dessous).
- **VDAB** (Flandre) : pas d'API grand public — recherche libre par mot-clé
  (`/vindeenjob/vacatures?woord=<mot-clé>`, une vraie page de recherche du
  site, générée côté serveur).
- **Actiris** (Bruxelles) : lu via les données structurées schema.org
  "JobPosting" de ses pages de recherche (`?keywords=...`).
- **Moovijob** (Luxembourg) : le site classe ses offres par **catégories
  fixes** dans l'URL (`profession-developer`, `profession-java-developer`,
  `profession-web-developer`...), pas par recherche libre. Un mot-clé qui ne
  correspond pas exactement au nom d'une catégorie existante renverra 0
  résultat. Pour trouver les bons mots-clés : va sur
  https://en.moovijob.com, filtre par métier, et reprends le mot après
  `profession-` dans l'URL obtenue.
- **Talent.com** (Belgique et Luxembourg) : gros agrégateur international,
  recherche libre par mot-clé (`k`), lu via ses données structurées.

Ces sources (sauf Forem) restent dépendantes du format HTML/structuré du site
plutôt que d'une API stable garantie dans le temps :
- Si un run renvoie 0 offre pour l'une d'elles, regarde les logs : le message
  précise la cause (mot-clé/catégorie sans résultat, ou changement de format
  du site — avec un extrait de la page reçue pour diagnostiquer).
- ⚠️ Le mot-clé partagé dans le panneau de filtres du dashboard convient à la
  recherche libre (France Travail, Jooble, HelloWork, APEC, LinkedIn, Forem,
  JobsWallonie, VDAB, Actiris, Talent.com), mais pas à Moovijob (catégories
  fixes) — inclus-y aussi le nom exact d'une catégorie Moovijob si tu veux
  des résultats de cette source.

### 6. Adapter tes filtres
Modifie `config.json` (ou le panneau ⚙️ Filtres du dashboard) :
- `keywords_list` : mots-clés du poste recherché (un ou plusieurs, par source)
- `strict_keyword_match` (`true` par défaut) : n'garde que les offres dont le
  **titre** contient réellement un des mots-clés demandés. Beaucoup de
  jobboards élargissent leur propre recherche interne (fautes de frappe,
  synonymes, mots isolés de la phrase...), ce qui remontait des offres sans
  rapport — ce filtre les retire après-coup. Désactive-le si tu préfères une
  recherche plus large.
- `contract_types` : `CDI`, `CDD`, `MIS` (intérim), etc. (France Travail)
- `remote_only` : `true`/`false`
- `commune` / `rayon_km` (France Travail, optionnel) : code INSEE ou code
  postal + rayon en km pour restreindre à une zone précise. Laisser `commune`
  vide garde une recherche nationale (comportement par défaut).
- `exclude_keywords` : mots à bannir (ex: "stage", "alternance")
- `max_job_age_days` : purge automatique des offres au-delà de cette
  ancienneté (7 par défaut)
- `experience_filter.enabled` : si `true`, exclut les offres où une
  expérience est détectée comme requise. France Travail fournit un champ
  officiel fiable pour ça ; les autres sources utilisent une détection par
  mots-clés dans le titre (et la description quand la source la fournit —
  Jooble, APEC, Forem, Actiris, JobsWallonie, Moovijob, Talent.com). **VDAB
  et LinkedIn ne fournissent pas de description**, donc la détection s'y
  limite au titre et ratera beaucoup de mentions d'expérience situées dans
  le corps de l'annonce — à activer en connaissance de cause.
  `keep_if_not_mentioned` (`true` par défaut) garde les offres qui ne
  mentionnent l'expérience nulle part plutôt que de les exclure par excès de
  prudence.

### 7. Activer GitHub Pages
**Settings → Pages** → Source : `Deploy from a branch` → Branch : `main` / `/(root)`.
Ton tableau de bord sera accessible à :
`https://<ton-utilisateur>.github.io/<nom-du-repo>/dashboard/`

### 8. Lancer une première collecte
Onglet **Actions** → workflow "Collecte des offres d'emploi" → **Run workflow**.
Après quelques dizaines de secondes, `data/jobs.json` se remplit et le
dashboard affiche les résultats.

## Aller plus loin
- Ajouter une notification (email/Telegram) sur nouvelle offre : facile à
  greffer dans `fetch_jobs.py` (variable `fresh` dans `main()`), en appelant
  l'API Telegram Bot (gratuite) ou un webhook.
- Stocker l'historique dans une base gratuite (Supabase, Neon) si tu préfères
  ne pas committer `data/jobs.json` dans git.

## Limites à connaître
- **LinkedIn, HelloWork, APEC, VDAB, Actiris, JobsWallonie, Moovijob,
  Talent.com** : le scraping de pages publiques est une zone grise vis-à-vis
  de leurs conditions d'utilisation. Usage strictement personnel, volume
  raisonnable, pas de revente de données — c'est ce que fait ce script, mais
  la prudence reste de mise (possible blocage d'IP après usage intensif).
- **France Travail, Forem et Jooble** : usage réglementé par leurs CGU API
  respectives (gratuit, mais quotas de requêtes — largement suffisants pour
  un usage personnel).
- **Indeed** a été délibérément retiré : ses mesures anti-bot bloquent
  systématiquement ce type de requête, sans contournement propre possible.
