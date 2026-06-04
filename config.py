import os


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "clave_segura_provisional")
    DATABASE_URL = os.getenv("DATABASE_URL")
