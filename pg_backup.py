#!/usr/bin/env python3
"""
PostgreSQL Google Drive Backup Script with Service Account Authentication
and File Sharing to Personal Account

This script:
1. Creates a backup of a PostgreSQL database
2. Compresses the backup using tar.gz for smaller file size
3. Uploads it to Google Drive using a service account
4. Shares the uploaded file with your personal Google account
5. Manages retention by removing old backups from Google Drive
"""

import os
import sys
import time
import logging
import tempfile
import subprocess
import tarfile
from datetime import datetime, timedelta
from pathlib import Path
import json

# Google Drive API libraries
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Google Drive API scopes
SCOPES = ['https://www.googleapis.com/auth/drive']

# Default backup settings
DEFAULT_RETENTION_DAYS = 7
DEFAULT_GOOGLE_DRIVE_FOLDER_NAME = "postgres_backups"


def get_env_or_default(var_name, default=None, required=False):
    """Get environment variable or return default value."""
    value = os.environ.get(var_name, default)
    if required and not value:
        logger.error(f"Required environment variable {var_name} is not set")
        sys.exit(1)
    return value


def create_postgres_backup(temp_dir):
    """Create a PostgreSQL backup and compress it using tar.gz."""
    # Get PostgreSQL connection details from environment variables
    pg_host = get_env_or_default("PGHOST", required=True)
    pg_port = get_env_or_default("PGPORT", "5432")
    pg_user = get_env_or_default("PGUSER", required=True)
    pg_password = get_env_or_default("PGPASSWORD", required=True)
    pg_database = get_env_or_default("PGDATABASE", required=True)
    
    # Get pg_dump command from environment variable (set by version_detect.sh)
    pg_dump_cmd = get_env_or_default("PG_DUMP_CMD", "pg_dump")
    
    # Create timestamped filenames
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    plain_dump_filename = f"postgres_dump_{timestamp}.sql"
    plain_dump_path = os.path.join(temp_dir, plain_dump_filename)
    
    compressed_filename = f"postgres_backup_{timestamp}.tar.gz"
    compressed_path = os.path.join(temp_dir, compressed_filename)
    
    # Set PGPASSWORD environment variable for pg_dump
    backup_env = os.environ.copy()
    backup_env["PGPASSWORD"] = pg_password
    
    # Build pg_dump command for SQL output (plain text for better compression)
    cmd = [
        pg_dump_cmd,
        "-h", pg_host,
        "-p", pg_port,
        "-U", pg_user,
        "--format=plain",  # Plain text format compresses better
        "--no-owner",      # Skip ownership information
        "--no-privileges", # Skip privilege assignments
        "--no-tablespaces", # Skip tablespace assignments
        "-f", plain_dump_path,
        pg_database
    ]
    
    logger.info(f"Creating PostgreSQL backup using {pg_dump_cmd}: {plain_dump_path}")
    
    try:
        # Create the SQL dump
        process = subprocess.run(
            cmd,
            env=backup_env,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Compress with tar.gz
        logger.info(f"Compressing backup to {compressed_path}")
        with tarfile.open(compressed_path, "w:gz") as tar:
            tar.add(plain_dump_path, arcname=os.path.basename(plain_dump_path))
        
        # Remove the plain SQL file to save space
        os.remove(plain_dump_path)
        
        # Get file sizes for logging
        compressed_size_mb = os.path.getsize(compressed_path) / (1024 * 1024)
        logger.info(f"Compressed backup size: {compressed_size_mb:.2f} MB")
        
        return compressed_path
    except subprocess.CalledProcessError as e:
        logger.error(f"Backup failed: {e.stderr}")
        return None
    except Exception as e:
        logger.error(f"Error creating or compressing backup: {str(e)}")
        if os.path.exists(plain_dump_path):
            os.remove(plain_dump_path)
        if os.path.exists(compressed_path):
            os.remove(compressed_path)
        return None


def authenticate_google_drive():
    """Authenticate with Google Drive API using service account."""
    # Get service account JSON from environment variable
    service_account_json = get_env_or_default("GOOGLE_SERVICE_ACCOUNT", required=True)
    
    try:
        # Create temporary file for service account credentials
        with tempfile.NamedTemporaryFile(mode='w+', delete=False) as temp_file:
            temp_file.write(service_account_json)
            temp_file_path = temp_file.name
        
        # Create credentials from service account file
        credentials = service_account.Credentials.from_service_account_file(
            temp_file_path, scopes=SCOPES)
        
        # Build drive service
        service = build('drive', 'v3', credentials=credentials)
        
        # Remove temporary file
        os.unlink(temp_file_path)
        
        logger.info("Successfully authenticated with Google Drive using service account")
        return service
    
    except Exception as e:
        logger.error(f"Failed to authenticate with Google Drive: {str(e)}")
        if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)
        sys.exit(1)


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
        return file.get('id')
    except Exception as e:
        logger.error(f"Upload failed: {str(e)}")
        return None


def share_file_with_user(service, file_id, user_email):
    """Share a file with a specific user."""
    if not user_email:
        logger.warning("No user email provided for sharing. Skipping share step.")
        return False

    try:
        # Create permission for the user
        permission = {
            'type': 'user',
            'role': 'owner',
            'emailAddress': user_email
        }
        
        result = service.permissions().create(
            fileId=file_id,
            body=permission,
            fields='id',
            sendNotificationEmail=True
        ).execute()
        
        logger.info(f"File shared successfully with {user_email}")
        return True
    except Exception as e:
        logger.error(f"Error sharing file: {str(e)}")
        return False


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
    retention_days = int(get_env_or_default("RETENTION_DAYS", DEFAULT_RETENTION_DAYS))
    gdrive_folder = get_env_or_default("GDRIVE_FOLDER", DEFAULT_GOOGLE_DRIVE_FOLDER_NAME)
    share_email = get_env_or_default("SHARE_EMAIL", "")
    
    # Create temporary directory for backup
    temp_dir = tempfile.mkdtemp()
    
    try:
        # Step 1: Create PostgreSQL backup with compression
        backup_path = create_postgres_backup(temp_dir)
        if not backup_path:
            logger.error("Backup creation failed. Exiting.")
            sys.exit(1)
        
        # Step 2: Authenticate with Google Drive using service account
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
        file_id = upload_to_google_drive(service, backup_path, folder_id)
        if not file_id:
            logger.error("Upload to Google Drive failed.")
            sys.exit(1)
        
        # Step 5: Share the file with user if email is provided
        if share_email:
            share_file_with_user(service, file_id, share_email)
        
        # Step 6: Clean up old backups on Google Drive
        if retention_days > 0:
            delete_old_backups_gdrive(service, folder_id, retention_days)
        
        logger.info("Backup process completed successfully")
    
    finally:
        # Clean up temporary files
        try:
            for file in os.listdir(temp_dir):
                file_path = os.path.join(temp_dir, file)
                if os.path.isfile(file_path):
                    os.remove(file_path)
            os.rmdir(temp_dir)
            logger.info("Temporary files cleaned up")
        except Exception as e:
            logger.warning(f"Error cleaning up temporary files: {str(e)}")


if __name__ == "__main__":
    main()