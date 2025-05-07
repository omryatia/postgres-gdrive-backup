#!/bin/bash
set -e

# Check if GOOGLE_SERVICE_ACCOUNT environment variable exists
if [ -z "$GOOGLE_SERVICE_ACCOUNT" ]; then
    echo "ERROR: GOOGLE_SERVICE_ACCOUNT environment variable is not set!"
    echo "Please set the GOOGLE_SERVICE_ACCOUNT environment variable with the contents of your service account JSON file"
    exit 1
fi

# Set up health check endpoint
cat > /app/health.html << 'EOF'
<!DOCTYPE html>
<html>
<head><title>PostgreSQL Backup Service</title></head>
<body>
  <h1>PostgreSQL Backup Service</h1>
  <p>Service is running correctly.</p>
</body>
</html>
EOF

# Start HTTP server for health checks
python -m http.server 8080 --directory /app &

# Check if custom cron schedule is defined
if [ -n "$CRON_SCHEDULE" ]; then
    echo "Using custom cron schedule: $CRON_SCHEDULE"
    echo "$CRON_SCHEDULE /app/version_detect.sh >> /proc/1/fd/1 2>&1" > /etc/cron.d/pg-backup
    chmod 0644 /etc/cron.d/pg-backup
    crontab /etc/cron.d/pg-backup
fi

# Run initial backup if requested
if [ "$RUN_ON_STARTUP" = "true" ]; then
    echo "Running initial backup..."
    /app/version_detect.sh
fi

# Start cron service
echo "Starting cron service..."
cron -f