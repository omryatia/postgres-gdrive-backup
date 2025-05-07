#!/bin/bash
set -e

# Create directories
mkdir -p /secrets

# Check if GOOGLE_CREDENTIALS environment variable exists
if [ -n "$GOOGLE_CREDENTIALS" ]; then
    echo "Found GOOGLE_CREDENTIALS environment variable"
    echo "$GOOGLE_CREDENTIALS" > /secrets/credentials.json
    echo "Saved credentials to /secrets/credentials.json"
fi

# Check if credentials exist
if [ ! -f "/secrets/credentials.json" ]; then
    echo "ERROR: No Google Drive credentials found!"
    echo "Please set the GOOGLE_CREDENTIALS environment variable with the contents of your credentials.json file"
    exit 1
fi

# Check if custom cron schedule is defined
if [ -n "$CRON_SCHEDULE" ]; then
    echo "Using custom cron schedule: $CRON_SCHEDULE"
    echo "$CRON_SCHEDULE /usr/local/bin/python /app/pg_backup.py >> /var/log/pg_backup.log 2>&1" > /etc/cron.d/pg-backup
    chmod 0644 /etc/cron.d/pg-backup
    crontab /etc/cron.d/pg-backup
fi

# Check if we need to run initial authentication
if [ ! -f "/secrets/token.json" ]; then
    echo "Running initial backup to authenticate with Google Drive..."
    python /app/pg_backup.py
fi

# Start cron service
echo "Starting cron service..."
cron -f