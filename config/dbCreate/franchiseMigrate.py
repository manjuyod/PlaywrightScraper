import os
import sys
from pathlib import Path
from sqlalchemy import create_engine, Column, Integer, String, text
from sqlalchemy.orm import declarative_base
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

# -----------------------
# Config / Engines
# -----------------------
script_dir = Path(__file__).parent.resolve()
db_file = script_dir.parent / "students.db"
sqlite_db_url = f"sqlite:///{db_file}"

# Env vars for CRM (SQL Server)
server = os.environ['CRMSrvAddress']
database = os.environ['CRMSrvDb']
username = os.environ['CRMSrvUs']
password = os.environ['CRMSrvPs']
0
if not all([server, database, username, password]):
    print("Missing env vars: CRMSrvAddress, CRMSrvDb, CRMSrvUs, CRMSrvPs")
    sys.exit(1)

# If your password has special characters, prefer odbc_connect form.
crm_db_url = (
    f"mssql+pyodbc://{username}:{password}@{server}/{database}?driver=SQL+Server"
)

try:
    crm_engine = create_engine(crm_db_url, pool_pre_ping=True, future=True)
    sqlite_engine = create_engine(sqlite_db_url, future=True)
except SQLAlchemyError as e:
    print(f"Engine creation failed: {e}")
    sys.exit(1)

# Optional SQLite pragmas for faster ingest (safe defaults)
with sqlite_engine.connect() as conn:
    conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    conn.exec_driver_sql("PRAGMA synchronous=NORMAL")
    conn.exec_driver_sql("PRAGMA temp_store=MEMORY")
    conn.exec_driver_sql("PRAGMA foreign_keys=ON")

# -----------------------
# Schema (SQLite target)
# -----------------------
Base = declarative_base()

class Franchise(Base):
    __tablename__ = "Franchise"
    ID = Column(Integer, primary_key=True)
    FranchiseName = Column(String, nullable=False)
    PrimaryPhone = Column(String, nullable=False)
    SecondaryPhone = Column(String)
    FranchiseEmail = Column(String, nullable=False)
    FranchiseAddress = Column(String, nullable=False)

# Ensure table exists
Base.metadata.create_all(sqlite_engine)

# -----------------------
# Streaming migration
# -----------------------
BATCH_SIZE = 1000

def migrate_franchises_streaming():
    """
    Stream rows from SQL Server and upsert into SQLite in batches.
    """
    print("\nStarting franchise data migration (streaming)...")
    crm_query = text("""
        SELECT ID, FranchiesName, PrimaryPhone, SecondryPhone, FranchiesEmail, FranchiesAddress
        FROM tblFranchies
    """)

    migrated = 0
    try:
        with crm_engine.connect() as crm_conn, sqlite_engine.connect() as sconn:
            # STREAM results from SQL Server
            result = crm_conn.execution_options(stream_results=True).execute(crm_query)

            # Begin first batch transaction
            trans = sconn.begin()
            try:
                for row in result.mappings():  # dict-like row access
                    stmt = (
                        sqlite_insert(Franchise.__table__)
                        .values(
                            ID=row["ID"],
                            FranchiseName=row["FranchiesName"],
                            PrimaryPhone=row["PrimaryPhone"],
                            SecondaryPhone=row["SecondryPhone"],
                            FranchiseEmail=row["FranchiesEmail"],
                            FranchiseAddress=row["FranchiesAddress"],
                        )
                        .on_conflict_do_update(
                            index_elements=[Franchise.__table__.c.ID],
                            set_={
                                "FranchiseName":   row["FranchiesName"],
                                "PrimaryPhone":    row["PrimaryPhone"],
                                "SecondaryPhone":  row["SecondryPhone"],
                                "FranchiseEmail":  row["FranchiesEmail"],
                                "FranchiseAddress":row["FranchiesAddress"],
                            },
                        )
                    )
                    sconn.execute(stmt)
                    migrated += 1

                    if migrated % BATCH_SIZE == 0:
                        trans.commit()        # flush this batch
                        trans = sconn.begin() # start next batch

                trans.commit()  # final batch
            except:
                trans.rollback()
                raise

        print(f"Successfully migrated {migrated} records (streamed).")
    except Exception as e:
        print(f"Migration error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    migrate_franchises_streaming()
