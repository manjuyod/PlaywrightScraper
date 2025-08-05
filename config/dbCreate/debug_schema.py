# config/dbCreate/debug_schema.py
import sqlite3
import pathlib

DB_PATH = pathlib.Path(__file__).parent.parent / "students.db"

def get_table_schema(db_path, table_name):
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(f"PRAGMA table_info({table_name})")
            rows = cursor.fetchall()
            if not rows:
                print(f"Table '{table_name}' not found or is empty.")
                return
            
            print(f"Schema for table '{table_name}':")
            print("CID | Name | Type | NotNull | DefaultValue | PK")
            print("--- | ---- | ---- | ------- | ------------ | --")
            for row in rows:
                # Handle potential None values for printing
                printable_row = [str(v) if v is not None else 'None' for v in row]
                print(f"{printable_row[0]:<3} | {printable_row[1]:<4} | {printable_row[2]:<4} | {printable_row[3]:<7} | {printable_row[4]:<12} | {printable_row[5]}")

    except sqlite3.Error as e:
        print(f"Database error: {e}")

if __name__ == "__main__":
    get_table_schema(DB_PATH, "Student")
