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


def _parse_positive_int(value) -> int | None:
    """Renvoie un entier >= 1, ou None si `value` n'en est pas un."""
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 1 else None


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
    updates: dict[str, str] = {}

    if "scan_limit" in data:
        scan_limit = _parse_positive_int(data["scan_limit"])
        if scan_limit is None:
            return jsonify({"error": "scan_limit doit être un entier positif"}), 400
        updates["quota_scan_limit"] = str(scan_limit)

    if "email_limit" in data:
        email_limit = _parse_positive_int(data["email_limit"])
        if email_limit is None:
            return jsonify({"error": "email_limit doit être un entier positif"}), 400
        updates["quota_email_limit"] = str(email_limit)

    if updates:
        db.set_config_values(updates)
    return jsonify({"ok": True})
