from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from flask import Flask, render_template


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "student_agenda_page_data.json"


def create_app() -> Flask:
    app = Flask(
        __name__,
        static_folder=str(ROOT / "ui" / "static"),
        template_folder=str(ROOT / "ui" / "templates"),
    )
    page_data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))

    @app.get("/")
    def student_agenda_preview() -> str:
        return render_template(
            "dashboard.html",
            page_data=page_data,
            page_title=page_data["title"],
        )

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve fictional student agenda UI")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    create_app().run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
