#!/bin/bash
# Database Backup Script for Docker Deployment
# Creates a compressed backup of the PostgreSQL database

set -e

# Configuration
CONTAINER_NAME="autobuysell_db"
DB_USER="autosys"
DB_NAME="autosys"
BACKUP_DIR="./api/backups"
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
FILENAME="autobuysell_${TIMESTAMP}.sql.gz"
OUTPUT_PATH="${BACKUP_DIR}/${FILENAME}"

# Create backup directory if it doesn't exist
mkdir -p "${BACKUP_DIR}"

echo "============================================================"
echo "AutoBuySell Database Backup (Docker)"
echo "============================================================"
echo "Database: ${DB_NAME}"
echo "Container: ${CONTAINER_NAME}"
echo "Output: ${OUTPUT_PATH}"
echo "------------------------------------------------------------"

# Check if container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "❌ Error: Container '${CONTAINER_NAME}' is not running"
    exit 1
fi

echo "📦 Creating backup..."

# Execute pg_dump in the database container and compress
docker exec "${CONTAINER_NAME}" pg_dump -U "${DB_USER}" "${DB_NAME}" \
    --no-owner --no-acl | gzip > "${OUTPUT_PATH}"

# Check if backup was successful
if [ $? -eq 0 ] && [ -f "${OUTPUT_PATH}" ]; then
    # Get file size
    SIZE=$(du -h "${OUTPUT_PATH}" | cut -f1)
    
    echo "✅ Backup created successfully!"
    echo "📁 File: ${OUTPUT_PATH}"
    echo "📊 Size: ${SIZE}"
    echo "============================================================"
else
    echo "❌ Backup failed!"
    exit 1
fi
