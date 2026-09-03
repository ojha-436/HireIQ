"""Seed a demo workspace through the real HTTP API — no fixtures, no direct DB writes."""
import json
import os
import urllib.error
import urllib.request

BASE = os.getenv("HIREIQ_API", "http://127.0.0.1:8000")


def call(method, path, body=None, token=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data) as r:
            return json.loads(r.read() or "null")
    except urllib.error.HTTPError as e:
        return {"__error": e.code, "detail": (e.read() or b"").decode()[:300]}


ROLES = [
    {
        "title": "Senior Backend Engineer", "department": "Platform",
        "location": "Bengaluru", "country": "India", "remote_mode": "hybrid",
        "jd_text": (
            "We are hiring a Senior Backend Engineer to own our real-time platform.\n\n"
            "5-8 years of experience. You will design distributed systems in Python, run our "
            "Kafka pipelines, and operate services on Kubernetes and AWS. Strong system design "
            "and API design skills are essential. You will mentor two engineers and take part "
            "in the on-call rotation.\n\n"
            "We care as much about why you built something as how. Expect to explain the "
            "customer impact of your technical choices, not just the implementation."),
        "stages": [
            {"seq": 1, "name": "AI Screen", "kind": "ai_interview",
             "interview_config_json": {"panel": ["tech", "product"], "preset": "screen",
                                       "start_difficulty": 3}},
            {"seq": 2, "name": "AI Panel", "kind": "ai_interview",
             "interview_config_json": {"panel": ["tech", "product", "customer"],
                                       "preset": "panel", "start_difficulty": 4}},
            {"seq": 3, "name": "Hiring Manager Call", "kind": "human_interview"},
        ],
    },
    {
        "title": "Product Manager, Platform", "department": "Product",
        "location": "Remote", "country": "India", "remote_mode": "remote",
        "jd_text": ("Own the platform roadmap. 4-7 years of experience. You will need strong "
                    "product sense, work cross-functional with engineering and support, and be "
                    "comfortable with SQL for your own analysis. Experience with observability "
                    "tooling helps."),
        "stages": [
            {"seq": 1, "name": "AI Panel", "kind": "ai_interview",
             "interview_config_json": {"panel": ["product", "customer", "hiring_manager"],
                                       "preset": "panel", "start_difficulty": 3}},
            {"seq": 2, "name": "Panel with Leadership", "kind": "human_interview"},
        ],
    },
    {
        "title": "Frontend Engineer", "department": "Web",
        "location": "Berlin", "country": "Germany", "remote_mode": "remote",
        "jd_text": ("Frontend Engineer. At least 2 years of experience with React and "
                    "TypeScript. You will own our design system and care about accessibility."),
    },
    {
        "title": "Staff SRE", "department": "Infrastructure",
        "location": "Bengaluru", "country": "India", "remote_mode": "onsite",
        "jd_text": ("Staff SRE. 8+ years. Kubernetes, Terraform, observability and incident "
                    "response. You will own the on-call rotation and mentor engineers."),
    },
]


def main():
    emp = call("POST", "/api/employer/auth/register", {
        "company_name": "Northwind Systems", "domain": "northwind.com",
        "industry": "SaaS", "size_band": "50-200",
        "full_name": "Rea Patel", "email": "rea@northwind.com",
        "password": "correct-horse-battery",
    })
    if emp.get("__error"):
        print("employer exists; logging in")
        emp = call("POST", "/api/employer/auth/login", {
            "email": "rea@northwind.com", "password": "correct-horse-battery"})
    et = emp["token"]
    print("employer ready")

    published = []
    for role in ROLES:
        job = call("POST", "/api/employer/jobs/", role, et)
        if job.get("__error"):
            print("  skip", role["title"], job["detail"][:80])
            continue
        call("POST", f"/api/employer/jobs/{job['id']}/publish", None, et)
        published.append(job)
        exp = (job["min_experience_years"], job["max_experience_years"])
        print(f"  {job['title']:<28} exp={exp} skills={len(job['required_skills_json'])} "
              f"stages={[s['kind'][:2] for s in job['stages']]}")

    cand = call("POST", "/api/candidate/auth/register", {
        "full_name": "Sam Okafor", "email": "sam@example.com",
        "password": "another-good-passphrase", "phone": "+91 98000 12345"})
    if cand.get("__error"):
        cand = call("POST", "/api/candidate/auth/login", {
            "email": "sam@example.com", "password": "another-good-passphrase"})
    ct = cand["token"]
    call("PATCH", "/api/candidate/auth/me", {
        "country": "India", "years_experience": 6,
        "profile_sections_json": {
            "headline": "Backend engineer, 6 yrs - payments & streaming",
            "summary": "I build and operate high-throughput event pipelines.",
            "skills": ["Python", "Kafka", "Kubernetes", "AWS", "System Design"],
        }}, ct)
    print("candidate ready (6 yrs, 5 skills on file)")

    board = call("GET", "/api/candidate/jobs?sort=match", None, ct)
    print(f"\nboard: {len(board['jobs'])} open, facets: "
          f"{board['facets']['countries']} / {board['facets']['remote_modes']}")
    for j in board["jobs"]:
        print(f"  {j['match_pct'] if j['match_pct'] is not None else '--':>3}%  {j['title']}")

    if published:
        app_row = call("POST", f"/api/candidate/apply/{published[0]['id']}", None, ct)
        if not app_row.get("__error"):
            s = call("POST", f"/api/employer/applications/{app_row['id']}/start-interview",
                     None, et)
            if not s.get("__error"):
                print(f"\ninterview queued: {s['session_id']}")
                print(f"  {s['stage_name']} | {[p['key'] for p in s['panel']]}")


if __name__ == "__main__":
    main()
