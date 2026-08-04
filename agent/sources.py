"""
Fetchers for each job source.
"""

import os
import re
from datetime import date, timedelta

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
            "deadline": fields.get("date", {}).get("closing"),
            "source": organization,
        })
    return results


def _matches_target_titles(title: str, target_titles: list[str], min_overlap: int = 2) -> bool:
    """
    A listing counts as a candidate only if its title shares at least
    min_overlap significant words with the SAME target title phrase - not
    just any single word from the combined pool across all target titles.
    A single generic overlap (e.g. "Officer") on its own is not enough;
    requiring 2 shared words filters that out while still catching real
    matches like "Senior Video Producer" against "Video Producer".
    """
    title_words = set(re.findall(r"[a-z]+", title.lower()))
    for phrase in target_titles:
        phrase_words = {w for w in re.findall(r"[a-z]+", phrase.lower()) if len(w) > 2}
        if len(phrase_words & title_words) >= min_overlap:
            return True
    return False


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

    results = []
    for card in soup.select("div.job-card"):
        title_link = card.select_one(".job-title h1 a")
        if not title_link:
            continue
        title = title_link.get_text(strip=True)
        href = title_link.get("href", "")
        if _matches_target_titles(title, keywords):
            org_links = card.select(".organisation a")
            organization = next(
                (a.get_text(strip=True) for a in org_links if a.get_text(strip=True)),
                "UN Talent",
            )
            # The list page only gives a relative "N days left" countdown, not
            # an absolute date - convert it so it's comparable across sources.
            deadline = None
            expiration = card.select_one(".expiration")
            if expiration:
                match = re.search(r"(\d+)\s*days?\s*left", expiration.get_text(strip=True))
                if match:
                    deadline = str(date.today() + timedelta(days=int(match.group(1))))
            results.append({
                "title": title,
                "url": href if href.startswith("http") else f"https://untalent.org{href}",
                "posted_date": None,
                "deadline": deadline,
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
            title = doc.get("title") or ""
            if not _matches_target_titles(title, keywords):
                # jobup's own relevance ranking is fuzzy enough to surface
                # unrelated "Officer"/"Manager" roles on a single shared word -
                # apply the same local filter as the scraped sources instead
                # of trusting it entirely.
                continue
            seen_ids.add(doc["id"])
            results.append({
                "title": title,
                "url": f"https://www.jobup.ch/en/jobs/detail/{doc['id']}/",
                "posted_date": doc.get("publicationDate"),
                "deadline": None,  # jobup.ch's search API doesn't expose a closing date
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

    results = []
    for link in soup.select('a[href*="event=vac.show&vacId"]'):
        title = link.contents[0].strip() if link.contents else link.get_text(strip=True)
        if not _matches_target_titles(title, keywords):
            continue
        row = link.find_parent("tr")
        org_cell = link.find_parent("td")
        organization = "IUCN"
        if org_cell:
            lines = [t.strip() for t in org_cell.stripped_strings]
            if len(lines) >= 2:
                organization = lines[-1]
        deadline = None
        if row:
            cells = row.find_all("td")
            if len(cells) >= 4:
                deadline = cells[3].get_text(strip=True) or None
        href = link.get("href", "")
        results.append({
            "title": title,
            "url": f"https://hrms.iucn.org{href}" if href.startswith("/") else href,
            "posted_date": None,
            "deadline": deadline,
            "source": organization,
        })
    return results


def fetch_impactpool(keywords: list[str]) -> list[dict]:
    """
    Impactpool (impactpool.org) has no public API, but its search page is
    server-rendered (Rails/Hotwire) despite the JS framework, so a plain GET
    to /search?search=... returns real job cards - no headless browser
    needed. Fires one request per target title (fuzzy relevance search, not
    boolean OR) and applies the same local title filter as other sources.
    """
    from bs4 import BeautifulSoup

    seen_urls = set()
    results = []
    for keyword in keywords:
        resp = requests.get(
            "https://www.impactpool.org/search",
            params={"search": keyword},
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for card in soup.select("div.job"):
            link = card.find("a", href=True)
            if not link:
                continue
            title_el = link.select_one("div[type='cardTitle']")
            title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)
            if not _matches_target_titles(title, keywords):
                continue
            url = f"https://www.impactpool.org{link['href']}"
            if url in seen_urls:
                continue
            seen_urls.add(url)
            fields = link.select("div[type='bodyEmphasis']")
            organization = fields[0].get_text(strip=True) if fields else "Impactpool"
            results.append({
                "title": title,
                "url": url,
                "posted_date": None,
                "deadline": None,  # not shown on the card
                "source": organization,
            })
    return results


def fetch_journalismjobs(keywords: list[str]) -> list[dict]:
    """
    JournalismJobs.com is plain server-rendered HTML with a real search (the
    query param is "keywords", not "search" - confirmed by inspecting the
    actual search form; the obvious guess silently returns the unfiltered
    listing instead of erroring). robots.txt only blocks AI-crawler bots, not
    generic user agents.
    """
    from bs4 import BeautifulSoup

    seen_urls = set()
    results = []
    for keyword in keywords:
        resp = requests.get(
            "https://www.journalismjobs.com/job-listings",
            params={"keywords": keyword},
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for card in soup.select("a.job-item"):
            title_el = card.select_one(".job-item-title")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            if not _matches_target_titles(title, keywords):
                continue
            href = card.get("href", "")
            url = href if href.startswith("http") else f"https://www.journalismjobs.com{href}"
            if url in seen_urls:
                continue
            seen_urls.add(url)
            org_el = card.select_one(".job-item-company")
            results.append({
                "title": title,
                "url": url,
                "posted_date": None,
                "deadline": None,  # not shown on this site
                "source": org_el.get_text(strip=True) if org_el else "JournalismJobs.com",
            })
    return results


def fetch_comnetwork(keywords: list[str]) -> list[dict]:
    """
    "CommsNetwork" as such doesn't exist - the real site is The Communications
    Network at comnetwork.org/jobs. Static Squarespace HTML, but with no
    search/query parameter at all, so this fetches the one chronological
    listing page (a few dozen postings) and filters locally.
    """
    from bs4 import BeautifulSoup

    resp = requests.get(
        "https://www.comnetwork.org/jobs", headers={"User-Agent": USER_AGENT}, timeout=20
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    for card in soup.select("article.blog-item"):
        title_link = card.select_one("h1.blog-title a")
        if not title_link:
            continue
        title = title_link.get_text(strip=True)
        if not _matches_target_titles(title, keywords):
            continue
        href = title_link.get("href", "")
        paragraphs = card.select("div.blog-excerpt-wrapper p")
        organization = paragraphs[0].get_text(strip=True) if paragraphs else "The Communications Network"
        results.append({
            "title": title,
            "url": href if href.startswith("http") else f"https://www.comnetwork.org{href}",
            "posted_date": None,
            "deadline": None,  # not shown on this site
            "source": organization,
        })
    return results


def fetch_unhcr(keywords: list[str]) -> list[dict]:
    """
    UNHCR's own site (unhcr.org) sits behind Cloudflare bot-detection, but its
    recruitment actually runs on Workday at a separate, unprotected domain,
    whose CxS search API is callable with no auth at all.
    """
    seen_paths = set()
    results = []
    for keyword in keywords:
        resp = requests.post(
            "https://unhcr.wd3.myworkdayjobs.com/wday/cxs/unhcr/External/jobs",
            json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": keyword},
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        resp.raise_for_status()
        for posting in resp.json().get("jobPostings", []):
            title = posting.get("title") or ""
            path = posting.get("externalPath")
            if not path or path in seen_paths or not _matches_target_titles(title, keywords):
                continue
            seen_paths.add(path)
            results.append({
                "title": title,
                "url": f"https://unhcr.wd3.myworkdayjobs.com/en-US/External{path}",
                "posted_date": None,
                "deadline": None,  # Workday's search results don't include a closing date
                "source": "UNHCR",
            })
    return results


def fetch_iom(keywords: list[str]) -> list[dict]:
    """
    IOM's main site (iom.int) is Akamai-blocked, but its recruiting backend is
    a separate Oracle Fusion Cloud instance with an unauthenticated REST API.
    Requires expand=requisitionList - without it the endpoint only returns
    search-facet metadata (counts per location/category), not the actual
    postings, which is easy to miss since it still returns 200 either way.
    """
    seen_ids = set()
    results = []
    for keyword in keywords:
        resp = requests.get(
            "https://fa-evlj-saasfaprod1.fa.ocs.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions",
            params={
                "onlyData": "true",
                "expand": "requisitionList",
                "finder": f"findReqs;siteNumber=CX_1001,limit=25,offset=0,sortBy=POSTING_DATES_DESC,keyword={keyword}",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        resp.raise_for_status()
        items = resp.json().get("items") or [{}]
        for req in items[0].get("requisitionList", []):
            title = req.get("Title") or ""
            req_id = req.get("Id")
            if not req_id or req_id in seen_ids or not _matches_target_titles(title, keywords):
                continue
            seen_ids.add(req_id)
            results.append({
                "title": title,
                "url": f"https://fa-evlj-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/{req_id}",
                "posted_date": req.get("PostedDate"),
                "deadline": req.get("PostingEndDate"),
                "source": req.get("Organization") or "IOM",
            })
    return results


def fetch_unep(keywords: list[str]) -> list[dict]:
    """
    UNEP has no separate careers site - it recruits through the central UN
    Secretariat portal (careers.un.org), which covers every UN department at
    once and has no free-text search, only structured facet filters. Rather
    than reverse-engineer UNEP's internal department code, this fetches the
    300 most recently posted UN Secretariat-wide listings in one request and
    filters locally for department name containing "Environment" - covers
    UNEP without needing the exact facet code, at the cost of only seeing the
    last ~300 postings UN-wide rather than UNEP's full history.
    """
    resp = requests.post(
        "https://careers.un.org/api/public/opening/jo/list/filteredV2/en",
        json={
            "filterConfig": {
                "aoe": [], "aoi": [], "el": [], "ct": [], "ds": [], "jn": [],
                "jf": [], "jc": [], "jle": [], "dept": [], "span": [],
            },
            "pagination": {"page": 0, "itemPerPage": 300, "sortBy": "startDate", "sortDirection": -1},
        },
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    resp.raise_for_status()

    results = []
    for item in resp.json().get("data", {}).get("list", []):
        dept_name = (item.get("dept") or {}).get("name") or ""
        if "environment" not in dept_name.lower():
            continue
        title = item.get("postingTitle") or ""
        if not _matches_target_titles(title, keywords):
            continue
        end_date = (item.get("endDate") or "").split("T")[0] or None
        results.append({
            "title": title,
            "url": f"https://careers.un.org/jobSearchDescription/{item.get('jobId')}?language=en",
            "posted_date": (item.get("startDate") or "").split("T")[0] or None,
            "deadline": end_date,
            "source": dept_name,
        })
    return results


def fetch_web_search(keywords: list[str]) -> list[dict]:
    """
    General web-search fallback for catching postings outside the fixed
    source list above (mirrored/aggregated LinkedIn postings, smaller boards
    not otherwise in rotation, etc). Uses the Brave Search API since it's the
    only realistic option for a personal, low-volume, no-existing-account
    setup: Google Custom Search is closed to new signups, Bing's Search API
    was fully retired in Aug 2025, and DuckDuckGo's unofficial HTML endpoint
    blocked ~30% of test requests with a bot-detection challenge.

    Requires a BRAVE_API_KEY environment variable - get a free-tier key at
    https://api-dashboard.search.brave.com (email signup, no card required
    for the free tier as of testing). NOT verified against a live key (this
    was written without one available) - the first real run is the first
    real test of this function, same as ReliefWeb's appname was.
    """
    api_key = os.environ.get("BRAVE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "BRAVE_API_KEY is not set. Get a free-tier key at "
            "https://api-dashboard.search.brave.com and set it as an "
            "environment variable before running this."
        )

    seen_urls = set()
    results = []
    for keyword in keywords:
        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": f'"{keyword}" job', "count": 10},
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
            timeout=20,
        )
        resp.raise_for_status()
        for item in resp.json().get("web", {}).get("results", []):
            title = item.get("title") or ""
            url = item.get("url")
            if not url or url in seen_urls or not _matches_target_titles(title, keywords):
                continue
            seen_urls.add(url)
            results.append({
                "title": title,
                "url": url,
                "posted_date": None,
                "deadline": None,
                "source": "Web search",
            })
    return results


def fetch_all(profile: dict) -> list[dict]:
    """
    Runs every source fetcher and combines results. Each source is isolated -
    if one fails (a missing credential, a site being down, a selector that's
    drifted), it's logged and skipped rather than taking down the whole run.

    Sources deliberately not included, and why:
    - LinkedIn: job pages require a logged-in session, not fetchable at all.
    - Devex: site (including its RSS feed) sits behind DataDome bot-detection,
      returns a CAPTCHA challenge instead of data for any automated request.
    - Idealist: client-rendered SPA; its search runs on Algolia but the
      app/API key isn't present anywhere in the static JS bundles (issued at
      runtime), so there's no stateless way to call it.
    - EU Careers portal: no unified vacancy list - actual postings are
      scattered across dozens of separate agency/institution sites.
    - MediaBistro, ProductionHub, Poynter, Muck Rack: client-rendered SPA
      with no discoverable API (MediaBistro), or blocked by a Cloudflare
      challenge (the other three).
    - WHO, FAO: recruitment runs on Oracle Taleo behind session/JSF-gated
      AJAX calls (WHO also has a SAML login wall) - the job data plainly
      isn't in the raw HTML and there's no stateless API to call, unlike
      IOM/UNHCR/UNEP below. Would need a full headless browser.
    - Editor & Publisher (via jobs.mediajobboard.com): technically
      scrapeable static HTML, but that site's own robots.txt disallows
      generic crawlers - skipped out of respect for their stated policy
      rather than because it's technically infeasible.
    Use Devex/LinkedIn/etc.'s own email job alerts instead of trying to
    automate around these.
    """
    keywords = profile["target_titles"]
    fetchers = [
        ("ReliefWeb", fetch_reliefweb),
        ("UN Talent", fetch_untalent),
        ("jobup.ch", fetch_jobup),
        ("IUCN", fetch_iucn),
        ("Impactpool", fetch_impactpool),
        ("JournalismJobs.com", fetch_journalismjobs),
        ("The Communications Network", fetch_comnetwork),
        ("UNHCR", fetch_unhcr),
        ("IOM", fetch_iom),
        ("UNEP", fetch_unep),
        ("Web search", fetch_web_search),
    ]
    all_results = []
    for name, fetcher in fetchers:
        try:
            all_results.extend(fetcher(keywords))
        except Exception as exc:
            print(f"WARNING: {name} fetcher failed, skipping it this run: {exc}")
    return all_results
