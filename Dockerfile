FROM python:3.13-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Add PostgreSQL repository and install PostgreSQL client tools for multiple versions
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    lsb-release \
    gnupg \
    curl \
    cron \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /usr/share/keyrings/postgresql-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/postgresql-keyring.gpg] http://apt.postgresql.org/pub/repos/apt/ $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
    postgresql-client-16 \
    postgresql-client-15 \
    postgresql-client-14 \
    postgresql-client-13 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the backup script and entrypoint
COPY pg_backup.py .
COPY entrypoint.sh .
COPY version_detect.sh .
RUN chmod +x /app/pg_backup.py /app/entrypoint.sh /app/version_detect.sh

# Set up default cron job (daily at 2:00 AM)
RUN echo "0 2 * * * /app/version_detect.sh >> /proc/1/fd/1 2>&1" > /etc/cron.d/pg-backup && \
    chmod 0644 /etc/cron.d/pg-backup && \
    crontab /etc/cron.d/pg-backup

# Start cron service (which will run the backup script based on schedule)
ENTRYPOINT ["/app/entrypoint.sh"]