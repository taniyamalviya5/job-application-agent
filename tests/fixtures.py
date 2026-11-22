import copy

from jobagent.config import DEFAULTS

RESUME = """Senior Software Engineer, 7 years.
SKILLS
React, Node.js, TypeScript, AWS, microservices, SQL.
Led migration to microservices; cut API latency 40%."""

ROWS = [
    {"site": "naukri", "title": "Senior Node.js Engineer", "company": "Acme Tech", "location": "Indore, India",
     "job_url": "https://example.org/job/1", "emails": None,
     "description": "We need Node.js, React and AWS. Mail hr@acme-tech.in", "skills": "Node.js, React, AWS, Kafka",
     "experience_range": "5-9 Yrs"},
    {"site": "indeed", "title": "Java Developer", "company": "Beta Soft", "location": "Indore",
     "job_url": "https://example.org/job/2", "emails": ["jobs@betasoft.com"],
     "description": "Java, Spring Boot, Hibernate", "skills": "Java, Spring Boot, Hibernate, Oracle"},
    {"site": "indeed", "title": "Software Engineer Intern", "company": "Gamma", "location": "Indore",
     "job_url": "https://example.org/job/3", "emails": None, "description": "intern", "skills": ""},
]


def cfg(**over):
    c = copy.deepcopy(DEFAULTS)
    c["profile"].update(name="Test User", headline="Senior Software Engineer")
    c["search"].update(terms=["engineer"], locations=["Indore"], sites=["indeed"], exclude_keywords=["intern"])
    c["scoring"]["min_score"] = 50
    for k, v in over.items():
        c[k].update(v)
    return c


def fake_scrape(**kw):
    return [dict(r) for r in ROWS]
