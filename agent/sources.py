"""
Fetchers for each job source.
"""

import os
import re

try:
    # Works around a TLS handshake failure some job APIs trigger on macOS's
    # system Python 3.9, which links against an old LibreSSL. No-op (and
    # harmless) on any environment with a modern OpenSSL, e.g. GitHub Actions.
    import urllib3.contrib.pyopenssl
    urllib3.contrib.pyopenssl.inject_into_urllib3()
except ImportError:
    pass

import requests

USER_AGENT = "Mozilla/5.0 (compatible; job-search-agent/1.0; personal use)"


def fetch_reliefweb(keywords: list[str]) -> list[dict]:
    """
    ReliefWeb has a real public API (no key needed). Docs: https://apidoc.reliefweb.int/

    As of Nov 2025 every request needs a pre-approved appname (request one at
    https://apidoc.reliefweb.int/parameters#appname - it's a short Google form,
    approved by email). Set it as the RELIEFWEB_APPNAME environment variable
    once you have it; a placeholder or unapproved value gets a 403.
    """
    appname = os.environ.get("RELIEFWEB_APPNAME")
    if not appname:
        raise RuntimeError(
            "RELIEFWEB_APPNAME is not set. Request an approved appname at "
            "https://apidoc.reliefweb.int/parameters#appname and set it as an "
            "environment variable before running this."
        )

    url = "https://api.reliefweb.int/v2/jobs"
    params = {"appname": appname}
    body = {
        "query": {"value": " OR ".join(keywords)},
        "limit": 20,
        "fields": {"include": ["title", "url", "date", "source"]},
    }
    resp = requests.post(url, params=params, json=body, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    results = []
    for item in data.get("data", []):
        fields = item.get("fields", {})
        source_list = fields.get("source") or []
        organization = source_list[0].get("name") if source_list else "ReliefWeb"
        results.append({
            "title": fields.get("title"),
            "url": fields.get("url"),
            "posted_date": fields.get("date", {}).get("created"),
            "source": organization,
        })
    return results


def _significant_words(phrases: list[str]) -> set[str]:
    """Splits multi-word target titles into individual words for a looser,
    keyword-level pre-filter (e.g. "Multimedia Producer" -> {"multimedia",
    "producer"}). Casts a wider net than exact-phrase matching since Claude's
    scoring step filters out the false positives afterward anyway."""
    words = set()
    for phrase in phrases:
        for word in phrase.split():
            if len(word) > 2:
                words.add(word.lower())
    return words


def fetch_untalent(keywords: list[str]) -> list[dict]:
    """
    UN Talent (untalent.org) doesn't have a public API, so this scrapes their
    jobs listing page (first page only, ~30 listings).
    """
    from bs4 import BeautifulSoup

    url = "https://untalent.org/jobs"
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    words = _significant_words(keywords)
    results = []
    for card in soup.select("div.job-card"):
        title_link = card.select_one(".job-title h1 a")
        if not title_link:
            continue
        title = title_link.get_text(strip=True)
        href = title_link.get("href", "")
        title_words = set(re.findall(r"[a-z]+", title.lower()))
        if words & title_words:
            org_links = card.select(".organisation a")
            organization = next(
                (a.get_text(strip=True) for a in org_links if a.get_text(strip=True)),
                "UN Talent",
            )
            results.append({
                "title": title,
                "url": href if href.startswith("http") else f"https://untalent.org{href}",
                "posted_date": None,
                "source": organization,
            })
    return results


def fetch_jobup(keywords: list[str]) -> list[dict]:
    """
    jobup.ch's search page is a JS-rendered SPA with no listings in the raw
    HTML, but it calls an internal (undocumented, unauthenticated) search API
    at job-search-api.jobup.ch that does real full-text relevance matching -
    far more reliable than scraping. It only matches on a single phrase at a
    time (a combined "A OR B OR C" query returns zero results), so this fires
    one request per target title and dedupes by job id.
    """
    url = "https://job-search-api.jobup.ch/search"
    seen_ids = set()
    results = []
    for keyword in keywords:
        resp = requests.get(
            url, params={"query": keyword}, headers={"User-Agent": USER_AGENT}, timeout=20
        )
        resp.raise_for_status()
        for doc in resp.json().get("documents", []):
            if doc["id"] in seen_ids:
                continue
            seen_ids.add(doc["id"])
            results.append({
                "title": doc.get("title"),
                "url": f"https://www.jobup.ch/en/jobs/detail/{doc['id']}/",
                "posted_date": doc.get("publicationDate"),
                "source": (doc.get("company") or {}).get("name") or "jobup.ch",
            })
    return results


def fetch_iucn(keywords: list[str]) -> list[dict]:
    """
    IUCN has no public API - its vacancies live on a legacy ColdFusion HRMS
    (hrms.iucn.org) with no search/filter that survives a plain GET, so this
    scrapes the full open-vacancies table and applies the same keyword-level
    pre-filter as UN Talent.
    """
    from bs4 import BeautifulSoup

    url = "https://hrms.iucn.org/iresy/index.cfm?event=vac.showOpenList"
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    words = _significant_words(keywords)
    results = []
    for link in soup.select('a[href*="event=vac.show&vacId"]'):
        title = link.contents[0].strip() if link.contents else link.get_text(strip=True)
        title_words = set(re.findall(r"[a-z]+", title.lower()))
        if not (words & title_words):
            continue
        row = link.find_parent("tr")
        org_cell = link.find_parent("td")
        organization = "IUCN"
        if org_cell:
            lines = [t.strip() for t in org_cell.stripped_strings]
            if len(lines) >= 2:
                organization = lines[-1]
        href = link.get("href", "")
        results.append({
            "title": title,
            "url": f"https://hrms.iucn.org{href}" if href.startswith("/") else href,
            "posted_date": None,
            "source": organization,
        })
    return results


def fetch_all(profile: dict) -> list[dict]:
    """
    Runs every source fetcher and combines results. Each source is isolated -
    if one fails (a missing credential, a site being down, a selector that's
    drifted), it's logged and skipped rather than taking down the whole run.

    Devex is deliberately not included: its site (including its RSS feed) sits
    behind DataDome bot-detection and returns a CAPTCHA challenge instead of
    data for any automated request. Use Devex's own email job alerts instead
    of trying to scrape it.
    """
    keywords = profile["target_titles"]
    fetchers = [
        ("ReliefWeb", fetch_reliefweb),
        ("UN Talent", fetch_untalent),
        ("jobup.ch", fetch_jobup),
        ("IUCN", fetch_iucn),
    ]
    all_results = []
    for name, fetcher in fetchers:
        try:
            all_results.extend(fetcher(keywords))
        except Exception as exc:
            print(f"WARNING: {name} fetcher failed, skipping it this run: {exc}")
    return all_results
