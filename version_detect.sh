#!/bin/bash
set -e

# Get PostgreSQL connection details from environment variables
PG_HOST="${PGHOST:-localhost}"
PG_PORT="${PGPORT:-5432}"
PG_USER="${PGUSER:-postgres}"
PG_PASSWORD="${PGPASSWORD}"
PG_DATABASE="${PGDATABASE:-postgres}"

echo "Detecting PostgreSQL server version..."

# Export password for psql
export PGPASSWORD="$PG_PASSWORD"

# Try to detect PostgreSQL version
PG_VERSION=$(psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DATABASE" -t -c "SELECT substring(version() from 'PostgreSQL\\s([0-9]+)') AS version;" 2>/dev/null || echo "detection_failed")

# Check if detection succeeded
if [ "$PG_VERSION" = "detection_failed" ]; then
    echo "Failed to detect PostgreSQL version. Trying alternative methods..."
    
    # Try with different client versions
    for VERSION in 16 15 14 13; do
        echo "Trying with PostgreSQL $VERSION client..."
        if command -v "psql-$VERSION" >/dev/null 2>&1; then
            PG_VERSION=$(psql-$VERSION -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DATABASE" -t -c "SELECT substring(version() from 'PostgreSQL\\s([0-9]+)') AS version;" 2>/dev/null || echo "detection_failed")
            if [ "$PG_VERSION" != "detection_failed" ]; then
                echo "Successfully connected using PostgreSQL $VERSION client."
                break
            fi
        else
            # Try with pg_dump path
            PG_VERSION=$(pg_dump-$VERSION --version 2>/dev/null | grep -oP 'PostgreSQL\s+\K\d+' || echo "detection_failed")
            if [ "$PG_VERSION" != "detection_failed" ]; then
                echo "Found PostgreSQL $VERSION client tools."
                break
            fi
        fi
    done
fi

# If version detection still failed, default to latest
if [ "$PG_VERSION" = "detection_failed" ]; then
    echo "Warning: Could not detect PostgreSQL version. Defaulting to PostgreSQL 16."
    PG_VERSION="16"
fi

# Trim whitespace
PG_VERSION=$(echo "$PG_VERSION" | tr -d '[:space:]')
echo "Detected PostgreSQL server version: $PG_VERSION"

# Set the appropriate pg_dump path based on detected version
if command -v "pg_dump-$PG_VERSION" >/dev/null 2>&1; then
    PG_DUMP_CMD="pg_dump-$PG_VERSION"
else
    # For Debian/Ubuntu where the binary might be in a versioned directory
    if [ -x "/usr/lib/postgresql/$PG_VERSION/bin/pg_dump" ]; then
        PG_DUMP_CMD="/usr/lib/postgresql/$PG_VERSION/bin/pg_dump"
    else
        echo "Warning: pg_dump for PostgreSQL $PG_VERSION not found. Defaulting to standard pg_dump."
        PG_DUMP_CMD="pg_dump"
    fi
fi

echo "Using pg_dump command: $PG_DUMP_CMD"

# Set environment variable for the backup script to use
export PG_DUMP_CMD

# Run the backup script
python /app/pg_backup.py