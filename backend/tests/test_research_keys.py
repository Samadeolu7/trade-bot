import json
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from research.keys import create_key
from research.models import ResearchApiKey, ResearchJob

JOB = {"strategy": "donchian", "timeframe": "4h", "params": {"pyramid.max_adds": [3]}}


@pytest.fixture(autouse=True)
def hypothesis(db):
    from research.models import Hypothesis

    h = Hypothesis.objects.create(title="pyramiding", statement="adding to winners helps breakouts",
                                  family="trend/breakout")
    JOB["hypothesis_id"] = h.pk
    yield h
    JOB.pop("hypothesis_id", None)


def bearer(key):
    return {"HTTP_AUTHORIZATION": f"Bearer {key}"}


def post(client, url, data, **extra):
    return client.post(url, json.dumps(data), content_type="application/json", **extra)


@pytest.fixture
def key(owner):
    _, key = create_key(owner, "claude", hours=24)
    return key


def test_key_starts_and_reads_research_jobs(key):
    client = Client(enforce_csrf_checks=True)  # a key needs no session or CSRF token
    response = post(client, "/api/research/jobs", JOB, **bearer(key))
    assert response.status_code == 200, response.content
    job_id = response.json()["id"]
    assert client.get(f"/api/research/jobs/{job_id}", **bearer(key)).json()["status"] == "queued"
    assert client.get("/api/research/jobs", **bearer(key)).status_code == 200
    assert client.get("/api/research/experiments", **bearer(key)).status_code == 200
    assert client.get("/api/strategies?include_research=true", **bearer(key)).status_code == 200
    assert ResearchApiKey.objects.get().jobs_started == 1


def test_key_cannot_reach_anything_but_research(key):
    client = Client()
    for url in ("/api/accounts", "/api/bots", "/api/users", "/api/research/keys", "/api/auth/me", "/api/audit"):
        assert client.get(url, **bearer(key)).status_code in (401, 403), url
    assert post(client, "/api/research/keys", {"name": "x"}, **bearer(key)).status_code in (401, 403)
    assert post(client, "/api/research/lifecycle", {"label": "x", "stage": "approved"}, **bearer(key)).status_code in (401, 403)


def test_expired_revoked_and_wrong_keys_are_rejected(owner, key):
    client = Client()
    assert client.get("/api/research/jobs", **bearer("rk_nope_nope")).status_code == 401
    ResearchApiKey.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert client.get("/api/research/jobs", **bearer(key)).status_code == 401
    _, fresh = create_key(owner, "second", hours=1)
    ResearchApiKey.objects.filter(name="second").update(revoked_at=timezone.now())
    assert client.get("/api/research/jobs", **bearer(fresh)).status_code == 401


def test_key_job_cap(owner):
    _, key = create_key(owner, "capped", hours=1, max_jobs=1)
    client = Client()
    assert post(client, "/api/research/jobs", JOB, **bearer(key)).status_code == 200
    other = {**JOB, "params": {"pyramid.max_adds": [4]}}
    response = post(client, "/api/research/jobs", other, **bearer(key))
    assert response.status_code == 429
    assert ResearchJob.objects.count() == 1


def test_key_lifetime_is_limited_to_a_day(owner):
    with pytest.raises(ValueError):
        create_key(owner, "long", hours=25)
    row, key = create_key(owner, "day", hours=24)
    assert row.expires_at <= timezone.now() + timedelta(hours=24)
    assert key not in (row.key_hash, row.prefix)  # only the hash is stored


def test_owner_creates_lists_and_revokes_keys_in_the_app(owner, django_user_model):
    client = Client()
    client.force_login(owner)
    created = post(client, "/api/research/keys", {"name": "claude", "hours": 12})
    assert created.status_code == 200, created.content
    body = created.json()
    assert body["key"].startswith("rk_") and body["status"] == "active"
    listed = client.get("/api/research/keys").json()
    assert "key" not in listed[0]  # shown once, at creation
    revoked = post(client, f"/api/research/keys/{body['id']}/revoke", {})
    assert revoked.json()["status"] == "revoked"
    assert Client().get("/api/research/jobs", **bearer(body["key"])).status_code == 401

    trader = django_user_model.objects.create_user("t", password="correct-horse-battery", role="trader")
    other = Client()
    other.force_login(trader)
    assert post(other, "/api/research/keys", {"name": "x"}).status_code == 403


def test_key_jobs_need_an_open_hypothesis_within_budget(key, hypothesis, owner):
    from research.models import Experiment

    client = Client()
    no_h = {k: v for k, v in JOB.items() if k != "hypothesis_id"}
    r = post(client, "/api/research/jobs", no_h, **bearer(key))
    assert r.status_code == 400 and "hypothesis_id" in r.json()["detail"]

    hypothesis.trial_budget = 3
    hypothesis.save()
    for n, run_key in enumerate(["a", "b"]):
        Experiment.objects.create(created_at=hypothesis.created_at, kind="research_report", strategy="donchian",
                                  strategy_label="x", symbol="BTC/USDT", timeframe="4h", config_hash="x",
                                  run_key=run_key, hypothesis=hypothesis)
    two = {**JOB, "params": {"pyramid.max_adds": [3, 4]}}
    r = post(client, "/api/research/jobs", two, **bearer(key))
    assert r.status_code == 409 and "used 2 of its 3 trials" in r.json()["detail"]
    assert post(client, "/api/research/jobs", JOB, **bearer(key)).status_code == 200

    # keys can register ideas but only a person concludes them
    r = post(client, "/api/research/hypotheses",
             {"title": "carry", "statement": "funding predicts returns", "family": "Carry"}, **bearer(key))
    assert r.status_code == 200 and r.json()["family"] == "carry" and r.json()["created_by"].endswith(key[3:11])
    hid = r.json()["id"]
    assert post(client, f"/api/research/hypotheses/{hid}/conclude",
                {"status": "failed", "conclusion": "no edge"}, **bearer(key)).status_code in (401, 403)
    client.force_login(owner)
    r = post(client, f"/api/research/hypotheses/{hid}/conclude", {"status": "failed", "conclusion": "no edge"})
    assert r.status_code == 200 and r.json()["status"] == "failed"
    r = post(client, "/api/research/jobs", {**JOB, "params": {"pyramid.max_adds": [6]}, "hypothesis_id": hid})
    assert r.status_code == 400 and "is failed" in r.json()["detail"]
