import sqlite3
from pathlib import Path
from dbml_sqlite import toSQLite

# Get the directory where this script is located
script_dir = Path(__file__).parent.resolve()

# Construct paths relative to the script's directory
dbml_file = script_dir / 'dbdiagram.dbml'
db_file = script_dir / 'students.db'

ddl = toSQLite(str(dbml_file))
assert isinstance(ddl, str)
con = sqlite3.connect(str(db_file))
with con:
    con.executescript(ddl)
con.close()
