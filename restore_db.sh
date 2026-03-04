#!/bin/bash
# Database Restore Script for Docker Deployment
# Restores a PostgreSQL database from a backup file

set -e

# Configuration
CONTAINER_NAME="autobuysell_db"
DB_USER="autosys"
DB_NAME="autosys"

# Check arguments
if [ $# -eq 0 ]; then
    echo "Usage: $0 <backup_file> [--force]"
    echo "Example: $0 api/backups/autobuysell_2026-01-03_18-00-00.sql.gz"
    exit 1
fi

BACKUP_FILE="$1"
FORCE=false

if [ "$2" == "--force" ]; then
    FORCE=true
fi

# Check if backup file exists
if [ ! -f "${BACKUP_FILE}" ]; then
    echo "❌ Error: Backup file not found: ${BACKUP_FILE}"
    exit 1
fi

echo "============================================================"
echo "AutoBuySell Database Restore (Docker)"
echo "============================================================"
echo "Backup file: ${BACKUP_FILE}"
echo "Database: ${DB_NAME}"
echo "Container: ${CONTAINER_NAME}"
echo "------------------------------------------------------------"

# Check if container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "❌ Error: Container '${CONTAINER_NAME}' is not running"
    exit 1
fi

# Confirmation prompt
if [ "${FORCE}" = false ]; then
    echo ""
    echo "⚠️  WARNING: This will overwrite all data in the database!"
    read -p "Are you sure you want to continue? Type 'yes' to proceed: " CONFIRM
    if [ "${CONFIRM}" != "yes" ]; then
        echo "❌ Restore cancelled."
        exit 0
    fi
fi

echo ""
echo "📦 Restoring database..."

# Decompress and restore to database
if [[ "${BACKUP_FILE}" == *.gz ]]; then
    # Gzipped file
    gunzip -c "${BACKUP_FILE}" | docker exec -i "${CONTAINER_NAME}" \
        psql -U "${DB_USER}" -d "${DB_NAME}" --quiet
else
    # Plain SQL file
    cat "${BACKUP_FILE}" | docker exec -i "${CONTAINER_NAME}" \
        psql -U "${DB_USER}" -d "${DB_NAME}" --quiet
fi

# Check if restore was successful
if [ $? -eq 0 ]; then
    echo "✅ Database restored successfully!"
    echo "============================================================"
else
    echo "❌ Restore failed!"
    exit 1
fi
