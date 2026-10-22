import os

# Tests never touch the real database: force SQLite before the app is imported.
os.environ["DATABASE_URL"] = "sqlite://"
