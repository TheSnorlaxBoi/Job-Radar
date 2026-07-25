# Job Radar

Automatise la recherche d'offres d'emploi sur **France Travail** et **LinkedIn**,
selon des filtres précis, et affiche le résultat dans un petit tableau de bord web.
100 % gratuit : GitHub Actions (planification) + GitHub Pages (dashboard).

## Comment ça marche

```
GitHub Actions (cron, toutes les 4h)
        │
        ▼
  fetch_jobs.py  ──►  France Travail API (officielle, gratuite)
        │        ──►  LinkedIn (page de recherche publique, sans login)
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

### 3. LinkedIn
Aucune clé nécessaire : le script interroge la page de recherche publique.
⚠️ Il n'existe pas d'API gratuite officielle pour la recherche d'offres LinkedIn
côté particulier — ce module reste donc plus fragile (structure HTML pouvant
changer, ou requêtes bloquées si trop fréquentes). Le workflow est réglé sur un
rythme modéré (toutes les 4h, 2 pages max) pour rester raisonnable. Si LinkedIn
bloque les requêtes, désactive simplement `"linkedin": {"enabled": false}`
dans `config.json` et garde France Travail, qui est robuste et illimité.

### 4. Adapter tes filtres
Modifie `config.json` :
- `keywords` : mots-clés du poste recherché
- `commune` (France Travail) : code INSEE ou code postal
- `contract_types` : `CDI`, `CDD`, `MIS` (intérim), etc.
- `remote_only` : `true`/`false`
- `exclude_keywords` : mots à bannir (ex: "stage", "alternance")

### 5. Activer GitHub Pages
**Settings → Pages** → Source : `Deploy from a branch` → Branch : `main` / `/(root)`.
Ton tableau de bord sera accessible à :
`https://<ton-utilisateur>.github.io/<nom-du-repo>/dashboard/`

### 6. Lancer une première collecte
Onglet **Actions** → workflow "Collecte des offres d'emploi" → **Run workflow**.
Après quelques dizaines de secondes, `data/jobs.json` se remplit et le
dashboard affiche les résultats.

## Aller plus loin
- Ajouter une notification (email/Telegram) sur nouvelle offre : facile à
  greffer dans `fetch_jobs.py` (variable `fresh` dans `main()`), en appelant
  l'API Telegram Bot (gratuite) ou un webhook.
- Ajouter d'autres jobboards disposant d'une API/RSS publique (Adzuna,
  Indeed via son flux XML pour éditeurs, Welcome to the Jungle...).
- Stocker l'historique dans une base gratuite (Supabase, Neon) si tu préfères
  ne pas committer `data/jobs.json` dans git.

## Limites à connaître
- **LinkedIn** : le scraping de pages publiques est une zone grise vis-à-vis
  de leurs conditions d'utilisation. Usage strictement personnel, volume
  raisonnable, pas de revente de données — c'est ce que fait ce script, mais
  la prudence reste de mise (possible blocage d'IP après usage intensif).
- **France Travail** : usage réglementé par leurs CGU API (gratuit, mais
  quotas de requêtes — largement suffisants pour un usage personnel).
