"""
Module for tracking the scraper status and info
i.e. Student Status (errored, synced, no grades)
"""
from scraper.runner import db_conn, DictCursor

def state():
    with db_conn() as conn:
        cur = conn.cursor(cursor_factory=DictCursor)
        cur.execute("""
                    select (id, franchiseid, firstname, portal1, portal, status) 
                    from student 
                    where status != 'synced' and status != 'never'
                    """)
        rows = cur.fetchall()
        for student in rows:
            print(dict(student))

if __name__ == '__main__':
    state()