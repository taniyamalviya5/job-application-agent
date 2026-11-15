"""Steps 2 and 3 - Score the match and draft the email.

Uses any OpenAI-compatible chat API (Gemini free tier by default, or Groq).
If no API key is set, or the AI call fails, a simple keyword matcher and an
email template take over, so the flow never stops.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

PROMPT = """You are a careful career assistant. Compare the candidate's resume with the job.

Return ONLY a JSON object with these keys:
  "score": integer 0-100 (how well the candidate fits; be strict, 70+ means a strong fit),
  "reasons": list of 2-4 short strings explaining the score,
  "missing_skills": list of skills the job asks for that the resume lacks,
  "subject": email subject line (under 80 characters),
  "body": a short, warm, professional application email (120-180 words) to the hiring team.
          Mention 2-3 specific resume achievements that match this job. No placeholders like [Name].
          End with this exact signature:
{signature}

CANDIDATE RESUME:
{resume}

JOB:
Title: {title}
Company: {company}
Location: {location}
Experience: {experience}
Skills: {skills}
Description:
{description}
"""

STOP = set("a an and the of to in for with on at by or is are be as we you your our this that will from "
           "have has it its who what job role team work years experience strong good ability".split())


def signature(cfg: dict) -> str:
    p = cfg["profile"]
    lines = ["Best regards,", p.get("name", "")]
    for k in ("headline", "phone", "linkedin"):
        if p.get(k):
            lines.append(p[k])
    return "\n".join(x for x in lines if x)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zA-Z][a-zA-Z+#.]{1,}", text.lower()) if w not in STOP and len(w) > 2}


def heuristic(job: dict, resume: str, cfg: dict) -> dict[str, Any]:
    """Offline fallback: keyword overlap score + template email."""
    job_skills = [s.strip() for s in job.get("skills", "").split(",") if s.strip()]
    res_words = _words(resume)
    if job_skills:
        hit = [s for s in job_skills if _words(s) & res_words]
        miss = [s for s in job_skills if s not in hit]
        score = round(100 * len(hit) / len(job_skills))
    else:
        jw = _words(job["title"] + " " + job.get("description", "")[:3000])
        hit = sorted(jw & res_words)[:6]
        miss = []
        score = min(100, round(100 * len(jw & res_words) / max(1, min(len(jw), 40))))
    name = cfg["profile"].get("name") or "the candidate"
    body = (
        f"Dear Hiring Team at {job['company'] or 'your company'},\n\n"
        f"I am writing to apply for the {job['title']} role. I am a "
        f"{cfg['profile'].get('headline') or 'software professional'}, and my background in "
        f"{', '.join(hit[:3]) or 'software engineering'} matches what you are looking for.\n\n"
        "I would welcome the chance to discuss how I can contribute to your team. "
        "My resume is attached for your review.\n\n"
        f"Thank you for your time and consideration.\n\n{signature(cfg)}"
    )
    return {
        "score": int(score),
        "reasons": [f"Matched: {', '.join(hit[:5])}" if hit else "Few keyword matches", "Scored offline (no AI key)"],
        "missing_skills": miss[:6],
        "subject": f"Application for {job['title']} - {name}",
        "body": body,
        "scored_by": "keywords",
    }


def _parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0) if m else text)


def ai_score(job: dict, resume: str, cfg: dict, api_key: str, session=requests) -> dict[str, Any]:
    llm = cfg["llm"]
    prompt = PROMPT.format(signature=signature(cfg), resume=resume[:12000], title=job["title"],
                           company=job["company"], location=job["location"], experience=job.get("experience", ""),
                           skills=job.get("skills", ""), description=job.get("description", "")[:6000])
    url = llm["base_url"].rstrip("/") + "/chat/completions"
    body = {"model": llm["model"], "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3, "response_format": {"type": "json_object"}}
    r = None
    for attempt in range(3):
        r = session.post(url, headers={"Authorization": f"Bearer {api_key}"}, json=body, timeout=90)
        if r.status_code == 400 and "response_format" in body:  # provider without JSON mode
            body.pop("response_format")
            continue
        if r.status_code in (429, 500, 503):  # rate limit / busy: wait and retry
            time.sleep(15 * (attempt + 1))
            continue
        break
    r.raise_for_status()
    out = _parse_json(r.json()["choices"][0]["message"]["content"])
    return {
        "score": max(0, min(100, int(out.get("score", 0)))),
        "reasons": [str(x) for x in out.get("reasons", [])][:4],
        "missing_skills": [str(x) for x in out.get("missing_skills", [])][:8],
        "subject": str(out.get("subject", f"Application for {job['title']}"))[:120],
        "body": str(out.get("body", "")).strip(),
        "scored_by": llm["model"],
    }


def score_and_draft(job: dict, resume: str, cfg: dict, api_key: str = "") -> dict[str, Any]:
    if api_key and resume.strip():
        try:
            res = ai_score(job, resume, cfg, api_key)
            if res["body"]:
                return res
        except Exception as e:
            log.warning("AI scoring failed for %s (%s); using keyword fallback", job["id"], e)
    return heuristic(job, resume, cfg)
