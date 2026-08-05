# Job Radar

Automatise la recherche d'offres d'emploi en France et en Wallonie (Belgique),
selon des filtres précis, et affiche le résultat dans un petit tableau de
bord web. 100 % gratuit : GitHub Actions (planification) + GitHub Pages
(dashboard).

## Comment ça marche

```
GitHub Actions (cron, toutes les 4h)
        │
        ▼
  fetch_jobs.py  ──►  France Travail (API officielle)      [France]
        │        ──►  Jooble (API officielle)               [France]
        │        ──►  LinkedIn (page publique)               [multi-pays]
        │        ──►  Forem (API open data officielle)      [Wallonie]
        ▼
  data/jobs.json  (dédupliqué, historisé, committé dans le repo)
        │
        ▼
  dashboard/index.html  (lu tel quel via GitHub Pages)
```

Quatre sources, toutes fiables : trois vraies API officielles (France
Travail, Jooble, Forem) et une page de recherche publique qui fonctionne de
façon stable (LinkedIn).

## Sources testées puis retirées

Plusieurs autres jobboards ont été essayés (VDAB, Actiris, JobsWallonie,
Moovijob, Talent.com, HelloWork, APEC, Indeed) mais retirés après vérification
en conditions réelles, pour deux raisons possibles :
- **Protection anti-bot active** (Indeed, APEC) : les requêtes sont
  bloquées (HTTP 403, ou détection Datadome), sans contournement propre
  possible pour un usage personnel.
- **Rendu par JavaScript côté client** (VDAB, Actiris, JobsWallonie,
  Moovijob, Talent.com, HelloWork) : la page reçue est complète mais ne
  contient aucune donnée d'offre exploitable — le contenu réel n'apparaît
  qu'après exécution de JavaScript dans un navigateur, ce qu'une simple
  requête HTTP ne peut pas déclencher. Vérifié en cherchant un titre
  d'offre affiché à l'écran directement dans le code source de la page :
  absent dans tous ces cas.

Si tu veux un jour ajouter une de ces sources (ou une autre), la seule
façon fiable de contourner ce blocage est un navigateur headless
(Playwright/Selenium) — une approche plus lourde, plus lente, et souvent
détectée par les mêmes systèmes anti-bot. Pas ajouté ici pour rester simple
et gratuit à faire tourner sur GitHub Actions.

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
une vraie API gratuite — conçue pour être appelée automatiquement, elle ne
bloque donc rien.
1. Va sur https://jooble.org/api/about, remplis le formulaire (nom, email,
   site web).
2. Une clé est générée immédiatement, aucune carte bancaire requise.
3. Ajoute-la en secret GitHub : `JOOBLE_API_KEY`.

Si tu ne renseignes pas cette clé, le script l'indique dans les logs et
continue sans cette source (comme pour France Travail).

### 4. LinkedIn — sans clé
Aucune clé nécessaire. Pas d'API gratuite officielle pour la recherche
d'offres LinkedIn côté particulier — ce module reste donc plus fragile
(structure HTML pouvant changer, ou requêtes bloquées si trop fréquentes)
que France Travail, Jooble et Forem. Le workflow reste à un rythme modéré
(toutes les 4h, 2 pages max). S'il bloque les requêtes, désactive-le dans
`config.json` et garde les trois autres sources.

### 5. Forem (Wallonie) — sans clé
Vraie API open data officielle et gratuite (plateforme OpenDataSoft), aucune
inscription requise. Recherche libre par mot-clé.

### 6. Adapter tes filtres
Modifie `config.json` (ou le panneau ⚙️ Filtres du dashboard) :
- `keywords_list` : mots-clés du poste recherché (un ou plusieurs, par source)
- `strict_keyword_match` (`true` par défaut) : ne garde que les offres dont
  le **titre** contient réellement un des mots-clés demandés. Beaucoup de
  jobboards élargissent leur propre recherche interne (fautes de frappe,
  synonymes, mots isolés de la phrase...), ce qui remontait des offres sans
  rapport — ce filtre les retire après-coup. Désactive-le si tu préfères une
  recherche plus large.
- `contract_types` : `CDI`, `CDD`, `MIS` (intérim), etc. (France Travail)
- `remote_only` : `true`/`false`
- `commune` / `rayon_km` (optionnel, s'applique à toutes les sources actives) :
  restreint la recherche à une zone précise au lieu du national par défaut.
  - **France Travail** : utilise `commune` comme code INSEE/postal et
    `rayon_km` comme vrai rayon géographique — le plus précis des quatre.
  - **Jooble** : utilise `commune` comme texte de localisation et `rayon_km`
    comme rayon (paramètre documenté par leur API).
  - **Forem** : utilise `commune` comme recherche textuelle sur la localité
    de l'offre — pas de notion de rayon dans ce jeu de données.
  - **LinkedIn** : utilise `commune` comme texte de localisation — pas de
    rayon disponible sur cet endpoint public.

  Un **code postal** (ex: `69003`) est le meilleur compromis : il fonctionne
  correctement pour France Travail (qui veut un code) et reste compréhensible
  comme texte pour les trois autres. Un **nom de ville** (ex: `Lyon`)
  fonctionne mieux pour LinkedIn/Jooble/Forem mais n'est pas interprété par
  France Travail, qui a besoin d'un code. Laisser `commune` vide garde une
  recherche nationale sur toutes les sources (comportement par défaut).
- `exclude_keywords` : mots à bannir (ex: "stage", "alternance")
- `max_job_age_days` : purge automatique des offres au-delà de cette
  ancienneté (7 par défaut), modifiable directement depuis le panneau
  ⚙️ Filtres du dashboard.
- `experience_filter.enabled` : si `true`, exclut les offres où une
  expérience est détectée comme requise. France Travail et Forem fournissent
  chacun un champ officiel fiable pour ça ; LinkedIn n'a pas de description
  disponible, donc la détection s'y limite au titre — à activer en
  connaissance de cause. `keep_if_not_mentioned` (`true` par défaut) garde
  les offres qui ne mentionnent l'expérience nulle part plutôt que de les
  exclure par excès de prudence.

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
- **LinkedIn** : le scraping d'une page publique est une zone grise
  vis-à-vis des conditions d'utilisation. Usage strictement personnel,
  volume raisonnable, pas de revente de données — c'est ce que fait ce
  script, mais la prudence reste de mise (possible blocage d'IP après usage
  intensif).
- **France Travail, Forem et Jooble** : usage réglementé par leurs CGU API
  respectives (gratuit, mais quotas de requêtes — largement suffisants pour
  un usage personnel).
