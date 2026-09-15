from __future__ import annotations

import time

from jobtomail.services import jobs


def test_job_lifecycle_success(temp_db):
    job_id = jobs.create_job(1, "test", params={"foo": "bar"})
    job = jobs.get_job(1, job_id)
    assert job["status"] == "queued"
    assert job["kind"] == "test"
    assert job["params"] == {"foo": "bar"}

    jobs.submit_job(1, job_id, lambda: {"count": 3}, kind="test")

    for _ in range(50):
        job = jobs.get_job(1, job_id)
        if job["status"] == "done":
            break
        time.sleep(0.05)

    assert job["status"] == "done"
    assert job["result"] == {"count": 3}


def test_job_lifecycle_error(temp_db):
    job_id = jobs.create_job(1, "test")

    def _boom():
        raise RuntimeError("échec volontaire")

    jobs.submit_job(1, job_id, _boom, kind="test")

    for _ in range(50):
        job = jobs.get_job(1, job_id)
        if job["status"] == "error":
            break
        time.sleep(0.05)

    assert job["status"] == "error"
    assert "échec volontaire" in job["error"]


def test_get_job_unknown_returns_none(temp_db):
    assert jobs.get_job(1, "does-not-exist") is None
