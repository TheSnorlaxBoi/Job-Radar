"""
job-radar / fetch_jobs.py
--------------------------------
Collecte des offres d'emploi selon des filtres définis dans config.json,
depuis :
  - France Travail (API officielle, gratuite, OAuth2 client_credentials)
  - Jooble (API officielle, gratuite, avec clé)
  - Le Forem, Wallonie (API open data officielle, gratuite, sans clé)
  - VDAB, Actiris, JobsWallonie, Moovijob, Talent.com, HelloWork, APEC,
    LinkedIn (pages de recherche publiques, sans login — best-effort,
    voir les avertissements dans chaque fonction et le README)

Le résultat est fusionné, dédupliqué (par rapport aux offres déjà vues) et
écrit dans data/jobs.json, qui alimente le tableau de bord (dashboard/index.html).

Ce script est conçu pour tourner via GitHub Actions (cron), voir
.github/workflows/scrape.yml.
"""

import json
import os
import re
import sys
import time
import html
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).parent
DATA_FILE = ROOT / "data" / "jobs.json"
CONFIG_FILE = ROOT / "config.json"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# France Travail (ex Pôle Emploi) — API officielle gratuite
# Inscription : https://francetravail.io  (créer une appli, activer
# "Offres d'emploi v2", récupérer client_id / client_secret)
# ---------------------------------------------------------------------------

FT_TOKEN_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=/partenaire"
FT_SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"


def get_france_travail_token(client_id, client_secret, scope):
    resp = requests.post(
        FT_TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"{resp.status_code} {resp.reason} — réponse de l'API : {resp.text[:500]}"
        )
    return resp.json()["access_token"]


def fetch_france_travail(cfg):
    ft_cfg = cfg.get("france_travail", {})
    if not ft_cfg.get("enabled"):
        return []

    client_id = os.environ.get("FT_CLIENT_ID")
    client_secret = os.environ.get("FT_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("[France Travail] Identifiants manquants (FT_CLIENT_ID / FT_CLIENT_SECRET) — étape ignorée.")
        return []

    scope = ft_cfg.get("scope", "api_offresdemploiv2 o2dsoffre")
    try:
        token = get_france_travail_token(client_id, client_secret, scope)
    except Exception as e:
        print(f"[France Travail] Échec d'authentification : {e}")
        return []

    # Un ou plusieurs mots-clés — rétrocompatible avec l'ancienne clé "keywords" (str)
    keywords_list = ft_cfg.get("keywords_list")
    if not keywords_list:
        keywords_list = [ft_cfg["keywords"]] if ft_cfg.get("keywords") else [""]

    base_params = {}
    if ft_cfg.get("contract_types"):
        # ex: CDI, CDD, MIS, SAI...
        base_params["typeContrat"] = ",".join(ft_cfg["contract_types"])
    if ft_cfg.get("remote_only"):
        base_params["teletravail"] = "1"
    if ft_cfg.get("min_salary"):
        base_params["salaireMin"] = ft_cfg["min_salary"]
    if ft_cfg.get("commune"):
        base_params["commune"] = ft_cfg["commune"]
    if ft_cfg.get("rayon_km"):
        base_params["rayon"] = ft_cfg["rayon_km"]
    base_params["sort"] = 1  # tri par date de création décroissante
    # Si "commune" est vide/absent, la recherche reste nationale.

    headers = {"Authorization": f"Bearer {token}"}
    jobs = []
    seen_ids = set()
    max_results = ft_cfg.get("max_results", 150)
    page_size = 150

    for keyword in keywords_list:
        params = dict(base_params)
        if keyword:
            params["motsCles"] = keyword

        range_start = 0
        while range_start < max_results:
            range_end = min(range_start + page_size - 1, max_results - 1)
            r = requests.get(
                f"{FT_SEARCH_URL}?range={range_start}-{range_end}",
                params=params,
                headers=headers,
                timeout=30,
            )
            if r.status_code not in (200, 206):
                print(f"[France Travail] ('{keyword}') Erreur HTTP {r.status_code} : {r.text[:300]}")
                break
            payload = r.json()
            results = payload.get("resultats", [])
            if not results:
                break

            for o in results:
                job_id = f"ft-{o.get('id')}"
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)
                jobs.append({
                    "id": job_id,
                    "source": "France Travail",
                    "title": o.get("intitule"),
                    "company": (o.get("entreprise") or {}).get("nom", "Non précisé"),
                    "location": (o.get("lieuTravail") or {}).get("libelle", ""),
                    "contract": o.get("typeContratLibelle", ""),
                    "remote": bool((o.get("teletravail") or "")),
                    "salary": (o.get("salaire") or {}).get("libelle", ""),
                    "published_at": o.get("dateCreation", ""),
                    "url": o.get("origineOffre", {}).get("urlOrigine")
                        or f"https://candidat.francetravail.fr/offres/recherche/detail/{o.get('id')}",
                    "description": o.get("description", ""),
                    # France Travail donne un champ structuré pour l'expérience :
                    # "D" = débutant accepté, "S" = souhaitée, "E" = exigée.
                    # Plus fiable qu'une détection par mots-clés — utilisé en priorité.
                    "experience_code": o.get("experienceExige"),
                })

            if len(results) < page_size:
                break
            range_start += page_size
            time.sleep(0.3)  # respect du rate-limit

    print(f"[France Travail] {len(jobs)} offres récupérées (national, {len(keywords_list)} mot(s)-clé(s)).")
    return jobs


# ---------------------------------------------------------------------------
# LinkedIn — page publique "guest" de recherche d'offres (sans authentification)
#
# ⚠️ Il n'existe pas d'API LinkedIn gratuite pour la recherche d'offres côté
# particulier. Ce module interroge l'endpoint HTML public utilisé par la page
# de recherche non connectée (linkedin.com/jobs). Il ne contourne aucune
# mesure de sécurité ni authentification, mais reste soumis aux conditions
# d'utilisation de LinkedIn : à utiliser avec modération (peu de requêtes,
# espacées) pour un usage strictement personnel. En cas de blocage/429,
# réduis la fréquence dans le workflow GitHub Actions.
# ---------------------------------------------------------------------------

LI_SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

import unicodedata


def _slugify(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text


# ---------------------------------------------------------------------------
# Le Forem (Wallonie) — vraie API open data gratuite, aucune clé nécessaire.
# Plateforme OpenDataSoft standard ; son jeu de données inclut aussi les
# offres VDAB traduites en français, ce qui comble une partie du besoin
# flamand en plus de la Wallonie/Bruxelles.
# ---------------------------------------------------------------------------

FOREM_API_URL = "https://leforem-digitalwallonia.opendatasoft.com/api/explore/v2.1/catalog/datasets/offres-d-emploi-forem/records"


def _pick_field(record, name_hints):
    """Cherche une valeur dans un enregistrement OpenDataSoft en devinant le
    bon champ par son nom (les noms exacts de colonnes ne sont pas garantis
    dans le temps, donc on reste tolérant plutôt que de viser un nom fixe)."""
    for key, val in record.items():
        if val in (None, ""):
            continue
        lk = key.lower()
        if any(h in lk for h in name_hints):
            return val
    return None


def fetch_forem(cfg):
    forem_cfg = cfg.get("forem", {})
    if not forem_cfg.get("enabled"):
        return []

    keywords_list = forem_cfg.get("keywords_list") or [""]
    max_results = min(forem_cfg.get("max_results", 100), 100)  # 100 = max par page côté API

    jobs = []
    seen_ids = set()
    printed_schema_hint = False

    for keyword in keywords_list:
        params = {"limit": max_results, "offset": 0}
        if keyword:
            params["q"] = keyword

        try:
            r = requests.get(FOREM_API_URL, params=params, timeout=20)
        except requests.RequestException as e:
            print(f"[Forem] Erreur réseau : {e}")
            continue
        if r.status_code != 200:
            print(f"[Forem] ('{keyword}') HTTP {r.status_code} : {r.text[:300]}")
            continue

        results = r.json().get("results", [])
        if results and not printed_schema_hint:
            print(f"[Forem] Champs disponibles (exemple) : {list(results[0].keys())}")
            printed_schema_hint = True

        for rec in results:
            title = _pick_field(rec, ["intitule", "titre", "poste", "fonction"]) or ""
            company = _pick_field(rec, ["entreprise", "employeur", "societe", "raison_sociale"]) or "Non précisé"
            location = _pick_field(rec, ["commune", "localite", "localisation", "lieu", "ville"]) or ""
            contract = _pick_field(rec, ["type_contrat", "contrat", "typecontrat"]) or ""
            date_pub = _pick_field(rec, ["date_publi", "date_creation", "date_diffusion", "date_debut"]) or ""
            url_offre = _pick_field(rec, ["url", "lien"]) or ""
            description = _pick_field(rec, ["description", "descriptif", "resume", "texte"]) or ""

            job_id_seed = url_offre or f"{title}-{company}-{date_pub}"
            job_id = f"forem-{abs(hash(job_id_seed))}"
            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            jobs.append({
                "id": job_id,
                "source": "Forem",
                "title": str(title),
                "company": str(company),
                "location": str(location),
                "contract": str(contract),
                "remote": False,
                "salary": "",
                "published_at": date_pub if isinstance(date_pub, str) else "",
                "url": url_offre or "https://www.leforem.be/recherche-offres/resultat-recherche-offre",
                "description": str(description),
            })

    print(f"[Forem] {len(jobs)} offre(s) récupérée(s).")
    return jobs


TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(s):
    return html.unescape(TAG_RE.sub("", s or "")).strip()


# ---------------------------------------------------------------------------
# VDAB (Belgique) — pas d'API grand public gratuite, mais les pages de
# recherche (https://www.vdab.be/vindeenjob/jobs/<mot-clé-en-slug>) sont
# entièrement générées côté serveur, ce qui permet un scraping fiable des
# liens canoniques de chaque offre (id numérique + slug dans l'URL).
# ---------------------------------------------------------------------------

VDAB_JOB_LINK_RE = re.compile(
    r'<a[^>]+href="(/vindeenjob/vacatures/(\d+)/[a-z0-9\-]+)[^"]*"[^>]*>(.*?)</a>',
    re.DOTALL,
)
VDAB_DATE_RE = re.compile(r"Online sinds\s+([\d]{1,2}\s+\w+\.?\s+\d{4})")


def fetch_vdab(cfg):
    vdab_cfg = cfg.get("vdab", {})
    if not vdab_cfg.get("enabled"):
        return []

    keywords_list = vdab_cfg.get("keywords_list") or [""]
    search_url = vdab_cfg.get("search_url", "https://www.vdab.be/vindeenjob/vacatures")
    keyword_param = vdab_cfg.get("keyword_param", "woord")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; job-radar personal bot)"}

    jobs = []
    seen_ids = set()

    for keyword in keywords_list:
        params = {keyword_param: keyword} if keyword else {}
        try:
            r = requests.get(search_url, params=params, headers=headers, timeout=20)
        except requests.RequestException as e:
            print(f"[VDAB] ('{keyword}') Erreur réseau : {e}")
            continue
        if r.status_code != 200:
            print(f"[VDAB] ('{keyword}') HTTP {r.status_code} pour {r.url}")
            continue

        found_here = 0
        for m in VDAB_JOB_LINK_RE.finditer(r.text):
            href, job_id_num, inner = m.group(1), m.group(2), m.group(3)
            job_id = f"vdab-{job_id_num}"
            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            strongs = re.findall(r"<strong[^>]*>(.*?)</strong>", inner, re.DOTALL)
            company = _strip_tags(strongs[0]) if len(strongs) >= 1 else ""
            location = _strip_tags(strongs[1]) if len(strongs) >= 2 else ""
            title = _strip_tags(inner.split("<strong", 1)[0])
            date_m = VDAB_DATE_RE.search(_strip_tags(inner))

            jobs.append({
                "id": job_id,
                "source": "VDAB",
                "title": title,
                "company": company or "Non précisé",
                "location": location,
                "contract": "",
                "remote": False,
                "salary": "",
                "published_at": date_m.group(1) if date_m else "",
                "url": f"https://www.vdab.be{href}",
            })
            found_here += 1

        if found_here == 0:
            print(f"[VDAB] ('{keyword}') aucune offre trouvée sur {r.url} "
                  f"({len(r.text)} caractères reçus) — vérifie ce mot-clé directement sur "
                  f"vdab.be, ou le format de la page a peut-être changé.")
        time.sleep(1.0)

    print(f"[VDAB] {len(jobs)} offre(s) récupérée(s).")
    return jobs


# ---------------------------------------------------------------------------
# Modules génériques pour jobboards exposant des données structurées
# schema.org "JobPosting" (utilisé pour le référencement Google for Jobs).
# C'est plus robuste qu'un scraping basé sur des classes CSS, qui changent
# plus souvent que ce format standardisé. Utilisé ici pour VDAB (Belgique)
# et Moovijob (Luxembourg), qui n'offrent pas d'API grand public gratuite.
# ---------------------------------------------------------------------------

JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def extract_jobpostings_from_html(page_html):
    postings = []
    for match in JSONLD_RE.finditer(page_html):
        raw = match.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if isinstance(data, list):
            candidates = data
        elif isinstance(data, dict) and isinstance(data.get("@graph"), list):
            candidates = data["@graph"]
        elif isinstance(data, dict):
            candidates = [data]
        else:
            candidates = []

        for item in candidates:
            if not isinstance(item, dict):
                continue
            item_type = item.get("@type")
            types = item_type if isinstance(item_type, list) else [item_type]
            if any(str(t).lower() == "jobposting" for t in types if t):
                postings.append(item)
    return postings


def _jobposting_to_job(item, source_name, fallback_url):
    title = _strip_tags(item.get("title") or "")

    org = item.get("hiringOrganization")
    company = org.get("name", "") if isinstance(org, dict) else (org or "")

    location = ""
    loc = item.get("jobLocation")
    if isinstance(loc, list) and loc:
        loc = loc[0]
    if isinstance(loc, dict):
        addr = loc.get("address")
        if isinstance(addr, dict):
            parts = [addr.get("addressLocality"), addr.get("addressRegion")]
            location = ", ".join(p for p in parts if p)

    contract = item.get("employmentType") or ""
    if isinstance(contract, list):
        contract = ", ".join(str(c) for c in contract)

    salary = ""
    sal = item.get("baseSalary")
    if isinstance(sal, dict):
        val = sal.get("value")
        if isinstance(val, dict):
            amount = val.get("value") or val.get("minValue") or ""
            unit = val.get("unitText", "")
            currency = sal.get("currency", "")
            if amount:
                salary = f"{amount} {currency}/{unit}".strip()

    published = item.get("datePosted") or ""
    url = item.get("url") or fallback_url
    job_id_seed = url or f"{title}-{company}"
    description = _strip_tags(item.get("description") or "")[:2000]

    return {
        "id": f"{source_name.lower()}-{abs(hash(job_id_seed))}",
        "source": source_name,
        "title": title,
        "company": company or "Non précisé",
        "location": location,
        "contract": contract,
        "remote": False,
        "salary": salary,
        "published_at": published,
        "url": url,
        "description": description,
    }


def _slugify(text):
    """Convertit 'Développeur Python' en 'developpeur-python', pour les
    jobboards qui utilisent des URL du type /mot-cle_<slug>.html plutôt que
    des paramètres de requête (ex: HelloWork)."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text


def fetch_generic_board(source_name, board_cfg):
    """Récupère les offres d'un jobboard via ses données structurées JobPosting.

    Deux modes d'URL possibles dans board_cfg :
    - "url_template" avec un espace réservé {slug} : un mot-clé de
      keywords_list est glissé dans l'URL (utile pour les jobboards qui
      classent leurs offres par catégories fixes plutôt que par recherche
      libre — le mot-clé doit alors correspondre exactement au nom d'une
      catégorie existante sur le site).
    - "search_url" + "keyword_param" : recherche classique en paramètre
      de requête (?motclé=...).

    ⚠️ Contrairement à France Travail (API officielle), ce module dépend de la
    présence de ce balisage sur les pages du site — s'il change de format, le
    module renverra simplement 0 résultat (avec un message dans les logs)
    plutôt que de faire planter la collecte.
    """
    if not board_cfg.get("enabled"):
        return []

    keywords_list = board_cfg.get("keywords_list") or [""]
    max_pages = board_cfg.get("max_pages", 1)
    page_param = board_cfg.get("page_param")
    url_template = board_cfg.get("url_template")
    search_url = board_cfg.get("search_url")
    keyword_param = board_cfg.get("keyword_param", "q")

    if not url_template and not search_url:
        return []

    headers = {"User-Agent": "Mozilla/5.0 (compatible; job-radar personal bot)"}
    jobs = []
    seen_ids = set()

    for keyword in keywords_list:
        base_url = url_template.format(slug=_slugify(keyword)) if url_template else search_url

        for page in range(max_pages):
            params = {}
            if not url_template and keyword:
                params[keyword_param] = keyword
            if page_param and page > 0:
                params[page_param] = page
            params.update(board_cfg.get("extra_params", {}))

            try:
                r = requests.get(base_url, params=params, headers=headers, timeout=20)
            except requests.RequestException as e:
                print(f"[{source_name}] Erreur réseau : {e}")
                break

            if r.status_code == 404:
                print(f"[{source_name}] ('{keyword}') page introuvable (404) à {base_url} — "
                      f"ce mot-clé ne correspond probablement à aucune catégorie existante sur le site.")
                break
            if r.status_code != 200:
                print(f"[{source_name}] ('{keyword}') page {page} : HTTP {r.status_code}")
                break

            postings = extract_jobpostings_from_html(r.text)
            if not postings:
                if page == 0:
                    snippet = re.sub(r"\s+", " ", r.text[:200]).strip()
                    hint = ""
                    lower_snippet = snippet.lower()
                    if len(r.text) < 2000:
                        hint = " (page très courte — probablement une redirection, un blocage, ou un mur de consentement plutôt que la vraie page)"
                    elif "enable javascript" in lower_snippet or "activer javascript" in lower_snippet:
                        hint = " (le site nécessite JavaScript pour afficher les résultats — non récupérable par ce type de requête)"
                    print(f"[{source_name}] ('{keyword}') aucune donnée JobPosting trouvée sur {base_url} "
                          f"({len(r.text)} caractères reçus){hint} — début de la page reçue : \"{snippet}\"")
                break

            for item in postings:
                job = _jobposting_to_job(item, source_name, base_url)
                if job["id"] in seen_ids:
                    continue
                seen_ids.add(job["id"])
                jobs.append(job)

            if not page_param:
                break  # pas de pagination connue pour ce jobboard
            time.sleep(1.0)

    print(f"[{source_name}] {len(jobs)} offre(s) récupérée(s).")
    return jobs


def fetch_linkedin(cfg):
    li_cfg = cfg.get("linkedin", {})
    if not li_cfg.get("enabled"):
        return []

    # Un ou plusieurs mots-clés — rétrocompatible avec l'ancienne clé "keywords" (str)
    keywords_list = li_cfg.get("keywords_list")
    if not keywords_list:
        keywords_list = [li_cfg["keywords"]] if li_cfg.get("keywords") else [""]

    location = li_cfg.get("location", "France")
    max_pages = li_cfg.get("max_pages", 2)  # 25 offres / page ; reste raisonnable
    f_tpr = li_cfg.get("published_since_seconds")  # ex: 86400 = dernières 24h

    jobs = []
    seen_ids = set()
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; job-radar personal bot)",
        "Accept-Language": "fr-FR,fr;q=0.9",
    }

    for keyword in keywords_list:
        for page in range(max_pages):
            params = {
                "keywords": keyword,
                "location": location,
                "start": page * 25,
            }
            if li_cfg.get("remote_only"):
                params["f_WT"] = "2"  # filtre "remote" LinkedIn
            if li_cfg.get("contract_types"):
                params["f_JT"] = ",".join(li_cfg["contract_types"])  # F,C,P,T...
            if f_tpr:
                params["f_TPR"] = f"r{f_tpr}"

            r = requests.get(LI_SEARCH_URL, params=params, headers=headers, timeout=20)
            if r.status_code != 200 or not r.text.strip():
                print(f"[LinkedIn] ('{keyword}') Page {page}: réponse vide ou erreur ({r.status_code}) — arrêt.")
                break

            cards = re.findall(r'<li>(.*?)</li>', r.text, re.DOTALL)
            if not cards:
                break

            for card in cards:
                title_m = re.search(r'class="base-search-card__title">(.*?)</h3>', card, re.DOTALL)
                company_m = re.search(r'class="base-search-card__subtitle">.*?>(.*?)</a>', card, re.DOTALL)
                location_m = re.search(r'class="job-search-card__location">(.*?)</span>', card, re.DOTALL)
                link_m = re.search(r'href="([^"?]+)', card)
                date_m = re.search(r'datetime="([^"]+)"', card)

                job_id = f"li-{(link_m.group(1).rstrip('/').rsplit('-',1)[-1] if link_m else len(jobs))}"
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                jobs.append({
                    "id": job_id,
                    "source": "LinkedIn",
                    "title": _strip_tags(title_m.group(1)) if title_m else "",
                    "company": _strip_tags(company_m.group(1)) if company_m else "",
                    "location": _strip_tags(location_m.group(1)) if location_m else "",
                    "contract": "",
                    "remote": bool(li_cfg.get("remote_only")),
                    "salary": "",
                    "published_at": date_m.group(1) if date_m else "",
                    "url": link_m.group(1) if link_m else "",
                })

            time.sleep(1.5)  # espacement volontaire entre les pages/mots-clés

    print(f"[LinkedIn] {len(jobs)} offres récupérées ({len(keywords_list)} mot(s)-clé(s)).")
    return jobs


# ---------------------------------------------------------------------------
# Détection du niveau d'expérience requis
#
# France Travail fournit un champ structuré fiable ("experienceExige" : D =
# débutant accepté, S = souhaitée, E = exigée), utilisé en priorité. Pour les
# autres sources, on retombe sur une détection par mots-clés dans le titre et
# la description — moins fiable, et d'autant plus faible que la source ne
# fournit pas de description (VDAB, LinkedIn, Moovijob actuellement : la
# détection s'y limite au titre, donc beaucoup d'offres finiront en
# "indéterminé" faute d'information).
# ---------------------------------------------------------------------------

NO_EXPERIENCE_PATTERNS = [
    r"sans exp[ée]rience", r"aucune exp[ée]rience", r"d[ée]butants?\s+accept[ée]s?",
    r"d[ée]butants?\s+bienvenus?", r"pas d'exp[ée]rience", r"0\s*an[s]?\s+d'exp[ée]rience",
    r"exp[ée]rience non requise", r"exp[ée]rience non exig[ée]e", r"ouvert(?:e)? aux d[ée]butants?",
    r"premier emploi accept[ée]", r"formation assur[ée]e", r"sans qualification",
]
EXPERIENCE_REQUIRED_PATTERNS = [
    r"\d+\s*(?:à|-|/)?\s*\d*\s*ans?\s+d'exp[ée]rience", r"exp[ée]rience\s+(?:requise|exig[ée]e|indispensable|obligatoire)",
    r"minimum\s+\d+\s*ans?", r"profil\s+confirm[ée]", r"exp[ée]riment[ée]e?\s", r"\bsenior\b",
]


def _detect_experience_from_text(text):
    t = (text or "").lower()
    if any(re.search(p, t) for p in NO_EXPERIENCE_PATTERNS):
        return "none_required"
    if any(re.search(p, t) for p in EXPERIENCE_REQUIRED_PATTERNS):
        return "required"
    return "unknown"


FT_EXPERIENCE_CODE_MAP = {"D": "none_required", "S": "desired", "E": "required"}


def tag_experience(jobs):
    """Ajoute un champ 'experience_detected' à chaque offre :
    'none_required' | 'desired' | 'required' | 'unknown'."""
    for j in jobs:
        code = j.get("experience_code")
        if code in FT_EXPERIENCE_CODE_MAP:
            j["experience_detected"] = FT_EXPERIENCE_CODE_MAP[code]
        else:
            text = f"{j.get('title', '')} {j.get('description', '')}"
            j["experience_detected"] = _detect_experience_from_text(text)
    return jobs


def filter_by_experience(jobs, cfg):
    exp_cfg = cfg.get("experience_filter", {})
    if not exp_cfg.get("enabled"):
        return jobs
    keep_unknown = exp_cfg.get("keep_if_not_mentioned", True)
    keep_desired = exp_cfg.get("keep_if_desired_only", False)

    kept = []
    for j in jobs:
        level = j.get("experience_detected", "unknown")
        if level == "none_required":
            kept.append(j)
        elif level == "unknown" and keep_unknown:
            kept.append(j)
        elif level == "desired" and keep_desired:
            kept.append(j)
    removed = len(jobs) - len(kept)
    if removed:
        print(f"[Filtre expérience] {removed} offre(s) exclue(s) (expérience requise détectée).")
    return kept


# ---------------------------------------------------------------------------
# Filtrage additionnel (mots-clés à exclure, titre, etc.) commun aux sources
# ---------------------------------------------------------------------------

def _normalize_text(s):
    s = (s or "").lower()
    replacements = {"é": "e", "è": "e", "ê": "e", "ë": "e", "à": "a", "â": "a",
                    "ù": "u", "û": "u", "ô": "o", "î": "i", "ï": "i", "ç": "c"}
    for a, b in replacements.items():
        s = s.replace(a, b)
    return s


def _collect_keyword_pool(cfg):
    """Rassemble tous les mots-clés de recherche configurés (toutes sources
    confondues, sauf Moovijob qui utilise des catégories fixes plutôt que des
    mots-clés libres) pour servir de référence au filtre strict ci-dessous."""
    pool = set()
    source_keys = [
        "france_travail", "linkedin", "vdab", "forem", "actiris", "jobswallonie",
        "hellowork", "apec", "jooble", "talent_be", "talent_lu",
    ]
    for key in source_keys:
        for kw in (cfg.get(key, {}) or {}).get("keywords_list", []) or []:
            if kw:
                pool.add(kw)
    return pool


def _title_matches_keyword_pool(title, keyword_pool):
    """Une offre est gardée si son titre contient TOUS les mots d'au moins un
    des mots-clés configurés (accents ignorés, ordre des mots indifférent).
    Ça évite qu'un moteur de recherche de jobboard élargisse la recherche à
    des offres qui n'ont rien à voir avec le mot-clé demandé."""
    if not keyword_pool:
        return True
    norm_title = _normalize_text(title)
    for kw in keyword_pool:
        words = [w for w in re.split(r"\s+", _normalize_text(kw)) if w]
        if words and all(w in norm_title for w in words):
            return True
    return False


def apply_strict_keyword_filter(jobs, cfg):
    if not cfg.get("strict_keyword_match", True):
        return jobs
    pool = _collect_keyword_pool(cfg)
    if not pool:
        return jobs
    kept = [j for j in jobs if _title_matches_keyword_pool(j.get("title", ""), pool)]
    removed = len(jobs) - len(kept)
    if removed:
        print(f"[Filtre mots-clés] {removed} offre(s) exclue(s) (titre ne correspondant à aucun mot-clé demandé).")
    return kept


def apply_common_filters(jobs, cfg):
    exclude_keywords = [k.lower() for k in cfg.get("exclude_keywords", [])]
    include_title_keywords = [k.lower() for k in cfg.get("title_must_contain", [])]

    filtered = []
    for j in jobs:
        text = f"{j.get('title','')} {j.get('company','')}".lower()
        if exclude_keywords and any(k in text for k in exclude_keywords):
            continue
        if include_title_keywords and not any(k in (j.get("title") or "").lower() for k in include_title_keywords):
            continue
        filtered.append(j)
    return filtered


# ---------------------------------------------------------------------------
# Fusion + dédoublonnage avec les offres déjà connues
# ---------------------------------------------------------------------------

def merge_and_dedupe(new_jobs, existing_jobs):
    seen_ids = {j["id"] for j in existing_jobs}
    fresh = [j for j in new_jobs if j["id"] not in seen_ids]
    for j in fresh:
        j["first_seen"] = datetime.now(timezone.utc).isoformat()
    merged = fresh + existing_jobs
    # On garde un historique raisonnable (ex: 60 derniers jours / 2000 offres max)
    merged = merged[:2000]
    return merged, fresh


def _parse_iso(date_str):
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def purge_old_jobs(jobs, max_age_days=7):
    """Retire les offres dont la date de publication (ou, à défaut, la date
    de première détection) dépasse max_age_days. Une offre dont la date est
    illisible est conservée par précaution plutôt que supprimée."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    kept = []
    for j in jobs:
        ref = _parse_iso(j.get("published_at")) or _parse_iso(j.get("first_seen"))
        if ref is None:
            kept.append(j)
            continue
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)
        if ref >= cutoff:
            kept.append(j)
    removed = len(jobs) - len(kept)
    if removed:
        print(f"[Purge] {removed} offre(s) de plus de {max_age_days} jour(s) supprimée(s).")
    return kept


def load_existing():
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return []
                return json.loads(content)
        except json.JSONDecodeError:
            print(f"[Avertissement] {DATA_FILE} illisible (JSON invalide) — on repart d'une liste vide.")
            return []
    return []


def save(jobs):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Jooble — agrégateur d'offres (69 pays, dont la France). Vraie API gratuite
# avec clé (pas de scraping) : inscription en 1 minute sur jooble.org/api/about,
# aucune carte bancaire requise. Contrairement à Indeed, elle ne bloque pas
# les requêtes automatisées puisqu'elle est justement conçue pour ça.
# ---------------------------------------------------------------------------

def fetch_jooble(cfg):
    j_cfg = cfg.get("jooble", {})
    if not j_cfg.get("enabled"):
        return []

    api_key = os.environ.get("JOOBLE_API_KEY")
    if not api_key:
        print("[Jooble] Clé API manquante (JOOBLE_API_KEY) — étape ignorée.")
        return []

    keywords_list = j_cfg.get("keywords_list") or [""]
    location = j_cfg.get("location", "France")
    max_pages = j_cfg.get("max_pages", 1)
    url = f"https://jooble.org/api/{api_key}"
    headers = {"Content-Type": "application/json"}

    jobs = []
    seen_ids = set()

    for keyword in keywords_list:
        for page in range(1, max_pages + 1):
            body = {"keywords": keyword, "location": location, "page": str(page)}
            try:
                r = requests.post(url, json=body, headers=headers, timeout=20)
            except requests.RequestException as e:
                print(f"[Jooble] ('{keyword}') Erreur réseau : {e}")
                break
            if r.status_code != 200:
                print(f"[Jooble] ('{keyword}') page {page} : HTTP {r.status_code} : {r.text[:200]}")
                break

            payload = r.json()
            results = payload.get("jobs", [])
            if not results:
                break

            for o in results:
                link = o.get("link", "")
                seed = link or f"{o.get('title','')}-{o.get('company','')}"
                job_id = f"jooble-{abs(hash(seed))}"
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)
                jobs.append({
                    "id": job_id,
                    "source": "Jooble",
                    "title": o.get("title", ""),
                    "company": o.get("company", "") or "Non précisé",
                    "location": o.get("location", ""),
                    "contract": o.get("type", "") or "",
                    "remote": False,
                    "salary": o.get("salary", "") or "",
                    "published_at": o.get("updated", ""),
                    "url": link,
                    "description": o.get("snippet", ""),
                })

            if len(results) < 20:  # taille de page habituelle de Jooble
                break
            time.sleep(0.5)

    print(f"[Jooble] {len(jobs)} offre(s) récupérée(s).")
    return jobs


def main():
    cfg = load_config()
    max_age_days = cfg.get("max_job_age_days", 7)
    existing = load_existing()
    existing = purge_old_jobs(existing, max_age_days)

    all_new = []
    all_new += fetch_france_travail(cfg)
    all_new += fetch_linkedin(cfg)
    all_new += fetch_jooble(cfg)
    all_new += fetch_generic_board("APEC", cfg.get("apec", {}))
    all_new += fetch_generic_board("HelloWork", cfg.get("hellowork", {}))
    all_new += fetch_forem(cfg)
    all_new += fetch_vdab(cfg)
    all_new += fetch_generic_board("Actiris", cfg.get("actiris", {}))
    all_new += fetch_generic_board("JobsWallonie", cfg.get("jobswallonie", {}))
    all_new += fetch_generic_board("Moovijob", cfg.get("moovijob", {}))
    all_new += fetch_generic_board("Talent.com Belgique", cfg.get("talent_be", {}))
    all_new += fetch_generic_board("Talent.com Luxembourg", cfg.get("talent_lu", {}))

    all_new = apply_common_filters(all_new, cfg)
    all_new = apply_strict_keyword_filter(all_new, cfg)
    all_new = tag_experience(all_new)
    all_new = filter_by_experience(all_new, cfg)
    for j in all_new:
        j.pop("description", None)
        j.pop("experience_code", None)

    merged, fresh = merge_and_dedupe(all_new, existing)
    merged = purge_old_jobs(merged, max_age_days)
    save(merged)

    print(f"Terminé. {len(fresh)} nouvelle(s) offre(s) ajoutée(s). Total stocké : {len(merged)}.")


if __name__ == "__main__":
    sys.exit(main())
