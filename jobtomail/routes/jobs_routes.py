"""Suivi des jobs asynchrones (scans longs)."""

from __future__ import annotations

from flask import Blueprint, jsonify

from jobtomail.routes.auth import current_user_id
from jobtomail.services import jobs

bp = Blueprint("jobs", __name__)


@bp.route("/api/jobs/<job_id>", methods=["GET"])
def get_job_status(job_id: str):
    job = jobs.get_job(current_user_id(), job_id)
    if not job:
        return jsonify({"error": "Job introuvable"}), 404
    return jsonify(
        {
            "id": job["id"],
            "kind": job["kind"],
            "status": job["status"],
            "progress": job.get("progress"),
            "result": job.get("result"),
            "error": job.get("error"),
        }
    )
