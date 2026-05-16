#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE="$SCRIPT_DIR/odbc_driver"
RESOURCES="$SCRIPT_DIR/share/resources/en_US"

# If a complete bundle already exists (driver + bundled deps + resources), skip rebuild.
BUNDLED_LIBS=$(ls "$BUNDLE"/libmsodbcsql-17*.so* 2>/dev/null | head -1)
EXTRA_LIBS=$(ls "$BUNDLE"/libkrb5*.so* 2>/dev/null | head -1)
RESOURCE_FILE="$RESOURCES/msodbcsqlr17.rll"

if [ -n "$BUNDLED_LIBS" ] && [ -n "$EXTRA_LIBS" ] && [ -f "$RESOURCE_FILE" ]; then
    echo "ODBC driver bundle already complete at $BUNDLE, skipping rebuild."
    exit 0
fi

echo "Building self-contained ODBC driver bundle at $BUNDLE..."

if ! command -v nix-env &>/dev/null; then
    echo "ERROR: nix-env not available and no complete bundle found." >&2
    echo "  Please run setup_odbc_build.sh in the dev environment first." >&2
    exit 1
fi

echo "Installing msodbcsql17 via nix-env..."
NIXPKGS_ALLOW_UNFREE=1 nix-env -iA nixpkgs.unixODBCDrivers.msodbcsql17

NIX_DRV=$(ls "$HOME/.nix-profile/lib/libmsodbcsql-17"*.so* 2>/dev/null | head -1)
if [ -z "$NIX_DRV" ] || [ ! -f "$NIX_DRV" ]; then
    echo "ERROR: msodbcsql17 driver not found after nix-env install." >&2
    exit 1
fi

echo "Source driver: $NIX_DRV"

rm -rf "$BUNDLE"
mkdir -p "$BUNDLE"

cp -f "$NIX_DRV" "$BUNDLE/"
BUNDLE_DRV="$BUNDLE/$(basename "$NIX_DRV")"
chmod u+w "$BUNDLE_DRV"

echo "Bundling non-glibc dependencies (excluding libodbcinst - use system unixODBC)..."
ldd "$NIX_DRV" 2>/dev/null \
    | grep "=> /" \
    | awk '{print $3}' \
    | grep -v glibc \
    | grep -v libodbcinst \
    | while read -r src; do
        dest="$BUNDLE/$(basename "$src")"
        cp -f "$src" "$dest"
        chmod u+w "$dest"
        echo "  + $(basename "$src")"
    done

# Bundle OpenSSL 1.1 explicitly - the driver dlopen()s it at runtime (not in ldd)
# Find the openssl-1.1 path from the driver's original RPATH.
ORIG_RPATH=$(patchelf --print-rpath "$BUNDLE_DRV" 2>/dev/null || true)
OPENSSL_PATH=$(echo "$ORIG_RPATH" | tr ':' '\n' | grep openssl | head -1)
if [ -z "$OPENSSL_PATH" ]; then
    # Fallback: search nix store for openssl 1.1.
    OPENSSL_PATH=$(ls -d /nix/store/*openssl-1.1*/lib 2>/dev/null | head -1)
fi
if [ -n "$OPENSSL_PATH" ] && [ -f "$OPENSSL_PATH/libssl.so.1.1" ]; then
    cp -f "$OPENSSL_PATH/libssl.so.1.1" "$BUNDLE/"
    cp -f "$OPENSSL_PATH/libcrypto.so.1.1" "$BUNDLE/"
    chmod u+w "$BUNDLE/libssl.so.1.1" "$BUNDLE/libcrypto.so.1.1"
    echo "  + libssl.so.1.1 (OpenSSL 1.1 for runtime dlopen)"
    echo "  + libcrypto.so.1.1"
else
    echo "WARNING: OpenSSL 1.1 not found - driver may fail to initialize" >&2
fi

patchelf --set-rpath '$ORIGIN' "$BUNDLE_DRV"
echo "Patched RPATH to \$ORIGIN for main driver"

echo "Patching RPATH to \$ORIGIN for all bundled dependency libs..."
for dep in "$BUNDLE"/lib*.so*; do
    [ "$dep" = "$BUNDLE_DRV" ] && continue
    patchelf --set-rpath '$ORIGIN' "$dep" 2>/dev/null && echo "  Patched: $(basename "$dep")" || true
done

ldd "$BUNDLE_DRV" 2>&1 | grep "not found" && { echo "ERROR: unresolved deps!"; exit 1; } || echo "All deps resolved."

# Copy driver resource files (needed for SQLAllocHandle initialization).
# Driver looks for resources at $ORIGIN/../share/resources/en_US/.
mkdir -p "$RESOURCES"
NIX_RESOURCES="$HOME/.nix-profile/share/resources/en_US/msodbcsqlr17.rll"
if [ -f "$NIX_RESOURCES" ]; then
    cp -f "$NIX_RESOURCES" "$RESOURCES/"
    chmod u+w "$RESOURCES/msodbcsqlr17.rll"
    echo "  + msodbcsqlr17.rll (driver resource file)"
else
    echo "ERROR: driver resource file not found at $NIX_RESOURCES" >&2
    exit 1
fi

echo "Bundle complete at $BUNDLE"
ls -lh "$BUNDLE/"
echo "Resources at $RESOURCES"
ls -lh "$RESOURCES/"
