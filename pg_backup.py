#!/usr/bin/env python3
"""
PostgreSQL Google Drive Backup Script

This script:
1. Creates a backup of a PostgreSQL database
2. Compresses the backup
3. Uploads it to Google Drive
4. Manages retention by removing old backups (optional)
"""

import os
import sys
import time
import logging
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

# Google Drive API libraries
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/var/log/pg_backup.log')
    ]
)
logger = logging.getLogger(__name__)

# Google Drive API scopes
SCOPES = ['https://www.googleapis.com/auth/drive']

# Default backup settings
DEFAULT_BACKUP_DIR = "/backups"
DEFAULT_RETENTION_DAYS = 7
DEFAULT_GOOGLE_DRIVE_FOLDER_NAME = "postgres_backups"


def get_env_or_default(var_name, default=None, required=False):
    """Get environment variable or return default value."""
    value = os.environ.get(var_name, default)
    if required and not value:
        logger.error(f"Required environment variable {var_name} is not set")
        sys.exit(1)
    return value


def create_postgres_backup(backup_file_path):
    """Create a PostgreSQL backup using pg_dump."""
    # Get PostgreSQL connection details from environment variables
    pg_host = get_env_or_default("PGHOST", required=True)
    pg_port = get_env_or_default("PGPORT", "5432")
    pg_user = get_env_or_default("PGUSER", required=True)
    pg_password = get_env_or_default("PGPASSWORD", required=True)
    pg_database = get_env_or_default("PGDATABASE", required=True)
    
    # Set PGPASSWORD environment variable for pg_dump
    backup_env = os.environ.copy()
    backup_env["PGPASSWORD"] = pg_password
    
    # Build pg_dump command
    cmd = [
        "pg_dump",
        "-h", pg_host,
        "-p", pg_port,
        "-U", pg_user,
        "-F", "c",  # Custom format (compressed)
        "-b",       # Include large objects
        "-v",       # Verbose
        "-f", backup_file_path,
        pg_database
    ]
    
    logger.info(f"Creating PostgreSQL backup: {backup_file_path}")
    
    try:
        process = subprocess.run(
            cmd,
            env=backup_env,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        logger.info(f"Backup completed successfully: {process.stdout}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Backup failed: {e.stderr}")
        return False


def authenticate_google_drive():
    """Authenticate with Google Drive API."""
    creds = None
    token_path = Path('/secrets/token.json')
    credentials_path = Path('/secrets/credentials.json')
    
    # Check if token file exists
    if token_path.exists():
        creds = Credentials.from_authorized_user_info(
            json.loads(token_path.read_text()), SCOPES)
    
    # If credentials don't exist or are invalid, refresh or get new ones
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_path.exists():
                logger.error("credentials.json not found. "
                            "Please provide Google Drive API credentials.")
                sys.exit(1)
            
            flow = InstalledAppFlow.from_client_secrets_file(
                credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        
        # Save the credentials for the next run
        token_path.write_text(creds.to_json())
    
    return build('drive', 'v3', credentials=creds)


def get_or_create_folder(service, folder_name):
    """Get or create a folder in Google Drive."""
    # Check if folder already exists
    response = service.files().list(
        q=f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
        spaces='drive',
        fields='files(id, name)'
    ).execute()
    
    folders = response.get('files', [])
    
    if folders:
        logger.info(f"Found existing folder: {folder_name}")
        return folders[0]['id']
    
    # Create folder if it doesn't exist
    logger.info(f"Creating new folder: {folder_name}")
    file_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder'
    }
    
    folder = service.files().create(
        body=file_metadata, fields='id'
    ).execute()
    
    return folder.get('id')


def upload_to_google_drive(service, file_path, folder_id):
    """Upload a file to Google Drive."""
    file_name = os.path.basename(file_path)
    logger.info(f"Uploading {file_name} to Google Drive")
    
    file_metadata = {
        'name': file_name,
        'parents': [folder_id]
    }
    
    media = MediaFileUpload(
        file_path,
        resumable=True
    )
    
    try:
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()
        
        logger.info(f"Upload successful, file ID: {file.get('id')}")
        return True
    except Exception as e:
        logger.error(f"Upload failed: {str(e)}")
        return False


def delete_old_backups_local(backup_dir, retention_days):
    """Delete local backups older than retention_days."""
    cutoff_date = datetime.now() - timedelta(days=retention_days)
    
    try:
        for item in os.listdir(backup_dir):
            item_path = os.path.join(backup_dir, item)
            if os.path.isfile(item_path):
                file_time = datetime.fromtimestamp(os.path.getmtime(item_path))
                if file_time < cutoff_date:
                    logger.info(f"Deleting old backup: {item}")
                    os.remove(item_path)
    except Exception as e:
        logger.error(f"Error deleting old backups: {str(e)}")


def delete_old_backups_gdrive(service, folder_id, retention_days):
    """Delete backups from Google Drive older than retention_days."""
    cutoff_date = datetime.now() - timedelta(days=retention_days)
    cutoff_timestamp = cutoff_date.strftime('%Y-%m-%dT%H:%M:%S')
    
    try:
        # List files in the backup folder
        response = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false and createdTime < '{cutoff_timestamp}'",
            spaces='drive',
            fields='files(id, name, createdTime)'
        ).execute()
        
        files = response.get('files', [])
        
        for file in files:
            logger.info(f"Deleting old backup from Google Drive: {file['name']}")
            service.files().delete(fileId=file['id']).execute()
    
    except Exception as e:
        logger.error(f"Error deleting old backups from Google Drive: {str(e)}")


def main():
    """Main function to orchestrate the backup process."""
    # Get configuration from environment variables
    backup_dir = get_env_or_default("BACKUP_DIR", DEFAULT_BACKUP_DIR)
    retention_days = int(get_env_or_default("RETENTION_DAYS", DEFAULT_RETENTION_DAYS))
    gdrive_folder = get_env_or_default("GDRIVE_FOLDER", DEFAULT_GOOGLE_DRIVE_FOLDER_NAME)
    
    # Create backup directory if it doesn't exist
    os.makedirs(backup_dir, exist_ok=True)
    
    # Create timestamped backup filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"postgres_backup_{timestamp}.dump"
    backup_path = os.path.join(backup_dir, backup_filename)
    
    # Step 1: Create PostgreSQL backup
    if not create_postgres_backup(backup_path):
        logger.error("Backup creation failed. Exiting.")
        sys.exit(1)
    
    # Step 2: Authenticate with Google Drive
    try:
        service = authenticate_google_drive()
    except Exception as e:
        logger.error(f"Google Drive authentication failed: {str(e)}")
        sys.exit(1)
    
    # Step 3: Get or create folder in Google Drive
    try:
        folder_id = get_or_create_folder(service, gdrive_folder)
    except Exception as e:
        logger.error(f"Failed to get/create Google Drive folder: {str(e)}")
        sys.exit(1)
    
    # Step 4: Upload backup to Google Drive
    if not upload_to_google_drive(service, backup_path, folder_id):
        logger.error("Upload to Google Drive failed.")
        sys.exit(1)
    
    # Step 5: Clean up old backups (both local and on Google Drive)
    if retention_days > 0:
        delete_old_backups_local(backup_dir, retention_days)
        delete_old_backups_gdrive(service, folder_id, retention_days)
    
    logger.info("Backup process completed successfully")


if __name__ == "__main__":
    import json
    main()