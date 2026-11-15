"""Smoke test of the Streamlit review screen."""
import json

from streamlit.testing.v1 import AppTest

from jobagent import store as S


def seed(path):
    j = {"id": "abc123", "title": "Senior Node.js Engineer", "company": "Acme", "location": "Indore",
         "site": "naukri", "job_url": "https://example.org/1", "score": 82, "reasons": ["Strong Node match"],
         "missing_skills": ["Kafka"], "scored_by": "keywords", "description": "desc",
         "draft": {"to": "hr@acme.in", "subject": "Application", "body": "Hello"}}
    S.set_status(j, S.REVIEW)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "jobs": {"abc123": j}, "runs": []}))


def test_review_and_approve(tmp_path, monkeypatch):
    data = tmp_path / "data" / "jobs.json"
    seed(data)
    for k in ("GH_REPO", "GH_TOKEN", "GITHUB_REPOSITORY", "GITHUB_TOKEN", "APP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(S, "make_store", lambda local_path=None: S.LocalStore(data))
    at = AppTest.from_file("../streamlit_app.py", default_timeout=30).run()
    assert not at.exception
    assert any("Senior Node.js Engineer" in m.value for m in at.markdown)
    approve = next(b for b in at.button if b.label == "Approve email")
    approve.click().run()
    assert not at.exception
    assert json.loads(data.read_text())["jobs"]["abc123"]["status"] == S.APPROVED


def test_password_gate(tmp_path, monkeypatch):
    data = tmp_path / "data" / "jobs.json"
    seed(data)
    monkeypatch.setenv("APP_PASSWORD", "secret")
    monkeypatch.setattr(S, "make_store", lambda local_path=None: S.LocalStore(data))
    at = AppTest.from_file("../streamlit_app.py", default_timeout=30).run()
    assert not any("Senior Node.js Engineer" in m.value for m in at.markdown)
    at.text_input[0].input("secret")
    at.button[0].click().run()
    assert any("Senior Node.js Engineer" in m.value for m in at.markdown)
