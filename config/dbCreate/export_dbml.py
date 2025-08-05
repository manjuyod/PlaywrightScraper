#!/usr/bin/env python3
"""
Export DBML from a SQLite database.

Usage:
  # Default: looks for ../students.db relative to this script
  python export_dbml.py > schema.dbml

  # Or specify a path explicitly
  python export_dbml.py path/to/db.sqlite > schema.dbml
"""

import sys
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

# ---------- Helpers ----------

def qident(name: str) -> str:
    """Quote identifier for DBML when needed."""
    return f'`{name}`' if re.search(r'[^A-Za-z0-9_]', name) else name

def map_type(t: str) -> str:
    """Map SQLite types to common DBML-ish names (best-effort)."""
    if not t:
        return "text"
    u = t.upper()
    if "INT" in u:
        return "integer"
    if "CHAR" in u or "CLOB" in u or "TEXT" in u:
        return "text"
    if "BLOB" in u:
        return "blob"
    if "REAL" in u or "FLOA" in u or "DOUB" in u:
        return "real"
    if "NUMERIC" in u or "DEC" in u:
        return "numeric"
    return t  # fallback: keep original

def parse_autoincrement(table_sql: str) -> bool:
    return bool(table_sql and "AUTOINCREMENT" in table_sql.upper())

def fmt_default(dflt):
    """Return DBML default clause or ''."""
    if dflt is None:
        return ""
    return f" [default: {dflt}]"

# ---------- Core ----------

def export_dbml(db_path: str) -> str:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Tables (ignore sqlite_*)
    cur.execute("""
        SELECT name, sql
        FROM sqlite_master
        WHERE type='table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
    """)
    tables = cur.fetchall()

    out = []
    all_refs = []  # (from_table, [from_cols], to_table, [to_cols])

    for t in tables:
        tname = t["name"]
        t_sql = t["sql"] or ""
        autoinc_in_table = parse_autoincrement(t_sql)

        cur.execute(f"PRAGMA table_info({qident(tname)})")
        cols = cur.fetchall()

        pk_cols = [c["name"] for c in sorted(cols, key=lambda r: (r["pk"] or 0)) if c["pk"]]

        # unique indexes (excluding PK)
        cur.execute(f"PRAGMA index_list({qident(tname)})")
        idx_list = cur.fetchall()
        unique_indexes = []
        for idx in idx_list:
            if idx["unique"] == 1 and idx["origin"] != "pk":
                cur.execute(f"PRAGMA index_info({qident(idx['name'])})")
                idx_cols = [r["name"] for r in cur.fetchall()]
                unique_indexes.append((idx["name"], idx_cols))

        # foreign keys
        cur.execute(f"PRAGMA foreign_key_list({qident(tname)})")
        fk_rows = cur.fetchall()
        fks = defaultdict(lambda: {"to_table": None, "pairs": [], "on_update": None, "on_delete": None})
        for r in fk_rows:
            fid = r["id"]
            fks[fid]["to_table"] = r["table"]
            fks[fid]["pairs"].append((r["from"], r["to"]))
            fks[fid]["on_update"] = r["on_update"]
            fks[fid]["on_delete"] = r["on_delete"]
        for fk in fks.values():
            from_cols = [a for a, _ in fk["pairs"]]
            to_cols = [b for _, b in fk["pairs"]]
            all_refs.append((tname, from_cols, fk["to_table"], to_cols))

        out.append(f"Table {qident(tname)} {{")

        unique_single_cols = {cols[0] for _, cols in unique_indexes if len(cols) == 1}
        for c in cols:
            cname = c["name"]
            ctype = map_type(c["type"])
            notnull = c["notnull"] == 1
            dflt = c["dflt_value"]
            is_pk = c["pk"] == 1
            attrs = []

            if is_pk and len(pk_cols) == 1:
                attrs.append("pk")
                if c["type"] and c["type"].upper() == "INTEGER" and autoinc_in_table:
                    attrs.append("increment")
            if notnull and not is_pk:
                attrs.append("not null")
            if cname in unique_single_cols:
                attrs.append("unique")

            # single-col FK hint
            fk_hint = ""
            single_fk_targets = [
                (to_table, to_cols[0])
                for (ft, fcols, to_table, to_cols) in all_refs
                if ft == tname and len(fcols) == 1 and fcols[0] == cname
            ]
            if single_fk_targets:
                to_table, to_col = single_fk_targets[0]
                fk_hint = f" [ref: > {qident(to_table)}.{qident(to_col)}]"

            dflt_clause = fmt_default(dflt)
            attr_clause = f" [{', '.join(attrs)}]" if attrs else ""
            out.append(f"  {qident(cname)} {ctype}{attr_clause}{dflt_clause}{fk_hint}")

        # Indexes block for composite PK/unique
        idx_lines = []
        if len(pk_cols) > 1:
            cols_list = ", ".join(qident(c) for c in pk_cols)
            idx_lines.append(f"  ({cols_list}) [pk]")
        for idx_name, idx_cols in unique_indexes:
            if len(idx_cols) == 1 and idx_cols[0] not in pk_cols:
                continue
            cols_list = ", ".join(qident(c) for c in idx_cols)
            idx_lines.append(f'  ({cols_list}) [name: "{idx_name}", unique]')

        if idx_lines:
            out.append("  Indexes {")
            out.extend(idx_lines)
            out.append("  }")

        out.append("}\n")

    if all_refs:
        out.append("// Relationships")
        for (ftable, fcols, ttable, tcols) in all_refs:
            if len(fcols) == 1 and len(tcols) == 1:
                out.append(f"Ref: {qident(ftable)}.{qident(fcols[0])} > {qident(ttable)}.{qident(tcols[0])}")
            else:
                left = ", ".join(qident(c) for c in fcols)
                right = ", ".join(qident(c) for c in tcols)
                out.append(f"Ref: {qident(ftable)}.[{left}] > {qident(ttable)}.[{right}]")

    return "\n".join(out)

# ---------- CLI ----------

def main():
    script_dir = Path(__file__).resolve().parent
    default_db = (script_dir.parent / "students.db").resolve()

    # If a path is provided, use it. Otherwise, default to ../students.db
    if len(sys.argv) == 1:
        db_path = default_db
    elif len(sys.argv) == 2:
        db_path = Path(sys.argv[1]).resolve()
    else:
        print("Usage: python export_dbml.py [path/to/db.sqlite] > schema.dbml", file=sys.stderr)
        sys.exit(1)

    if not db_path.exists():
        print(f"File not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    print(export_dbml(str(db_path)))

if __name__ == "__main__":
    main()