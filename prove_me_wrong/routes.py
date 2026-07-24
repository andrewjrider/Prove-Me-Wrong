import uuid
from pathlib import Path
from urllib.parse import urlparse

from flask import Blueprint, Response, abort, current_app, jsonify, redirect, render_template, request, url_for

from . import db
from .og_image import og_png_for_claim
from .summarizer import generate_summary

# Crawlers, link-preview fetchers, and CLI tools shouldn't inflate the "is anyone
# actually here" signal. Cheap substring filter on the User-Agent.
BOT_UA_MARKERS = (
    "bot", "crawler", "spider", "slurp", "facebookexternalhit", "embedly",
    "quora link preview", "headlesschrome", "lighthouse", "curl", "wget",
    "python-requests", "go-http-client", "render/",
)

bp = Blueprint("main", __name__)

VOTER_COOKIE = "voter_id"
VOTER_COOKIE_MAX_AGE = 60 * 60 * 24 * 365 * 5

VOTE_RATE_LIMIT_WINDOW_SECONDS = 60
# A voter has exactly one (changeable) vote per claim, so flipping never skews the
# tally — this cap is just an abuse ceiling, and it's generous enough that a genuine
# "changed my mind" flip right after voting always goes through.
VOTE_RATE_LIMIT_MAX = 8
RESPONSE_RATE_LIMIT_WINDOW_SECONDS = 60 * 60
RESPONSE_RATE_LIMIT_MAX = 5
CLAIM_SUBMIT_RATE_LIMIT_WINDOW_SECONDS = 60 * 60
CLAIM_SUBMIT_RATE_LIMIT_MAX = 3
REACT_RATE_LIMIT_WINDOW_SECONDS = 60 * 60
REACT_RATE_LIMIT_MAX = 60

CLAIM_MIN_LENGTH = 10
CLAIM_MAX_LENGTH = 280


def get_voter_id():
    return request.cookies.get(VOTER_COOKIE)


def rate_limited(message):
    return render_template("rate_limited.html", message=message), 429


def record_view(claim_id):
    """Log a page view for analytics. Never let a logging failure or a weird
    referrer break the actual page — swallow everything."""
    ua = (request.user_agent.string or "").lower()
    if any(marker in ua for marker in BOT_UA_MARKERS):
        return
    host = ""
    ref = request.referrer
    if ref:
        try:
            host = (urlparse(ref).hostname or "").lower()
        except ValueError:
            host = ""
    try:
        db.record_page_view(claim_id, host)
    except Exception:
        pass


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
    responses = db.get_responses(claim_id, voter_id)
    summary = {
        "agree_summary": claim["summary_agree"] or "No arguments submitted yet.",
        "disagree_summary": claim["summary_disagree"] or "No arguments submitted yet.",
    }
    my_choice = db.get_voter_choice(claim_id, voter_id) if voter_id else None
    return {
        "claim": claim,
        "counts": counts,
        "responses": responses,
        "summary": summary,
        "my_choice": my_choice,
        "you": _standing(my_choice, stats) if my_choice else None,
        **stats,
    }


@bp.route("/healthz")
def healthz():
    return "ok"


@bp.route("/favicon.ico")
def favicon():
    return redirect(url_for("static", filename="favicon.ico"))


@bp.route("/robots.txt")
def robots():
    body = "\n".join([
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin",
        "Sitemap: " + url_for("main.sitemap", _external=True),
        "",
    ])
    return Response(body, mimetype="text/plain")


@bp.route("/sitemap.xml")
def sitemap():
    urls = [url_for("main.index", _external=True)]
    urls += [url_for("main.claim_detail", claim_id=c["id"], _external=True) for c in db.get_claims()]
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    parts += ["  <url><loc>{}</loc></url>".format(u) for u in urls]
    parts.append("</urlset>")
    return Response("\n".join(parts), mimetype="application/xml")


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

    total_votes = sum(c["total"] for c in enriched)

    # Featured "debate of the day": the spiciest live claim — most divisive among
    # those with a real crowd, else the most-voted. Only worth featuring when it
    # actually creates hierarchy (more than a couple of claims).
    featured = None
    if len(enriched) > 2:
        crowd = [c for c in enriched if c["total"] >= DIVISIVE_MIN_VOTES]
        if crowd:
            featured = min(crowd, key=lambda c: (c["margin"], -c["total"]))
        else:
            featured = max(enriched, key=lambda c: c["total"])
            if featured["total"] == 0:
                featured = None
    featured_id = featured["id"] if featured else None

    listed = [c for c in enriched if c["id"] != featured_id]
    if sort == "hot":
        listed.sort(key=lambda c: c["total"], reverse=True)
    elif sort == "divisive":
        listed.sort(
            key=lambda c: (
                0 if c["total"] >= DIVISIVE_MIN_VOTES else 1,
                c["margin"] if c["total"] >= DIVISIVE_MIN_VOTES else 999,
                -c["total"],
            )
        )
    # "new" is already newest-first from db.get_claims().

    record_view(None)
    return render_template(
        "index.html",
        claims=listed,
        featured=featured,
        sort=sort,
        claim_count=len(enriched),
        total_votes=total_votes,
    )


@bp.route("/claim/<int:claim_id>")
def claim_detail(claim_id):
    context = claim_context(claim_id)  # 404s unless approved, so views only count real claims
    record_view(claim_id)
    return render_template("claim.html", **context)


@bp.route("/claim/<int:claim_id>/card")
def claim_card(claim_id):
    context = claim_context(claim_id)
    return render_template("card.html", **context)


@bp.route("/claim/<int:claim_id>/og.png")
def claim_og(claim_id):
    claim = get_approved_claim_or_404(claim_id)
    counts = db.get_vote_counts(claim_id)
    stats = claim_stats(counts)
    cache_dir = Path(current_app.config["DATABASE_PATH"]).parent / "og_cache"
    png = og_png_for_claim(
        cache_dir,
        claim_id,
        claim["text"],
        stats["agree_pct"],
        stats["disagree_pct"],
        counts["agree"],
        counts["disagree"],
    )
    return Response(png, mimetype="image/png", headers={"Cache-Control": "public, max-age=300"})


VOTE_RATE_LIMIT_MESSAGE = "You're voting awfully fast — give it a second."


def _register_vote(claim_id, choice):
    """Shared vote logic for the form and JSON endpoints. Returns
    (voter_id, None) on success, or (None, error_message) if rate-limited."""
    ip = request.remote_addr
    since = db.utc_ago(VOTE_RATE_LIMIT_WINDOW_SECONDS)
    if db.count_rate_limit_events(ip, "vote", since, claim_id=claim_id) >= VOTE_RATE_LIMIT_MAX:
        return None, VOTE_RATE_LIMIT_MESSAGE
    voter_id = get_voter_id() or str(uuid.uuid4())
    db.cast_vote(claim_id, voter_id, choice)
    db.record_rate_limit_event(ip, "vote", claim_id=claim_id)
    return voter_id, None


def _standing(choice, stats):
    """Where the voter lands vs the crowd: majority / minority (contrarian) / even."""
    you_pct = stats["agree_pct"] if choice == "agree" else stats["disagree_pct"]
    standing = "majority" if you_pct > 50 else "minority" if you_pct < 50 else "even"
    return {"side": choice, "pct": you_pct, "standing": standing}


@bp.route("/claim/<int:claim_id>/vote", methods=["POST"])
def vote(claim_id):
    get_approved_claim_or_404(claim_id)
    choice = request.form.get("choice")
    if choice not in {"agree", "disagree"}:
        abort(400)
    voter_id, error = _register_vote(claim_id, choice)
    if error:
        return rate_limited(error)
    # ?voted= lets the claim page fire a celebratory confetti burst on the no-JS reload path.
    response = redirect(url_for("main.claim_detail", claim_id=claim_id, voted=choice))
    return set_voter_cookie(response, voter_id)


@bp.route("/claim/<int:claim_id>/vote.json", methods=["POST"])
def vote_json(claim_id):
    """Same vote, but returns the fresh split as JSON so the claim page can do an
    animated in-place reveal instead of a full reload (progressive enhancement)."""
    get_approved_claim_or_404(claim_id)
    choice = request.form.get("choice")
    if choice not in {"agree", "disagree"}:
        abort(400)
    voter_id, error = _register_vote(claim_id, choice)
    if error:
        return jsonify(ok=False, error=error), 429
    counts = db.get_vote_counts(claim_id)
    stats = claim_stats(counts)
    resp = jsonify(
        ok=True,
        agree=counts["agree"],
        disagree=counts["disagree"],
        total=stats["total"],
        agree_pct=stats["agree_pct"],
        disagree_pct=stats["disagree_pct"],
        margin=stats["margin"],
        divisive=stats["divisive"],
        verdict=stats["verdict"],
        my_choice=choice,
        you=_standing(choice, stats),
    )
    return set_voter_cookie(resp, voter_id)


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


def _react_allowed():
    ip = request.remote_addr
    since = db.utc_ago(REACT_RATE_LIMIT_WINDOW_SECONDS)
    return db.count_rate_limit_events(ip, "react", since) < REACT_RATE_LIMIT_MAX


@bp.route("/response/<int:response_id>/react.json", methods=["POST"])
def react_json(response_id):
    resp = db.get_response(response_id)
    if resp is None:
        abort(404)
    get_approved_claim_or_404(resp["claim_id"])
    if not _react_allowed():
        return jsonify(ok=False, error="You're reacting a lot — take a breather."), 429
    voter_id = get_voter_id() or str(uuid.uuid4())
    reacted, count = db.toggle_reaction(response_id, voter_id)
    db.record_rate_limit_event(request.remote_addr, "react")
    payload = jsonify(ok=True, reacted=reacted, count=count)
    return set_voter_cookie(payload, voter_id)


@bp.route("/response/<int:response_id>/react", methods=["POST"])
def react(response_id):
    """No-JS fallback: toggle the reaction and redirect back to the debate."""
    resp = db.get_response(response_id)
    if resp is None:
        abort(404)
    get_approved_claim_or_404(resp["claim_id"])
    voter_id = get_voter_id()
    if _react_allowed():
        voter_id = voter_id or str(uuid.uuid4())
        db.toggle_reaction(response_id, voter_id)
        db.record_rate_limit_event(request.remote_addr, "react")
    redirect_resp = redirect(url_for("main.claim_detail", claim_id=resp["claim_id"]) + "#debate")
    if voter_id:
        set_voter_cookie(redirect_resp, voter_id)
    return redirect_resp


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


@bp.route("/admin/stats")
def admin_stats():
    token = require_admin()
    day = db.utc_ago(24 * 60 * 60)
    week = db.utc_ago(7 * 24 * 60 * 60)
    our_host = (urlparse(request.url_root).hostname or "").lower()
    return render_template(
        "stats.html",
        token=token,
        views_total=db.count_page_views(),
        views_24h=db.count_page_views(since=day),
        views_7d=db.count_page_views(since=week),
        top_claims=db.top_claims_by_views(limit=8, since=week),
        top_referrers=db.top_referrers(limit=8, since=week, exclude_host=our_host),
        claims_approved=len(db.get_claims()),
        claims_pending=db.get_pending_count(),
        votes_total=db.count_votes(),
        responses_total=db.count_responses(),
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
