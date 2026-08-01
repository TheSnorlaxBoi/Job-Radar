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
        │        ──►  HelloWork (données structurées)       [France]
        │        ──►  Indeed (données structurées)           [France + Luxembourg]
        │        ──►  LinkedIn (page publique)              [multi-pays]
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

### 3. LinkedIn, HelloWork et Indeed (France)
Aucune clé nécessaire pour aucune des trois.
⚠️ Il n'existe pas d'API gratuite officielle pour la recherche d'offres côté
particulier sur ces sites — ces modules restent donc plus fragiles (structure
HTML/JSON-LD pouvant changer, ou requêtes bloquées si trop fréquentes) :
- **HelloWork** classe ses offres par mot-clé dans l'URL
  (`/emploi/mot-cle_<slug>.html`) — un mot-clé trop spécifique ou mal
  orthographié peut renvoyer 0 résultat.
- **Indeed** (France et Luxembourg) : **désactivé par défaut** dans
  `config.json` (`"enabled": false`). Les tests réels montrent qu'Indeed
  bloque systématiquement ces requêtes (HTTP 403) — c'est une mesure
  anti-bot volontaire de leur part, pas un bug du script, et il n'existe pas
  de moyen propre de la contourner pour un usage personnel. Tu peux réactiver
  la source si tu veux retenter, mais ne t'attends pas à des résultats
  fiables.
- **HelloWork** : URL de recherche corrigée
  (`/emploi/recherche.html?k=<mot-clé>`) après un premier essai raté sur de
  mauvaises pages.
- **VDAB** : URL de recherche corrigée (`/vindeenjob/vacatures?woord=<mot-clé>`)
  pour la même raison.
- **Actiris, JobsWallonie, Moovijob, Talent.com** : si elles renvoient encore
  0 résultat, les logs affichent maintenant un extrait de la page reçue et
  sa taille — utile pour distinguer un vrai blocage (page très courte,
  redirection) d'un site nécessitant JavaScript (auquel cas ce type de
  script ne peut pas les récupérer sans changer d'approche, ex: navigateur
  headless).

Le workflow reste à un rythme modéré (toutes les 4h, 1-2 pages max) pour ces
trois sources. Si l'une d'elles bloque les requêtes, désactive-la dans
`config.json` et garde France Travail, qui est robuste et illimité.

### 4. Belgique et Luxembourg
Aucune clé nécessaire pour aucune de ces sources, mais elles ne fonctionnent
pas toutes de la même façon :

- **Forem** (Wallonie) : vraie API open data officielle et gratuite
  (plateforme OpenDataSoft), aucune inscription requise. Recherche libre par
  mot-clé (`q`). Son jeu de données inclut aussi une partie des offres VDAB
  traduites en français — un bon complément côté flamand.
- **JobsWallonie** (Wallonie) : jobboard commercial spécifiquement centré sur
  la Wallonie, lu via ses données structurées (recherche par mot-clé `q`,
  best-effort comme les sources ci-dessous).
- **VDAB** (Flandre) : pas d'API grand public — le script utilise une
  recherche libre, comme France Travail (URL
  `vdab.be/vindeenjob/jobs/<mot-clé-en-slug>`, une vraie page de recherche du
  site, générée côté serveur).
- **Actiris** (Bruxelles) : lu via les données structurées schema.org
  "JobPosting" de ses pages de recherche (`?keywords=...`).
- **Moovijob** (Luxembourg) : le site classe ses offres par **catégories
  fixes** dans l'URL (`profession-developer`, `profession-java-developer`,
  `profession-web-developer`...), pas par recherche libre. Un mot-clé qui ne
  correspond pas exactement au nom d'une catégorie existante renverra 0
  résultat (log : "page introuvable (404)"). Pour trouver les bons mots-clés :
  va sur https://en.moovijob.com, filtre par métier, et reprends le mot après
  `profession-` dans l'URL obtenue.
- **Indeed Luxembourg** : deuxième source pour le Luxembourg (recherche libre
  par mot-clé), avec les mêmes réserves anti-bot qu'Indeed France ci-dessus.
  Aucune autre source publique équivalente n'a été trouvée pour le
  Luxembourg (l'open data de l'ADEM ne contient que des statistiques
  agrégées, pas les offres individuelles) — si Indeed Luxembourg et Moovijob
  bloquent tous les deux, il ne reste que ces deux options pour ce pays.
- **Talent.com** (Belgique et Luxembourg) : gros agrégateur international,
  recherche libre par mot-clé (`k`), lu via ses données structurées.

Ces sources (sauf Forem) restent dépendantes du format HTML/structuré du site
plutôt que d'une API stable garantie dans le temps :
- Si un run renvoie 0 offre pour l'une d'elles, regarde les logs : le message
  précise la cause (mot-clé/catégorie sans résultat, page introuvable, ou
  changement de format du site).
- ⚠️ Le mot-clé partagé dans le panneau de filtres du dashboard convient à la
  recherche libre (France Travail, HelloWork, Indeed, LinkedIn, Forem,
  JobsWallonie, VDAB, Actiris, Talent.com), mais pas à Moovijob (catégories
  fixes) — inclus-y aussi le nom exact d'une catégorie Moovijob si tu veux
  des résultats de cette source.

### 5. Adapter tes filtres
Modifie `config.json` (ou le panneau ⚙️ Filtres du dashboard) :
- `keywords_list` : mots-clés du poste recherché (un ou plusieurs, par source)
- `contract_types` : `CDI`, `CDD`, `MIS` (intérim), etc. (France Travail)
- `remote_only` : `true`/`false`
- `commune` / `rayon_km` (France Travail, optionnel) : code INSEE ou code
  postal + rayon en km pour restreindre à une zone précise. Laisser `commune`
  vide garde une recherche nationale (comportement par défaut).
- `exclude_keywords` : mots à bannir (ex: "stage", "alternance")
- `max_job_age_days` : purge automatique des offres au-delà de cette
  ancienneté (7 par défaut)

### 6. Activer GitHub Pages
**Settings → Pages** → Source : `Deploy from a branch` → Branch : `main` / `/(root)`.
Ton tableau de bord sera accessible à :
`https://<ton-utilisateur>.github.io/<nom-du-repo>/dashboard/`

### 7. Lancer une première collecte
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
- **LinkedIn, HelloWork, Indeed, VDAB, Actiris, JobsWallonie, Moovijob,
  Talent.com** : le scraping de pages publiques est une zone grise vis-à-vis
  de leurs conditions d'utilisation. Usage strictement personnel, volume
  raisonnable, pas de revente de données — c'est ce que fait ce script, mais
  la prudence reste de mise (possible blocage d'IP après usage intensif,
  Indeed étant la source la plus susceptible de bloquer).
- **France Travail et Forem** : usage réglementé par leurs CGU API
  respectives (gratuit, mais quotas de requêtes — largement suffisants pour
  un usage personnel).
