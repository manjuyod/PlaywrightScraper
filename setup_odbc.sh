#!/usr/bin/env bash
set -e

ODBC_DIR="$HOME/.odbc"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_DIR="$SCRIPT_DIR/odbc_driver"

echo "Setting up Microsoft ODBC Driver 17 for SQL Server..."

DRIVER_LIB=$(ls "$BUNDLE_DIR"/libmsodbcsql-17*.so* 2>/dev/null | head -1)

if [ -z "$DRIVER_LIB" ] || [ ! -f "$DRIVER_LIB" ]; then
  echo "ERROR: Driver bundle not found at $BUNDLE_DIR" >&2
  echo "  Make sure the build step ran successfully." >&2
  exit 1
fi

echo "Driver library: $DRIVER_LIB"

mkdir -p "$ODBC_DIR"
cat > "$ODBC_DIR/odbcinst.ini" <<EOF
[ODBC Driver 17 for SQL Server]
Description=Microsoft ODBC Driver 17 for SQL Server
Driver=$DRIVER_LIB
UsageCount=1
EOF

echo "ODBC driver registered at $ODBC_DIR/odbcinst.ini"
ODBCSYSINI="$ODBC_DIR" odbcinst -q -d
