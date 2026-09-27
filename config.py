import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_DIR = os.path.join(BASE_DIR, "database")
DB_PATH = os.path.join(DATABASE_DIR, "pow_os.db")

SECRET_KEY = os.getenv("SECRET_KEY", "pow_os_super_secure_key_2026")