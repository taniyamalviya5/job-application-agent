import base64
import json

import pytest

from jobagent import llm, search, store as S
from tests.fixtures import RESUME, ROWS, cfg, fake_scrape


def test_normalize_extracts_emails_from_description():
    j = search.normalize(ROWS[0])
    assert j["emails"] == ["hr@acme-tech.in"]
    assert j["title"] == "Senior Node.js Engineer"
    assert len(j["id"]) == 12


def test_extract_emails_skips_noreply():
    assert search.extract_emails("a@x.com no-reply@x.com A@X.com") == ["a@x.com"]


def test_search_dedupes_excludes_and_survives_site_errors():
    calls = []

    def scrape(**kw):
        calls.append(kw["site_name"])
        if kw["site_name"] == ["naukri"]:
            raise RuntimeError("blocked")
        return fake_scrape()

    c = cfg(search={"sites": ["indeed", "naukri"], "locations": ["Indore", "Remote"]})
    jobs = search.search_jobs(c, scrape=scrape)
    assert len(calls) == 4
    assert {j["title"] for j in jobs} == {"Senior Node.js Engineer"}  # Java-only and intern listings excluded


def test_heuristic_scores_matching_job_higher():
    c = cfg()
    good = llm.heuristic(search.normalize(ROWS[0]), RESUME, c)
    bad = llm.heuristic(search.normalize(ROWS[1]), RESUME, c)
    assert good["score"] > bad["score"]
    assert "Test User" in good["body"] and good["subject"]


class FakeResp:
    def __init__(self, status=200, payload=None, content=b""):
        self.status_code, self._p, self.content = status, payload, content

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_ai_score_parses_openai_style_response():
    out = {"score": 88, "reasons": ["Node match"], "missing_skills": ["Kafka"], "subject": "Hi", "body": "Hello"}

    class Sess:
        def post(self, url, **kw):
            assert url.endswith("/chat/completions")
            assert kw["json"]["model"] == cfg()["llm"]["model"]
            return FakeResp(payload={"choices": [{"message": {"content": "```json\n" + json.dumps(out) + "\n```"}}]})

    r = llm.ai_score(search.normalize(ROWS[0]), RESUME, cfg(), "key", session=Sess())
    assert r["score"] == 88 and r["missing_skills"] == ["Kafka"]


def test_score_and_draft_falls_back_when_ai_fails(monkeypatch):
    monkeypatch.setattr(llm, "ai_score", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("quota")))
    r = llm.score_and_draft(search.normalize(ROWS[0]), RESUME, cfg(), api_key="k")
    assert r["scored_by"] == "keywords"


def test_local_store_roundtrip(tmp_path):
    st = S.LocalStore(tmp_path / "j.json")
    st.update(lambda d: d["jobs"].update(a={"id": "a", "status": S.NEW}))
    assert st.load()["jobs"]["a"]["status"] == S.NEW


def test_github_store_retries_on_conflict():
    state = {"sha": "s1", "data": {"version": 1, "jobs": {"x": {"id": "x"}}, "runs": []}, "puts": 0}

    class Sess:
        headers = {}

        def get(self, url, **kw):
            if "/branches/" in url:
                return FakeResp(200, {})
            raw = base64.b64encode(json.dumps(state["data"]).encode()).decode()
            return FakeResp(200, {"content": raw, "sha": state["sha"]})

        def put(self, url, json=None, **kw):
            state["puts"] += 1
            if state["puts"] == 1:          # someone else saved first
                state["sha"] = "s2"
                return FakeResp(409, {})
            assert json["sha"] == "s2" and json["branch"] == "data"
            state["data"] = __import__("json").loads(base64.b64decode(json["content"]))
            return FakeResp(200, {})

    gs = S.GitHubStore("me/repo", "tok", session=Sess())
    gs.update(lambda d: d["jobs"].update(y={"id": "y"}))
    assert state["puts"] == 2 and set(state["data"]["jobs"]) == {"x", "y"}


def test_github_store_requires_credentials():
    with pytest.raises(ValueError):
        S.GitHubStore("", "")


def test_ai_score_retries_without_json_mode():
    seen = []

    class Sess:
        def post(self, url, json=None, **kw):
            seen.append("response_format" in json)
            if "response_format" in json:
                return FakeResp(400, {})
            content = '{"score": 70, "reasons": [], "missing_skills": [], "subject": "s", "body": "b"}'
            return FakeResp(200, {"choices": [{"message": {"content": content}}]})

    assert llm.ai_score(search.normalize(ROWS[0]), RESUME, cfg(), "k", session=Sess())["score"] == 70
    assert seen == [True, False]
