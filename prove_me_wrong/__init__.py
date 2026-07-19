from pathlib import Path

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Config


def create_app(config_object=Config):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    database_path = Path(app.config["DATABASE_PATH"])
    if not database_path.is_absolute():
        database_path = Path.cwd() / database_path
        app.config["DATABASE_PATH"] = str(database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)

    from . import db

    db.init_app(app)

    with app.app_context():
        db.init_db()

    from .routes import bp

    app.register_blueprint(bp)

    return app
