FROM python:3.13-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install PostgreSQL client tools and other dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    postgresql-client \
    cron \
    && rm -rf /var/lib/apt/lists/*

# Create directories for backups, secrets, and logs
RUN mkdir -p /backups /secrets /var/log && \
    touch /var/log/pg_backup.log && \
    chmod 777 /var/log/pg_backup.log

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the backup script
COPY pg_backup.py .
RUN chmod +x pg_backup.py

# Set up default cron job (daily at 2:00 AM)
RUN echo "0 2 * * * /usr/local/bin/python /app/pg_backup.py >> /var/log/pg_backup.log 2>&1" > /etc/cron.d/pg-backup && \
    chmod 0644 /etc/cron.d/pg-backup && \
    crontab /etc/cron.d/pg-backup

# Copy entrypoint script
COPY entrypoint.sh .
RUN chmod +x /app/entrypoint.sh

# Volume for persistent data
VOLUME ["/backups", "/secrets"]

# Start cron service (which will run the backup script based on schedule)
ENTRYPOINT ["/app/entrypoint.sh"]