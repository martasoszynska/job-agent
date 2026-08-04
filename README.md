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

## This was written without live internet access to the job sites

Important: `agent/sources.py` was written in a sandboxed environment that
could reach GitHub and package registries, but not the actual job sites. That
means:
- The ReliefWeb fetcher uses their real public API and is probably close to
  right, but hasn't been run against live data.
- The UN Talent (untalent.org) fetcher scrapes HTML with placeholder
  selectors that almost certainly need adjusting once you can see the real
  page structure.
- jobup.ch, Devex, and IUCN don't have fetchers yet at all - `fetch_all()` has
  a TODO marking where to add them.

**This is exactly the work to do next in Claude Code**, which has full
internet access and can run the script, show you the real errors, and fix
them with you - a much better way to learn what's actually happening than
reading finished code.

## Setup steps (do these in Claude Code / on GitHub)

1. Create a new GitHub repository and push this code to it.
2. Get an Anthropic API key from https://console.anthropic.com (this is
   separate from your claude.ai subscription - it's pay-as-you-go, and at
   this scale (one run a week, a handful of listings scored) the cost is
   small - a few cents per run, not dollars).
3. In your GitHub repo: Settings -> Secrets and variables -> Actions -> New
   repository secret. Name it `ANTHROPIC_API_KEY`, paste in your key.
4. Test it locally first: `pip install -r requirements.txt`, set
   `ANTHROPIC_API_KEY` in your terminal environment, run
   `python agent/main.py`, see what breaks, fix it.
5. Once it runs cleanly locally, push to GitHub. The workflow will fire
   automatically every Monday at 07:00 UTC (edit the cron line in
   `weekly.yml` to change the schedule), or you can trigger it manually from
   the repo's "Actions" tab any time via "Run workflow."
6. Each run commits the updated `state.json` and `This_Weeks_Picks.xlsx`
   straight back to the repo, so you always have the latest file plus a full
   history of what's been found over time.

## Known limitations to design around together

- No LinkedIn source - LinkedIn blocks this kind of automated access, same as
  discussed for the Chrome-based approach.
- Scrapers (as opposed to real APIs) will occasionally break when a site
  redesigns its page - expect to revisit `sources.py` every so often.
- The Claude-scoring step assumes the model returns clean JSON; `score.py`
  has a fallback for when it doesn't, but keep an eye on how often that
  triggers.
