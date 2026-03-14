#!/usr/bin/env python3
"""Pi Archiver - Main entry point."""

import os

from flask import Flask, render_template
from flask_compress import Compress

from app.routes import api


def create_app():
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "templates"),
        static_folder=os.path.join(os.path.dirname(__file__), "static"),
    )

    Compress(app)
    app.config["COMPRESS_MIMETYPES"] = [
        "text/html", "text/css", "application/javascript", "application/json",
    ]
    app.config["COMPRESS_MIN_SIZE"] = 500

    app.register_blueprint(api)

    @app.route("/")
    def index():
        return render_template("index.html")

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=os.environ.get("DEBUG", False))
