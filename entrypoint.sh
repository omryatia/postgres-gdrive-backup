#!/bin/bash
set -e

# Check if GOOGLE_CREDENTIALS environment variable exists
if [ -z "$GOOGLE_CREDENTIALS" ]; then
    echo "ERROR: GOOGLE_CREDENTIALS environment variable is not set!"
    echo "Please set the GOOGLE_CREDENTIALS environment variable with the contents of your credentials.json file"
    exit 1
fi

# Check if custom cron schedule is defined
if [ -n "$CRON_SCHEDULE" ]; then
    echo "Using custom cron schedule: $CRON_SCHEDULE"
    echo "$CRON_SCHEDULE /usr/local/bin/python /app/pg_backup.py >> /proc/1/fd/1 2>&1" > /etc/cron.d/pg-backup
    chmod 0644 /etc/cron.d/pg-backup
    crontab /etc/cron.d/pg-backup
fi

# Run initial backup if requested
if [ "$RUN_ON_STARTUP" = "true" ]; then
    echo "Running initial backup..."
    python /app/pg_backup.py
    
    # Check if GOOGLE_TOKEN was set during the initial run
    if [ -n "$GOOGLE_TOKEN" ]; then
        echo "Token successfully generated. Please add this token as GOOGLE_TOKEN environment variable in Railway to avoid authentication prompts in the future."
    fi
fi

# Start cron service
echo "Starting cron service..."
cron -f