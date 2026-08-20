import os
import sys
import subprocess
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import text
from app.db.session import engine

print("Connecting to database to drop all public tables...")
with engine.begin() as conn:
    rows = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")).fetchall()
    for (tbl,) in rows:
        conn.execute(text(f'DROP TABLE IF EXISTS public."{tbl}" CASCADE'))
        print(f"Dropped: {tbl}")

print("\nRunning Alembic migration to create clean schema...")
res = subprocess.run(["python", "-m", "alembic", "upgrade", "head"], capture_output=True, text=True)
print(res.stdout)
if res.stderr:
    print(res.stderr)
if res.returncode == 0:
    print("\nDATABASE RESET COMPLETE: All tables created cleanly from scratch!")
else:
    print("\nAlembic migration failed.")
