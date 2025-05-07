#!/usr/bin/env python3
"""
PostgreSQL Google Drive Backup Script with Multi-Version Support

This script:
1. Uses the pg_dump command detected by version_detect.sh
2. Creates a backup of a PostgreSQL database
3. Compresses the backup using tar.gz for smaller file size
4. Uploads it to Google Drive using local token storage
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

# Google Drive API libraries
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import json

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
TOKEN_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token.json")


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
    """Authenticate with Google Drive API using local token storage."""
    creds = None
    
    # Check if token file exists
    if os.path.exists(TOKEN_FILE_PATH):
        try:
            with open(TOKEN_FILE_PATH, 'r') as token:
                creds = Credentials.from_authorized_user_info(json.load(token), SCOPES)
            logger.info("Using existing token file for authentication")
        except Exception as e:
            logger.warning(f"Invalid token file, will create new one: {str(e)}")
            creds = None
    
    # If credentials don't exist or are invalid, refresh or get new ones
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # Try to refresh the token
            try:
                creds.refresh(Request())
                logger.info("Successfully refreshed expired token")
            except Exception as e:
                logger.warning(f"Failed to refresh token, will create new one: {str(e)}")
                creds = None
        
        # If still no valid credentials, we need to create new ones
        if not creds or not creds.valid:
            # Check if GOOGLE_CREDENTIALS environment variable exists
            google_creds_str = get_env_or_default("GOOGLE_CREDENTIALS")
            if not google_creds_str:
                logger.error("GOOGLE_CREDENTIALS environment variable not set")
                sys.exit(1)
            
            # Create temporary file for credentials
            temp_dir = tempfile.mkdtemp()
            creds_path = os.path.join(temp_dir, "credentials.json")
            
            # Write credentials to temporary file
            with open(creds_path, "w") as f:
                f.write(google_creds_str)
            
            try:
                # For headless server usage (OAuth2 device flow)
                flow = InstalledAppFlow.from_client_secrets_file(
                    creds_path, 
                    SCOPES,
                    redirect_uri='urn:ietf:wg:oauth:2.0:oob'  # Use out-of-band flow
                )
                
                auth_url, _ = flow.authorization_url(prompt='consent')
                logger.info("Please go to this URL to authenticate:")
                logger.info(auth_url)
                logger.info("After granting permission, you will receive a code. Enter that code below:")
                
                # In a non-interactive environment, we need to wait for manual intervention
                auth_code = input("Enter the authorization code: ").strip()
                
                flow.fetch_token(code=auth_code)
                creds = flow.credentials
                
                # Clean up temporary directory
                os.remove(creds_path)
                os.rmdir(temp_dir)
            except Exception as e:
                logger.error(f"Authentication failed: {str(e)}")
                if os.path.exists(creds_path):
                    os.remove(creds_path)
                if os.path.exists(temp_dir):
                    os.rmdir(temp_dir)
                sys.exit(1)
        
        # Save the credentials for the next run
        try:
            with open(TOKEN_FILE_PATH, 'w') as token:
                token.write(creds.to_json())
            logger.info(f"Token saved to {TOKEN_FILE_PATH}")
        except Exception as e:
            logger.warning(f"Failed to save token file: {str(e)}")
    
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
    
    # Create temporary directory for backup
    temp_dir = tempfile.mkdtemp()
    
    try:
        # Step 1: Create PostgreSQL backup with compression
        backup_path = create_postgres_backup(temp_dir)
        if not backup_path:
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
        
        # Step 5: Clean up old backups on Google Drive
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