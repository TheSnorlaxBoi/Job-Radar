"""
job-radar / fetch_jobs.py
--------------------------------
Collecte des offres d'emploi selon des filtres définis dans config.json,
depuis :
  - France Travail (API officielle, gratuite, OAuth2 client_credentials)
  - LinkedIn (page publique "guest" de résultats de recherche, sans login)

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
    base_params["sort"] = 1  # tri par date de création décroissante
    # Pas de "commune"/"rayon" : recherche au niveau national (toute la France)

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

TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(s):
    return html.unescape(TAG_RE.sub("", s or "")).strip()


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
# Filtrage additionnel (mots-clés à exclure, titre, etc.) commun aux sources
# ---------------------------------------------------------------------------

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


def main():
    cfg = load_config()
    max_age_days = cfg.get("max_job_age_days", 7)
    existing = load_existing()
    existing = purge_old_jobs(existing, max_age_days)

    all_new = []
    all_new += fetch_france_travail(cfg)
    all_new += fetch_linkedin(cfg)

    all_new = apply_common_filters(all_new, cfg)
    merged, fresh = merge_and_dedupe(all_new, existing)
    merged = purge_old_jobs(merged, max_age_days)
    save(merged)

    print(f"Terminé. {len(fresh)} nouvelle(s) offre(s) ajoutée(s). Total stocké : {len(merged)}.")


if __name__ == "__main__":
    sys.exit(main())
