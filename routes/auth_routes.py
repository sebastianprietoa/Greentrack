from flask import Blueprint, jsonify, render_template, request, redirect, flash, session
import psycopg2
import psycopg2.extras

from db import get_db
from auth_utils import create_api_token, verify_password

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/", methods=["GET", "POST"])
def inicio():
    if "user_id" in session:
        return redirect("/admin/dashboard" if session.get("es_admin") == 1 else "/dashboard")

    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id, empresa, password, es_admin FROM usuarios WHERE email = %s", (email,))
        user = cursor.fetchone()
        conn.close()

        if user and verify_password(password, user[2]):
            session["user_id"] = user[0]
            session["empresa"] = user[1]
            session["es_admin"] = user[3]
            return redirect("/admin/dashboard" if user[3] == 1 else "/dashboard")

        flash("Credenciales incorrectas", "error")

    return render_template("login.html")


@auth_bp.route("/api/login", methods=["POST"])
def api_login():
    payload = request.get_json(silent=True) or request.form
    email = (payload.get("email") or "").strip()
    password = payload.get("password") or ""

    if not email or not password:
        return jsonify({"error": "email y password son obligatorios"}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, empresa, password, es_admin FROM usuarios WHERE email = %s", (email,))
    user = cursor.fetchone()
    conn.close()

    if not user or not verify_password(password, user[2]):
        return jsonify({"error": "Credenciales incorrectas"}), 401

    token, expires_in = create_api_token(user[0], user[1], user[3], email)
    return jsonify({
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "user": {
            "id": user[0],
            "empresa": user[1],
            "email": email,
            "es_admin": int(user[3] or 0),
        },
    })


@auth_bp.route("/logout")
def logout():
    session.clear()
    flash("Sesión cerrada", "info")
    return redirect("/")
