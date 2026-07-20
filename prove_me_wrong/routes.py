import uuid

from flask import Blueprint, abort, current_app, redirect, render_template, request, url_for

from . import db
from .summarizer import generate_summary

bp = Blueprint("main", __name__)

VOTER_COOKIE = "voter_id"
VOTER_COOKIE_MAX_AGE = 60 * 60 * 24 * 365 * 5

VOTE_RATE_LIMIT_WINDOW_SECONDS = 60
RESPONSE_RATE_LIMIT_WINDOW_SECONDS = 60 * 60
RESPONSE_RATE_LIMIT_MAX = 5
CLAIM_SUBMIT_RATE_LIMIT_WINDOW_SECONDS = 60 * 60
CLAIM_SUBMIT_RATE_LIMIT_MAX = 3

CLAIM_MIN_LENGTH = 10
CLAIM_MAX_LENGTH = 280


def get_voter_id():
    return request.cookies.get(VOTER_COOKIE)


def rate_limited(message):
    return render_template("rate_limited.html", message=message), 429


def require_admin():
    """Abort 403 unless a valid admin token is present (in query string or form)."""
    token = request.values.get("token", "")
    if token != current_app.config["ADMIN_TOKEN"]:
        abort(403)
    return token


def get_approved_claim_or_404(claim_id):
    claim = db.get_claim(claim_id)
    if claim is None or claim.get("status") != "approved":
        abort(404)
    return claim


def set_voter_cookie(response, voter_id):
    response.set_cookie(
        VOTER_COOKIE,
        voter_id,
        max_age=VOTER_COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
    )
    return response


# A claim counts as "divisive" once there's enough of a crowd that a near-even
# split is meaningful rather than 1-vs-1 noise. Close splits are the whole point
# of the site, so they get surfaced and badged.
DIVISIVE_MIN_VOTES = 3
DIVISIVE_MAX_MARGIN = 20


def claim_stats(counts):
    """Everything derived from a claim's vote counts: percentages, the verdict
    label, and whether it's a divisive (close) debate. Shared by the homepage
    and the claim page so the two never disagree."""
    agree, disagree = counts["agree"], counts["disagree"]
    total = agree + disagree
    agree_pct = round((agree / total) * 100) if total else 0
    disagree_pct = 100 - agree_pct if total else 0
    margin = abs(agree_pct - disagree_pct)

    if total == 0:
        verdict = {"label": "No votes yet", "side": "none"}
        divisive = False
    elif agree_pct == disagree_pct:
        verdict = {"label": "Dead heat", "side": "tie"}
        divisive = total >= DIVISIVE_MIN_VOTES
    elif agree_pct > disagree_pct:
        verdict = {"label": "Agree leads", "side": "agree"}
        divisive = total >= DIVISIVE_MIN_VOTES and margin <= DIVISIVE_MAX_MARGIN
    else:
        verdict = {"label": "Disagree leads", "side": "disagree"}
        divisive = total >= DIVISIVE_MIN_VOTES and margin <= DIVISIVE_MAX_MARGIN

    return {
        "total": total,
        "agree_pct": agree_pct,
        "disagree_pct": disagree_pct,
        "margin": margin,
        "verdict": verdict,
        "divisive": divisive,
    }


def claim_context(claim_id):
    claim = get_approved_claim_or_404(claim_id)
    voter_id = get_voter_id()
    counts = db.get_vote_counts(claim_id)
    stats = claim_stats(counts)
    responses = db.get_responses(claim_id)
    summary = {
        "agree_summary": claim["summary_agree"] or "No arguments submitted yet.",
        "disagree_summary": claim["summary_disagree"] or "No arguments submitted yet.",
    }
    return {
        "claim": claim,
        "counts": counts,
        "responses": responses,
        "summary": summary,
        "my_choice": db.get_voter_choice(claim_id, voter_id) if voter_id else None,
        **stats,
    }


@bp.route("/healthz")
def healthz():
    return "ok"


SORT_OPTIONS = ("hot", "new", "divisive")


@bp.route("/")
def index():
    sort = request.args.get("sort", "hot")
    if sort not in SORT_OPTIONS:
        sort = "hot"

    claims = db.get_claims()  # approved, newest-first from the DB
    enriched = []
    for claim in claims:
        counts = db.get_vote_counts(claim["id"])
        enriched.append({**claim, "counts": counts, **claim_stats(counts)})

    if sort == "hot":
        # Most-voted first; ties keep DB order (newest first).
        enriched.sort(key=lambda c: c["total"], reverse=True)
    elif sort == "divisive":
        # Closest splits first among claims with a real crowd; unvoted claims last.
        enriched.sort(
            key=lambda c: (
                0 if c["total"] >= DIVISIVE_MIN_VOTES else 1,
                c["margin"] if c["total"] >= DIVISIVE_MIN_VOTES else 999,
                -c["total"],
            )
        )
    # "new" is already newest-first from db.get_claims().

    total_votes = sum(c["total"] for c in enriched)
    return render_template(
        "index.html",
        claims=enriched,
        sort=sort,
        claim_count=len(enriched),
        total_votes=total_votes,
    )


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
    get_approved_claim_or_404(claim_id)
    choice = request.form.get("choice")
    if choice not in {"agree", "disagree"}:
        abort(400)

    ip = request.remote_addr
    since = db.utc_ago(VOTE_RATE_LIMIT_WINDOW_SECONDS)
    if db.count_rate_limit_events(ip, "vote", since, claim_id=claim_id) > 0:
        return rate_limited("You can only change your vote on a claim once per minute. Try again shortly.")

    voter_id = get_voter_id() or str(uuid.uuid4())
    db.cast_vote(claim_id, voter_id, choice)
    db.record_rate_limit_event(ip, "vote", claim_id=claim_id)

    # ?voted= lets the claim page fire a celebratory confetti burst on arrival.
    response = redirect(url_for("main.claim_detail", claim_id=claim_id, voted=choice))
    return set_voter_cookie(response, voter_id)


@bp.route("/claim/<int:claim_id>/respond", methods=["POST"])
def respond(claim_id):
    get_approved_claim_or_404(claim_id)
    side = request.form.get("side")
    body = (request.form.get("body") or "").strip()
    if side not in {"agree", "disagree"} or not body:
        abort(400)

    ip = request.remote_addr
    since = db.utc_ago(RESPONSE_RATE_LIMIT_WINDOW_SECONDS)
    if db.count_rate_limit_events(ip, "respond", since) >= RESPONSE_RATE_LIMIT_MAX:
        return rate_limited("You've submitted too many responses in the last hour. Try again later.")

    voter_id = get_voter_id() or str(uuid.uuid4())
    db.add_response(claim_id, voter_id, side, body)
    db.record_rate_limit_event(ip, "respond")

    claim = db.get_claim(claim_id)
    responses = db.get_responses(claim_id)
    summary = generate_summary(claim["text"], responses)
    db.update_claim_summary(claim_id, summary["agree_summary"], summary["disagree_summary"], len(responses))

    response = redirect(url_for("main.claim_detail", claim_id=claim_id))
    return set_voter_cookie(response, voter_id)


@bp.route("/submit", methods=["GET"])
def submit_form():
    return render_template(
        "submit.html",
        min_length=CLAIM_MIN_LENGTH,
        max_length=CLAIM_MAX_LENGTH,
        error=None,
        text="",
    )


@bp.route("/submit", methods=["POST"])
def submit_claim():
    text = (request.form.get("text") or "").strip()

    def reject(error):
        return (
            render_template(
                "submit.html",
                min_length=CLAIM_MIN_LENGTH,
                max_length=CLAIM_MAX_LENGTH,
                error=error,
                text=text,
            ),
            400,
        )

    if len(text) < CLAIM_MIN_LENGTH:
        return reject(f"A claim needs to be at least {CLAIM_MIN_LENGTH} characters.")
    if len(text) > CLAIM_MAX_LENGTH:
        return reject(f"Keep it under {CLAIM_MAX_LENGTH} characters — one sharp sentence works best.")

    ip = request.remote_addr
    since = db.utc_ago(CLAIM_SUBMIT_RATE_LIMIT_WINDOW_SECONDS)
    if db.count_rate_limit_events(ip, "submit_claim", since) >= CLAIM_SUBMIT_RATE_LIMIT_MAX:
        return rate_limited("You've submitted a few claims already this hour. Try again later.")

    db.create_claim(text, status="pending")
    db.record_rate_limit_event(ip, "submit_claim")
    return redirect(url_for("main.submitted"))


@bp.route("/submitted")
def submitted():
    return render_template("submitted.html")


@bp.route("/admin")
def admin_new_claim_form():
    token = require_admin()
    return render_template("admin.html", token=token, pending_count=db.get_pending_count())


@bp.route("/admin/claims", methods=["POST"])
def admin_create_claim():
    require_admin()
    text = (request.form.get("text") or "").strip()
    if not text:
        abort(400)
    claim_id = db.create_claim(text, status="approved")
    return redirect(url_for("main.claim_detail", claim_id=claim_id))


@bp.route("/admin/moderation")
def admin_moderation():
    token = require_admin()
    return render_template(
        "moderation.html",
        token=token,
        pending=db.get_pending_claims(),
    )


@bp.route("/admin/claims/<int:claim_id>/approve", methods=["POST"])
def admin_approve_claim(claim_id):
    token = require_admin()
    if db.get_claim(claim_id) is None:
        abort(404)
    db.set_claim_status(claim_id, "approved")
    return redirect(url_for("main.admin_moderation", token=token))


@bp.route("/admin/claims/<int:claim_id>/reject", methods=["POST"])
def admin_reject_claim(claim_id):
    token = require_admin()
    if db.get_claim(claim_id) is None:
        abort(404)
    db.set_claim_status(claim_id, "rejected")
    return redirect(url_for("main.admin_moderation", token=token))
