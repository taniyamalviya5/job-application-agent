"""The agent's flow, run by GitHub Actions (or locally):

    python -m jobagent.pipeline run      # search -> score -> send approved -> track replies
    python -m jobagent.pipeline search   # only find new jobs
    python -m jobagent.pipeline score    # only score/draft new jobs
    python -m jobagent.pipeline send     # only send jobs you approved
    python -m jobagent.pipeline track    # only check Gmail for replies

Slow work (searching, AI calls, sending) happens OUTSIDE the save step, and every
save only changes jobs that are still in the expected status. So if you approve or
reject something in the web app while a run is going, nothing gets overwritten.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from . import store as S
from .config import load_config, secret
from .llm import score_and_draft
from .mailer import build_message, find_replies, reply_key, send_email
from .resume import resume_pdf_path, resume_text
from .search import search_jobs

log = logging.getLogger("jobagent")


# --------------------------------------------------------------------- steps
def step_search(st, cfg, scrape=None) -> int:
    found = search_jobs(cfg, scrape=scrape)

    def add(data):
        added = 0
        for j in found:
            if j["id"] in data["jobs"]:
                continue
            j["found_at"] = S.now_iso()
            S.set_status(j, S.NEW, f"found on {j['site']}")
            data["jobs"][j["id"]] = j
            added += 1
        return added

    added = st.update(add, f"add jobs ({len(found)} found)") if found else 0
    log.info("Search: %d found, %d new", len(found), added)
    return added


def step_score(st, cfg, api_key: str = "") -> dict[str, int]:
    data = st.load()
    todo = [j for j in data["jobs"].values() if j["status"] == S.NEW][: cfg["scoring"]["max_jobs_per_run"]]
    if not todo:
        log.info("Score: nothing new to score")
        return {"review": 0, "low_match": 0}
    resume = resume_text(cfg)
    if not resume.strip():
        log.warning("No resume found (profile/resume.md or resume.pdf). Scores will be low.")
    results: dict[str, dict] = {}
    for i, job in enumerate(todo):
        results[job["id"]] = score_and_draft(job, resume, cfg, api_key)
        log.info("  %3d  %s @ %s", results[job["id"]]["score"], job["title"], job["company"])
        if api_key and i < len(todo) - 1:
            time.sleep(cfg["llm"]["seconds_between_calls"])

    min_score = cfg["scoring"]["min_score"]

    def apply(data):
        counts = {"review": 0, "low_match": 0}
        for jid, r in results.items():
            j = data["jobs"].get(jid)
            if not j or j["status"] != S.NEW:
                continue
            j.update(score=r["score"], reasons=r["reasons"], missing_skills=r["missing_skills"],
                     scored_by=r["scored_by"],
                     draft={"to": (j.get("emails") or [""])[0], "subject": r["subject"], "body": r["body"]})
            ok = r["score"] >= min_score
            S.set_status(j, S.REVIEW if ok else S.LOW_MATCH, f"score {r['score']}")
            counts["review" if ok else "low_match"] += 1
        return counts

    counts = st.update(apply, f"score {len(results)} jobs")
    log.info("Score: %s", counts)
    return counts


def sent_today(data) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    return sum(1 for j in data["jobs"].values() if (j.get("sent_at") or "").startswith(today))


def step_send(st, cfg, user: str, password: str, smtp=None) -> dict[str, int]:
    data = st.load()
    approved = [j for j in data["jobs"].values() if j["status"] == S.APPROVED]
    if not approved:
        log.info("Send: nothing approved")
        return {"sent": 0, "failed": 0}
    if not (user and password):
        log.warning("Send: GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set - %d approved emails waiting", len(approved))
        return {"sent": 0, "failed": 0}
    room = cfg["email"]["daily_send_limit"] - sent_today(data)
    if room <= 0:
        log.info("Send: daily limit reached, the rest go out tomorrow")
        return {"sent": 0, "failed": 0}
    attachment = resume_pdf_path(cfg)
    sender = smtp or send_email
    counts = {"sent": 0, "failed": 0}
    for j in approved[:room]:
        d = j.get("draft") or {}
        try:
            if not d.get("to"):
                raise ValueError("no recipient email address")
            msg = build_message(user, cfg["profile"].get("name", ""), d["to"], d["subject"], d["body"], attachment)
            mid = sender(msg, user, password)
            new_status, note, extra = S.SENT, f"sent to {d['to']}", {"sent_at": S.now_iso(), "message_id": mid}
            counts["sent"] += 1
        except Exception as e:
            new_status, note, extra = S.FAILED, str(e)[:200], {"last_error": str(e)[:500]}
            counts["failed"] += 1
            log.warning("  failed %s: %s", j["id"], e)

        def mark(data, jid=j["id"], status=new_status, note=note, extra=extra):
            jj = data["jobs"].get(jid)
            if jj and jj["status"] == S.APPROVED:
                jj.update(extra)
                S.set_status(jj, status, note)

        st.update(mark, f"{new_status} {j['id']}")  # saved right away: never sends twice
    log.info("Send: %s", counts)
    return counts


def step_track(st, cfg, user: str, password: str, finder=None) -> dict[str, int]:
    data = st.load()
    sent = [j for j in data["jobs"].values() if j["status"] == S.SENT and (j.get("draft") or {}).get("to")]
    if not sent or not (user and password):
        log.info("Track: nothing to check" if sent else "Track: no sent emails yet")
        return {"replied": 0, "follow_up_due": 0}
    replies = (finder or find_replies)(user, password, [j["draft"]["to"] for j in sent])
    due_before = (datetime.now(timezone.utc) - timedelta(days=cfg["email"]["follow_up_after_days"])).isoformat()

    def apply(data):
        c = {"replied": 0, "follow_up_due": 0}
        for j in data["jobs"].values():
            if j["status"] != S.SENT or not (j.get("draft") or {}).get("to"):
                continue
            key = reply_key(j["draft"]["to"])
            if key in replies:
                j["replied_at"] = replies[key]
                S.set_status(j, S.REPLIED, f"reply from {key}")
                c["replied"] += 1
            elif (j.get("sent_at") or "9") < due_before and not j.get("follow_up_due"):
                j["follow_up_due"] = True
                c["follow_up_due"] += 1
        return c

    c = st.update(apply, "track replies")
    log.info("Track: %s", c)
    return c


def step_prune(st, days: int = 45) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    def apply(data):
        old = [k for k, j in data["jobs"].items()
               if j["status"] in (S.LOW_MATCH, S.REJECTED) and j.get("updated_at", "") < cutoff]
        for k in old:
            # keep a stub so the same job is never re-added
            data["jobs"][k] = {"id": k, "title": data["jobs"][k]["title"], "company": data["jobs"][k]["company"],
                               "status": data["jobs"][k]["status"], "updated_at": data["jobs"][k]["updated_at"],
                               "pruned": True}
        return len(old)

    return st.update(apply, "prune old jobs")


# ----------------------------------------------------------------------- main
def run(command: str, st=None, cfg=None, scrape=None, smtp=None, finder=None) -> dict[str, Any]:
    cfg = cfg or load_config()
    st = st or S.make_store()
    api_key = secret("LLM_API_KEY")
    user, pwd = secret("GMAIL_ADDRESS"), secret("GMAIL_APP_PASSWORD").replace(" ", "")
    summary: dict[str, Any] = {"command": command, "at": S.now_iso(), "ai": bool(api_key)}
    if command in ("run", "search"):
        summary["new_jobs"] = step_search(st, cfg, scrape)
    if command in ("run", "score"):
        summary["scored"] = step_score(st, cfg, api_key)
    if command in ("run", "send"):
        summary["send"] = step_send(st, cfg, user, pwd, smtp)
    if command in ("run", "track"):
        summary["track"] = step_track(st, cfg, user, pwd, finder)
    if command == "run":
        summary["pruned"] = step_prune(st)

    def log_run(data):
        data.setdefault("runs", []).append(summary)
        data["runs"] = data["runs"][-30:]

    st.update(log_run, f"log {command} run")
    _write_github_summary(summary)
    return summary


def _write_github_summary(summary: dict) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write("## Job Application Agent\n\n| Step | Result |\n|---|---|\n")
        for k, v in summary.items():
            f.write(f"| {k} | {v} |\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="jobagent")
    ap.add_argument("command", choices=["run", "search", "score", "send", "track"], nargs="?", default="run")
    ap.add_argument("--local", metavar="FILE", help="use a local JSON file instead of the GitHub data branch")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    st = S.make_store(args.local) if args.local else None
    print(run(args.command, st=st))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
