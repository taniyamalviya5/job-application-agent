"""End-to-end: search -> score -> approve -> send (real SMTP to a local server) -> track reply."""
import socket

import pytest

from jobagent import mailer, pipeline, store as S
from tests.fixtures import RESUME, cfg, fake_scrape


@pytest.fixture
def smtp_server():
    from aiosmtpd.controller import Controller

    class Handler:
        def __init__(self):
            self.messages = []

        async def handle_DATA(self, server, session, envelope):
            self.messages.append(envelope)
            return "250 OK"

    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    h = Handler()
    c = Controller(h, hostname="127.0.0.1", port=port)
    c.start()
    yield h, port
    c.stop()


def test_full_flow(tmp_path, monkeypatch, smtp_server):
    handler, port = smtp_server
    monkeypatch.setattr(pipeline, "resume_text", lambda c: RESUME)
    pdf = tmp_path / "resume.pdf"; pdf.write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(pipeline, "resume_pdf_path", lambda c: pdf)
    for k in ("LLM_API_KEY", "GH_REPO", "GH_TOKEN", "GITHUB_REPOSITORY", "GITHUB_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GMAIL_ADDRESS", "me@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh")
    st = S.LocalStore(tmp_path / "jobs.json")
    c = cfg()

    # 1-3: search + score + draft
    smtp = lambda msg, u, p: mailer.send_email(msg, u, p, host="127.0.0.1", port=port, use_ssl=False)
    pipeline.run("search", st=st, cfg=c, scrape=fake_scrape)
    pipeline.run("score", st=st, cfg=c)
    jobs = st.load()["jobs"]
    assert len(jobs) == 1
    node = next(j for j in jobs.values() if "Node" in j["title"])
    assert node["status"] == S.REVIEW and node["draft"]["to"] == "hr@acme-tech.in"

    # search again: no duplicates
    assert pipeline.step_search(st, c, fake_scrape) == 0

    # 4: user approves in the app
    def approve(d):
        S.set_status(d["jobs"][node["id"]], S.APPROVED, "test")
    st.update(approve)

    # 5: send
    pipeline.run("send", st=st, cfg=c, smtp=smtp)
    j = st.load()["jobs"][node["id"]]
    assert j["status"] == S.SENT and j["sent_at"] and j["message_id"]
    assert len(handler.messages) == 1
    raw = handler.messages[0].content.decode()
    assert "hr@acme-tech.in" in handler.messages[0].rcpt_tos and "resume.pdf" in raw

    # sending again does nothing (no duplicates)
    pipeline.run("send", st=st, cfg=c, smtp=smtp)
    assert len(handler.messages) == 1

    # 6: track - a colleague at the same company replies
    finder = lambda u, p, addrs: {mailer.reply_key("talent@acme-tech.in"): "Mon, 16 Nov 2026"}
    pipeline.run("track", st=st, cfg=c, finder=finder)
    assert st.load()["jobs"][node["id"]]["status"] == S.REPLIED
    assert st.load()["runs"][-1]["command"] == "track"


def test_send_respects_daily_limit_and_missing_address(tmp_path, monkeypatch):
    st = S.LocalStore(tmp_path / "jobs.json")
    sent = []

    def seed(d):
        for i in range(4):
            j = {"id": f"j{i}", "title": "t", "company": "c", "draft": {"to": f"a{i}@x.com" if i else "", "subject": "s", "body": "b"}}
            S.set_status(j, S.APPROVED)
            d["jobs"][j["id"]] = j
    st.update(seed)
    monkeypatch.setattr(pipeline, "resume_pdf_path", lambda c: None)
    c = cfg(email={"daily_send_limit": 3})
    res = pipeline.step_send(st, c, "me@gmail.com", "pw", smtp=lambda m, u, p: sent.append(m) or "id")
    assert res == {"sent": 2, "failed": 1}
    statuses = sorted(j["status"] for j in st.load()["jobs"].values())
    assert statuses == [S.APPROVED, S.FAILED, S.SENT, S.SENT]


def test_score_does_not_overwrite_user_action(tmp_path, monkeypatch):
    """If the user skips a job while scoring is running, the user's choice wins."""
    st = S.LocalStore(tmp_path / "jobs.json")
    monkeypatch.setattr(pipeline, "resume_text", lambda c: RESUME)
    pipeline.step_search(st, cfg(), fake_scrape)
    real_update = st.update
    first = next(iter(st.load()["jobs"]))

    def update(mut, msg=""):
        def skip(d):
            S.set_status(d["jobs"][first], S.REJECTED, "user")
        real_update(skip)
        return real_update(mut, msg)
    st.update = update
    pipeline.step_score(st, cfg())
    assert st.load()["jobs"][first]["status"] == S.REJECTED
