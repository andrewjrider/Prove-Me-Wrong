import uuid

from flask import Blueprint, abort, redirect, render_template, request, url_for

from . import db
from .summarizer import generate_summary

bp = Blueprint("main", __name__)

VOTER_COOKIE = "voter_id"
VOTER_COOKIE_MAX_AGE = 60 * 60 * 24 * 365 * 5


def get_voter_id():
    return request.cookies.get(VOTER_COOKIE)


def set_voter_cookie(response, voter_id):
    response.set_cookie(
        VOTER_COOKIE,
        voter_id,
        max_age=VOTER_COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
    )
    return response


def claim_context(claim_id):
    claim = db.get_claim(claim_id)
    if claim is None:
        abort(404)
    voter_id = get_voter_id()
    counts = db.get_vote_counts(claim_id)
    total = counts["agree"] + counts["disagree"]
    agree_pct = round((counts["agree"] / total) * 100) if total else 0
    disagree_pct = 100 - agree_pct if total else 0
    responses = db.get_responses(claim_id)
    summary = generate_summary(responses)
    return {
        "claim": claim,
        "counts": counts,
        "total": total,
        "agree_pct": agree_pct,
        "disagree_pct": disagree_pct,
        "responses": responses,
        "summary": summary,
        "my_choice": db.get_voter_choice(claim_id, voter_id) if voter_id else None,
    }


@bp.route("/")
def index():
    claims = db.get_claims()
    claims_with_counts = []
    for claim in claims:
        counts = db.get_vote_counts(claim["id"])
        claims_with_counts.append({**claim, "counts": counts, "total": counts["agree"] + counts["disagree"]})
    return render_template("index.html", claims=claims_with_counts)


@bp.route("/claim/<int:claim_id>")
def claim_detail(claim_id):
    context = claim_context(claim_id)
    return render_template("claim.html", **context)


@bp.route("/claim/<int:claim_id>/card")
def claim_card(claim_id):
    context = claim_context(claim_id)
    return render_template("card.html", **context)


@bp.route("/claim/<int:claim_id>/vote", methods=["POST"])
def vote(claim_id):
    if db.get_claim(claim_id) is None:
        abort(404)
    choice = request.form.get("choice")
    if choice not in {"agree", "disagree"}:
        abort(400)

    voter_id = get_voter_id() or str(uuid.uuid4())
    db.cast_vote(claim_id, voter_id, choice)

    response = redirect(url_for("main.claim_detail", claim_id=claim_id))
    return set_voter_cookie(response, voter_id)


@bp.route("/claim/<int:claim_id>/respond", methods=["POST"])
def respond(claim_id):
    if db.get_claim(claim_id) is None:
        abort(404)
    side = request.form.get("side")
    body = (request.form.get("body") or "").strip()
    if side not in {"agree", "disagree"} or not body:
        abort(400)

    voter_id = get_voter_id() or str(uuid.uuid4())
    db.add_response(claim_id, voter_id, side, body)

    response = redirect(url_for("main.claim_detail", claim_id=claim_id))
    return set_voter_cookie(response, voter_id)


@bp.route("/admin")
def admin_new_claim_form():
    from flask import current_app

    token = request.args.get("token", "")
    if token != current_app.config["ADMIN_TOKEN"]:
        abort(403)
    return render_template("admin.html", token=token)


@bp.route("/admin/claims", methods=["POST"])
def admin_create_claim():
    from flask import current_app

    token = request.form.get("token", "")
    if token != current_app.config["ADMIN_TOKEN"]:
        abort(403)
    text = (request.form.get("text") or "").strip()
    if not text:
        abort(400)
    claim_id = db.create_claim(text)
    return redirect(url_for("main.claim_detail", claim_id=claim_id))
