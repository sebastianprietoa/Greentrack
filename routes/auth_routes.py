from flask import Blueprint, render_template, request, redirect, flash, session
import psycopg2
import psycopg2.extras

from db import get_db
from auth_utils import verify_password

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


@auth_bp.route("/logout")
def logout():
    session.clear()
    flash("Sesión cerrada", "info")
    return redirect("/")
