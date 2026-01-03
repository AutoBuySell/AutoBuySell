#!/usr/bin/env python3
"""
Database Backup Script for AutoBuySell
Creates a compressed backup of the PostgreSQL database using pg_dump.

Usage:
    python scripts/backup_db.py                           # Default backup to api/backups/
    python scripts/backup_db.py --output /path/to/backup  # Custom output path
"""

import argparse
import os
import sys
from datetime import datetime
import subprocess

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings


def parse_database_url(url: str) -> dict:
    """Parse DATABASE_URL into components."""
    # Format: postgresql+asyncpg://user:password@host:port/dbname
    # Remove the driver part
    url = url.replace("postgresql+asyncpg://", "")
    
    # Split user:password and host:port/dbname
    auth_part, host_part = url.split("@")
    user, password = auth_part.split(":")
    
    # Split host:port and dbname
    host_port, dbname = host_part.split("/")
    host, port = host_port.split(":")
    
    return {
        "user": user,
        "password": password,
        "host": host,
        "port": port,
        "dbname": dbname
    }


def is_running_in_docker() -> bool:
    """Check if running inside a Docker container."""
    return os.path.exists('/.dockerenv')


def create_backup(output_dir: str) -> str:
    """Create a database backup using pg_dump."""
    # Parse database URL
    db_config = parse_database_url(settings.DATABASE_URL)
    
    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)
    
    # Generate filename with timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"autobuysell_{timestamp}.sql.gz"
    output_path = os.path.join(output_dir, filename)
    
    print("=" * 60)
    print("AutoBuySell Database Backup")
    print("=" * 60)
    print(f"Database: {db_config['dbname']}")
    print(f"Host: {db_config['host']}:{db_config['port']}")
    print(f"Output: {output_path}")
    print("-" * 60)
    
    try:
        print("📦 Creating backup...")
        
        # Check if we're running in Docker
        in_docker = is_running_in_docker()
        
        if in_docker and db_config['host'] == 'db':
            # Running inside Docker, use docker exec to access pg_dump in db container
            print("  Running in Docker environment, using database container...")
            
            # We need to execute pg_dump from outside the API container
            # This is a limitation - backup should be run from host or db container
            print("\n⚠️  Notice: For Docker deployments, please run backup from host:")
            print(f"  docker exec autobuysell_db pg_dump -U {db_config['user']} {db_config['dbname']} | gzip > {filename}")
            print("\nAlternatively, use the provided shell script for Docker deployments.")
            
            # Let's create a workaround by calling docker from within the container
            # This requires the Docker socket to be mounted, which is not standard
            # Instead, we'll provide instructions
            sys.exit(1)
        else:
            # Running locally or host has access to pg_dump
            env = os.environ.copy()
            env["PGPASSWORD"] = db_config["password"]
            
            cmd = [
                "pg_dump",
                "-h", db_config["host"],
                "-p", db_config["port"],
                "-U", db_config["user"],
                "-d", db_config["dbname"],
                "--no-owner",
                "--no-acl",
                "-F", "p",  # Plain text format
            ]
            
            # Run pg_dump and pipe to gzip
            with open(output_path, 'wb') as f:
                pg_dump = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    env=env,
                    stderr=subprocess.PIPE
                )
                gzip = subprocess.Popen(
                    ["gzip"],
                    stdin=pg_dump.stdout,
                    stdout=f,
                    stderr=subprocess.PIPE
                )
                pg_dump.stdout.close()
                
                # Wait for both processes to complete
                gzip_stderr = gzip.communicate()[1]
                pg_dump_stderr = pg_dump.communicate()[1]
                
                if pg_dump.returncode != 0:
                    error_msg = pg_dump_stderr.decode() if pg_dump_stderr else "Unknown error"
                    raise Exception(f"pg_dump failed: {error_msg}")
                
                if gzip.returncode != 0:
                    error_msg = gzip_stderr.decode() if gzip_stderr else "Unknown error"
                    raise Exception(f"gzip failed: {error_msg}")
            
            # Get file size
            file_size = os.path.getsize(output_path)
            size_mb = file_size / (1024 * 1024)
            
            print("✅ Backup created successfully!")
            print(f"📁 File: {output_path}")
            print(f"📊 Size: {size_mb:.2f} MB")
            print("=" * 60)
            
            return output_path
        
    except Exception as e:
        print(f"❌ Backup failed: {e}")
        # Clean up partial backup file
        if os.path.exists(output_path):
            os.remove(output_path)
        sys.exit(1)



def main():
    parser = argparse.ArgumentParser(description="Backup AutoBuySell Database")
    parser.add_argument(
        "--output",
        default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backups"),
        help="Output directory for backup files (default: api/backups/)"
    )
    
    args = parser.parse_args()
    create_backup(args.output)


if __name__ == "__main__":
    main()
