import ui.auth as auth
import pyodbc

def test_db_connection():
    connection = None
    try:
        connection = pyodbc.connect(auth._connect_string())
        print("Database connection successful.")
    except Exception as e:
        print(f"Database connection failed: {e}")
    finally:
        if 'connection' in locals() and connection:
            connection.close()
            print("Database connection closed.")

if __name__ == "__main__":
    test_db_connection()    