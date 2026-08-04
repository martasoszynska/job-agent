# Job Search Agent

A weekly agent that searches job sites, scores listings against your profile
using the Claude API, avoids showing you the same listing twice, and writes
the results to an Excel file - fully automated via GitHub Actions.

## How it works

1. **`agent/profile.json`** - your search criteria as data (edit this any time
   your preferences change - no code changes needed).
2. **`agent/state.json`** - permanent memory of every listing ever seen. This
   is what solves the "Chrome extension forgets everything" problem: since
   this file lives in the git repo, it persists across every run, forever.
3. **`agent/sources.py`** - fetches raw listings from each job site.
4. **`agent/score.py`** - asks Claude to score each *new* listing against your
   profile.
5. **`agent/main.py`** - runs the whole pipeline: fetch, dedupe against
   `state.json`, score what's new, write `This_Weeks_Picks.xlsx`, update
   `state.json`.
6. **`.github/workflows/weekly.yml`** - the schedule. GitHub runs this for you
   every Monday, on their servers, whether or not your laptop is on.

## Sources currently in rotation

Verified against live data (real API or real scrape, tested and fixed
together in Claude Code): ReliefWeb, UN Talent (untalent.org), jobup.ch,
IUCN, Impactpool, JournalismJobs.com, The Communications Network
(comnetwork.org), UNHCR, IOM, UNEP, plus an optional general web-search
fallback (Brave Search API) for anything outside this fixed list.

Two of these need a one-time setup step before they'll return anything (see
below): ReliefWeb requires a pre-approved `appname`, and the web-search
fallback requires your own Brave Search API key. Every other source works
with no extra setup.

Only a low-relevance-word overlap or a listing scoring below 0.4 against your
profile gets filtered out automatically - nothing needs an API key beyond
what's listed below to start returning real results.

### Sources deliberately left out

- **LinkedIn** - job pages require a logged-in session; not fetchable at all.
- **Devex** - blocked by DataDome bot-detection (returns a CAPTCHA challenge
  to any automated request, including its RSS feed). Use Devex's own email
  job alerts instead.
- **Idealist, MediaBistro, EU Careers portal** - client-rendered SPAs with no
  discoverable API, or (EU Careers) no unified list at all.
- **ProductionHub, Poynter, Muck Rack** - blocked by a Cloudflare challenge.
- **WHO, FAO** - Oracle Taleo behind session/JSF-gated AJAX (WHO also has a
  SAML login wall); would need a full headless browser, not just requests.
- **Editor & Publisher (mediajobboard.com)** - technically scrapeable, but
  its `robots.txt` disallows generic crawlers, so it's skipped out of
  respect for that policy rather than because it's technically infeasible.

Full detail and evidence for each of these lives in the docstring of
`fetch_all()` in `agent/sources.py`.

## Setup steps (do these in Claude Code / on GitHub)

1. Create a new GitHub repository and push this code to it.
2. Get an Anthropic API key from https://console.anthropic.com (this is
   separate from your claude.ai subscription - it's pay-as-you-go, and at
   this scale (one run a week, a handful of listings scored) the cost is
   small - a few cents per run, not dollars).
3. In your GitHub repo: Settings -> Secrets and variables -> Actions -> New
   repository secret. Name it `ANTHROPIC_API_KEY`, paste in your key.
4. Optional, to enable ReliefWeb: request an approved `appname` at
   https://apidoc.reliefweb.int/parameters#appname (a short form, approved by
   email) and add it as a second repository secret, `RELIEFWEB_APPNAME`.
5. Optional, to enable the general web-search fallback: get a free-tier key
   at https://api-dashboard.search.brave.com and add it as a third repository
   secret, `BRAVE_API_KEY`. Without it, this source is skipped automatically
   (same as ReliefWeb without its appname) - everything else still runs.
6. Test it locally first: `pip install -r requirements.txt`, set
   `ANTHROPIC_API_KEY` (and the optional ones above) in your terminal
   environment, run `python agent/main.py`, see what breaks, fix it.
7. Once it runs cleanly locally, push to GitHub. The workflow will fire
   automatically every Monday at 07:00 UTC (edit the cron line in
   `weekly.yml` to change the schedule), or you can trigger it manually from
   the repo's "Actions" tab any time via "Run workflow."
8. Each run commits the updated `state.json` and `This_Weeks_Picks.xlsx`
   straight back to the repo, so you always have the latest file plus a full
   history of what's been found over time.

## Known limitations to design around together

- Scrapers (as opposed to real APIs) will occasionally break when a site
  redesigns its page - expect to revisit `sources.py` every so often.
- The Claude-scoring step assumes the model returns clean JSON; `score.py`
  has a fallback for when it doesn't, but keep an eye on how often that
  triggers.
- Listings scoring below 0.4 aren't remembered in `state.json`, only shown
  ones are - so a low-scoring listing gets re-fetched and re-scored (costing
  a small API call) every week it's still posted, rather than being
  permanently skipped after the first pass.
- The web-search fallback (Brave Search API) returns arbitrary web pages, not
  verified job postings the way the other sources are - expect noisier,
  lower-precision results from that source specifically.
