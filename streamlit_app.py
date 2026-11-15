"""Step 4 - Your review screen. Hosted free on Streamlit Community Cloud.

Approve, edit, or skip each drafted email. Approved emails are sent by the
GitHub Actions agent (right away if you press "Send approved now", otherwise at
the next daily run).
"""
from __future__ import annotations

import hmac

import pandas as pd
import streamlit as st

from jobagent import store as S
from jobagent.config import load_config, secret

st.set_page_config(page_title="Job Application Agent", layout="wide")
WORKFLOW = "agent.yml"

LABELS = {S.NEW: "New", S.REVIEW: "To review", S.APPROVED: "Approved (queued)", S.SENT: "Sent",
          S.APPLIED_MANUALLY: "Applied on website", S.REPLIED: "Replied", S.LOW_MATCH: "Low match",
          S.REJECTED: "Skipped", S.FAILED: "Failed"}


# ------------------------------------------------------------------ login gate
def check_password() -> bool:
    expected = secret("APP_PASSWORD")
    if not expected:
        st.warning("APP_PASSWORD is not set. Add it in Streamlit -> App settings -> Secrets before sharing the link.")
        return True
    if st.session_state.get("auth_ok"):
        return True
    st.title("Job Application Agent")
    pwd = st.text_input("Password", type="password")
    if st.button("Sign in", type="primary"):
        if hmac.compare_digest(pwd, expected):
            st.session_state.auth_ok = True
            st.rerun()
        st.error("Wrong password")
    return False


if not check_password():
    st.stop()

cfg = load_config()


@st.cache_resource
def get_store():
    return S.make_store()


store = get_store()
remote = isinstance(store, S.GitHubStore)


def load(force: bool = False) -> dict:
    if force or "data" not in st.session_state:
        st.session_state.data = store.load()
    return st.session_state.data


def save(mutator, message: str) -> None:
    store.update(mutator, message)
    load(force=True)


def change_status(jid: str, status: str, note: str = "", draft: dict | None = None,
                  only_from: tuple[str, ...] | None = None) -> None:
    def m(data):
        j = data["jobs"].get(jid)
        if not j or (only_from and j["status"] not in only_from):
            return
        if draft is not None:
            j["draft"] = draft
        S.set_status(j, status, note)
    save(m, f"{status} {jid}")


def dispatch(command: str) -> None:
    if not remote:
        st.error("Running the agent from here needs GH_REPO and GH_TOKEN in the app secrets.")
        return
    try:
        store.dispatch_workflow(WORKFLOW, {"command": command})
        st.toast(f"Started '{command}' on GitHub Actions. Refresh in 2-3 minutes.")
    except Exception as e:
        st.error(f"Could not start the workflow: {e}")


data = load()
jobs = [j for j in data["jobs"].values() if not j.get("pruned")]
by_status: dict[str, list] = {s: [] for s in S.ALL_STATUSES}
for j in jobs:
    by_status.setdefault(j["status"], []).append(j)

# ---------------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Agent")
    st.caption(f"Storage: {'GitHub data branch (' + store.repo + ')' if remote else 'local file'}")
    if st.button("Refresh", use_container_width=True):
        load(force=True)
        st.rerun()
    if st.button("Find and score new jobs now", use_container_width=True):
        dispatch("run")
    n_appr = len(by_status[S.APPROVED])
    if st.button(f"Send approved now ({n_appr})", use_container_width=True, disabled=n_appr == 0, type="primary"):
        dispatch("send")
    st.divider()
    for s in S.ALL_STATUSES:
        if by_status.get(s):
            st.write(f"{LABELS[s]}: **{len(by_status[s])}**")
    if remote:
        runs = store.latest_runs(WORKFLOW, 3)
        if runs:
            st.divider()
            st.caption("Recent agent runs")
            for r in runs:
                icon = {"success": "OK", "failure": "FAILED"}.get(r["conclusion"] or "", "running")
                st.markdown(f"{icon} · [{r['created_at'][:16].replace('T', ' ')}]({r['url']}) · {r['event']}")

# ------------------------------------------------------------------------ main
st.title("Job Application Agent")
c = st.columns(5)
c[0].metric("To review", len(by_status[S.REVIEW]))
c[1].metric("Queued to send", len(by_status[S.APPROVED]))
c[2].metric("Sent", len(by_status[S.SENT]) + len(by_status[S.REPLIED]))
c[3].metric("Applied on website", len(by_status[S.APPLIED_MANUALLY]))
c[4].metric("Replies", len(by_status[S.REPLIED]))

tab_review, tab_track, tab_low, tab_all, tab_help = st.tabs(
    ["Review", "Sent & tracking", "Low match", "All jobs", "Help"])


def job_header(j: dict) -> None:
    st.markdown(f"#### {j['title']} — {j['company'] or 'Unknown company'}")
    meta = [j.get("location"), j.get("site"), j.get("experience"), (j.get("date_posted") or "")[:10]]
    st.caption(" · ".join(x for x in meta if x))
    if j.get("job_url"):
        st.markdown(f"[Open job posting]({j['job_url']})")


with tab_review:
    queue = sorted(by_status[S.REVIEW], key=lambda j: -(j.get("score") or 0))
    if not queue:
        st.info("Nothing to review. Use 'Find and score new jobs now' in the sidebar, or wait for the daily run.")
    for j in queue:
        with st.container(border=True):
            left, right = st.columns([3, 1])
            with left:
                job_header(j)
            with right:
                st.metric("Match", f"{j.get('score', 0)}/100")
                st.caption(f"by {j.get('scored_by', '?')}")
            for r in j.get("reasons", []):
                st.write(f"- {r}")
            if j.get("missing_skills"):
                st.caption("Missing: " + ", ".join(j["missing_skills"]))
            with st.expander("Job description"):
                st.markdown(j.get("description") or "_No description_")
            d = j.get("draft") or {}
            to = st.text_input("To", d.get("to", ""), key=f"to_{j['id']}",
                               help="Found in the posting. If empty, type the recruiter's email or apply on the website.")
            subject = st.text_input("Subject", d.get("subject", ""), key=f"sub_{j['id']}")
            body = st.text_area("Email", d.get("body", ""), height=260, key=f"body_{j['id']}")
            b1, b2, b3 = st.columns(3)
            new_draft = {"to": to.strip(), "subject": subject.strip(), "body": body.strip()}
            if b1.button("Approve email", key=f"ok_{j['id']}", type="primary", use_container_width=True):
                if "@" not in new_draft["to"]:
                    st.error("Add a recipient email first, or use 'I applied on the website'.")
                else:
                    change_status(j["id"], S.APPROVED, "approved in app", new_draft, only_from=(S.REVIEW,))
                    st.rerun()
            if b2.button("I applied on the website", key=f"man_{j['id']}", use_container_width=True):
                change_status(j["id"], S.APPLIED_MANUALLY, "applied manually", new_draft, only_from=(S.REVIEW,))
                st.rerun()
            if b3.button("Skip", key=f"rej_{j['id']}", use_container_width=True):
                change_status(j["id"], S.REJECTED, "skipped in app", only_from=(S.REVIEW,))
                st.rerun()

with tab_track:
    for s in (S.APPROVED, S.FAILED, S.SENT, S.REPLIED, S.APPLIED_MANUALLY):
        items = sorted(by_status[s], key=lambda j: j.get("updated_at", ""), reverse=True)
        if not items:
            continue
        st.subheader(f"{LABELS[s]} ({len(items)})")
        for j in items:
            with st.container(border=True):
                a, b = st.columns([4, 1])
                with a:
                    st.markdown(f"**{j['title']}** — {j['company']}  ·  to `{(j.get('draft') or {}).get('to', '-')}`")
                    info = [f"score {j.get('score', '-')}"]
                    if j.get("sent_at"):
                        info.append(f"sent {j['sent_at'][:10]}")
                    if j.get("replied_at"):
                        info.append(f"reply {j['replied_at']}")
                    if j.get("follow_up_due") and s == S.SENT:
                        info.append("follow-up due")
                    if j.get("last_error"):
                        info.append(f"error: {j['last_error']}")
                    st.caption(" · ".join(info))
                with b:
                    if s == S.FAILED and st.button("Retry", key=f"retry_{j['id']}"):
                        change_status(j["id"], S.APPROVED, "retry", only_from=(S.FAILED,))
                        st.rerun()
                    if s == S.APPROVED and st.button("Undo", key=f"undo_{j['id']}"):
                        change_status(j["id"], S.REVIEW, "undo approve", only_from=(S.APPROVED,))
                        st.rerun()
                    if s in (S.SENT, S.APPLIED_MANUALLY) and st.button("Got a reply", key=f"rep_{j['id']}"):
                        change_status(j["id"], S.REPLIED, "marked in app", only_from=(S.SENT, S.APPLIED_MANUALLY))
                        st.rerun()
    if not any(by_status[s] for s in (S.APPROVED, S.FAILED, S.SENT, S.REPLIED, S.APPLIED_MANUALLY)):
        st.info("No applications yet.")

with tab_low:
    low = sorted(by_status[S.LOW_MATCH], key=lambda j: -(j.get("score") or 0))
    st.caption(f"Jobs scoring under {cfg['scoring']['min_score']}. Move any of them to review if you disagree.")
    for j in low[:100]:
        a, b = st.columns([5, 1])
        a.markdown(f"**{j.get('score', 0)}** · [{j['title']}]({j.get('job_url', '')}) — {j['company']} · {j.get('location', '')}")
        if b.button("Review", key=f"up_{j['id']}"):
            change_status(j["id"], S.REVIEW, "moved to review", only_from=(S.LOW_MATCH,))
            st.rerun()

with tab_all:
    if jobs:
        df = pd.DataFrame([{"status": LABELS.get(j["status"], j["status"]), "score": j.get("score"),
                            "title": j["title"], "company": j["company"], "location": j.get("location"),
                            "site": j.get("site"), "found": (j.get("found_at") or "")[:10],
                            "link": j.get("job_url")} for j in jobs])
        st.dataframe(df.sort_values(["found", "score"], ascending=False), use_container_width=True, hide_index=True,
                     column_config={"link": st.column_config.LinkColumn("link")})
        st.download_button("Download CSV", df.to_csv(index=False), "jobs.csv", "text/csv")
    else:
        st.info("No jobs yet.")

with tab_help:
    st.markdown(f"""
**Daily flow**
1. GitHub Actions runs the agent every morning (or press *Find and score new jobs now*).
2. Review each email here: edit it, then **Approve**, **I applied on the website**, or **Skip**.
3. Press **Send approved now** (or wait for the next run). Max **{cfg['email']['daily_send_limit']}** emails a day.
4. Replies from the same company are detected automatically from Gmail.

**Change search terms, locations, or limits:** open your repo, press `.` to open github.dev,
edit `config.yaml`, and commit. No install needed.
""")
    if data.get("runs"):
        st.subheader("Last runs")
        st.json(data["runs"][-5:][::-1], expanded=False)
