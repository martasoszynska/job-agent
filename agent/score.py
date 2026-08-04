"""
Scores a job listing against the profile using the Claude API.

Requires an ANTHROPIC_API_KEY environment variable (set as a GitHub Actions
secret - never commit it to the repo).
"""

import json
import os
import anthropic

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SCORING_PROMPT = """You are helping score a job listing against a candidate profile.

CANDIDATE PROFILE:
{profile}

JOB LISTING:
Title: {title}
Source: {source}
URL: {url}

Return ONLY a JSON object (no other text, no markdown fences) with this exact shape:
{{"match_score": <float 0.0-1.0>, "notes": "<one or two sentence rationale>", "legal_work_flag": "<short note on visa/sponsorship relevance if location is mentioned, else empty string>"}}
"""


def score_listing(listing: dict, profile: dict) -> dict:
    prompt = SCORING_PROMPT.format(
        profile=json.dumps(profile, indent=2),
        title=listing.get("title", ""),
        source=listing.get("source", ""),
        url=listing.get("url", ""),
    )
    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    text_block = next((block for block in response.content if block.type == "text"), None)
    if text_block is None:
        # No text block at all (e.g. cut off mid-reasoning before producing
        # output) - flag for manual review rather than crashing the whole run.
        return {"match_score": None, "notes": "Model returned no text output.", "legal_work_flag": ""}
    text = text_block.text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Model didn't follow the format exactly - flag for manual review rather
        # than silently failing. Worth handling more gracefully once you see how
        # often this actually happens in practice.
        return {"match_score": None, "notes": f"Could not parse score. Raw: {text}", "legal_work_flag": ""}
