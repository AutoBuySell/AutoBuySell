#!/usr/bin/env python3
"""
Database Restore Script for AutoBuySell
Restores a PostgreSQL database from a backup file created by backup_db.py.

Usage:
    python scripts/restore_db.py backups/autobuysell_2026-01-03_18-00-00.sql.gz
    python scripts/restore_db.py --force backups/autobuysell_2026-01-03_18-00-00.sql.gz
"""

import argparse
import os
import sys
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
        "dbname": dbname,
    }


def restore_backup(backup_file: str, force: bool = False):
    """Restore database from a backup file."""
    # Check if backup file exists
    if not os.path.exists(backup_file):
        print(f"❌ Error: Backup file not found: {backup_file}")
        sys.exit(1)

    # Parse database URL
    db_config = parse_database_url(settings.DATABASE_URL)

    print("=" * 60)
    print("AutoBuySell Database Restore")
    print("=" * 60)
    print(f"Backup file: {backup_file}")
    print(f"Database: {db_config['dbname']}")
    print(f"Host: {db_config['host']}:{db_config['port']}")
    print("-" * 60)

    # Confirmation prompt
    if not force:
        print("\n⚠️  WARNING: This will overwrite all data in the database!")
        confirm = input("Are you sure you want to continue? Type 'yes' to proceed: ")
        if confirm.lower() != "yes":
            print("❌ Restore cancelled.")
            sys.exit(0)

    # Set environment variable for password
    env = os.environ.copy()
    env["PGPASSWORD"] = db_config["password"]

    try:
        print("\n📦 Restoring database...")

        # Determine if file is gzipped
        is_gzipped = backup_file.endswith(".gz")

        if is_gzipped:
            # Decompress on the fly and pipe to psql
            with open(backup_file, "rb") as f:
                gunzip = subprocess.Popen(
                    ["gunzip", "-c"],
                    stdin=f,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

                psql = subprocess.Popen(
                    [
                        "psql",
                        "-h",
                        db_config["host"],
                        "-p",
                        db_config["port"],
                        "-U",
                        db_config["user"],
                        "-d",
                        db_config["dbname"],
                        "--quiet",
                    ],
                    stdin=gunzip.stdout,
                    env=env,
                    stderr=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                )

                gunzip.stdout.close()

                # Wait for both processes
                psql_stderr = psql.communicate()[1]
                gunzip_stderr = gunzip.communicate()[1]

                if gunzip.returncode != 0:
                    error_msg = (
                        gunzip_stderr.decode() if gunzip_stderr else "Unknown error"
                    )
                    raise Exception(f"gunzip failed: {error_msg}")

                if psql.returncode != 0:
                    error_msg = psql_stderr.decode() if psql_stderr else "Unknown error"
                    raise Exception(f"psql failed: {error_msg}")
        else:
            # Direct restore from uncompressed file
            with open(backup_file, "r") as f:
                psql = subprocess.Popen(
                    [
                        "psql",
                        "-h",
                        db_config["host"],
                        "-p",
                        db_config["port"],
                        "-U",
                        db_config["user"],
                        "-d",
                        db_config["dbname"],
                        "--quiet",
                    ],
                    stdin=f,
                    env=env,
                    stderr=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                )

                psql_stderr = psql.communicate()[1]

                if psql.returncode != 0:
                    error_msg = psql_stderr.decode() if psql_stderr else "Unknown error"
                    raise Exception(f"psql failed: {error_msg}")

        print("✅ Database restored successfully!")
        print("=" * 60)

    except Exception as e:
        print(f"❌ Restore failed: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Restore AutoBuySell Database")
    parser.add_argument(
        "backup_file",
        help="Path to backup file (e.g., backups/autobuysell_2026-01-03.sql.gz)",
    )
    parser.add_argument("--force", action="store_true", help="Skip confirmation prompt")

    args = parser.parse_args()
    restore_backup(args.backup_file, args.force)


if __name__ == "__main__":
    main()
