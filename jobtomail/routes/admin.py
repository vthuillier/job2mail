"""Panneau admin : liste utilisateurs, quotas, usage global."""

from __future__ import annotations

from flask import Blueprint, abort, jsonify, render_template, request

from jobtomail import db
from jobtomail.routes.auth import current_user_id

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _require_admin() -> None:
    user = db.get_user_by_id(current_user_id())
    if not user or not user["is_admin"]:
        abort(403)


@bp.before_request
def _guard():
    _require_admin()


@bp.route("")
def dashboard():
    return render_template("admin.html")


@bp.route("/api/users")
def api_users():
    return jsonify(db.get_all_users_with_usage())


@bp.route("/api/quotas", methods=["POST"])
def api_set_quotas():
    data = request.get_json(force=True) or {}
    if "scan_limit" in data:
        db.set_config_values({"quota_scan_limit": str(data["scan_limit"])})
    if "email_limit" in data:
        db.set_config_values({"quota_email_limit": str(data["email_limit"])})
    return jsonify({"ok": True})
