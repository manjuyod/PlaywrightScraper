from __future__ import annotations

from dotenv import load_dotenv
from flask import Flask, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

from ui.dashboard_data import DashboardDataError


load_dotenv()

app = Flask(__name__, static_folder="static", template_folder="templates")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


@app.after_request
def protect_dashboard_response(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.errorhandler(DashboardDataError)
def dashboard_data_unavailable(_error: DashboardDataError):
    return render_template("unavailable.html"), 503
