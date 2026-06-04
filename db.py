import os

import psycopg2


def get_db():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError("Error CRITICO: No se encontro la variable DATABASE_URL en el entorno.")
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(db_url, sslmode="require")
