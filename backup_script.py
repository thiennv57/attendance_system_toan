import os
import shutil
import datetime

# Configuration
DATABASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance')
BACKUP_DIR = os.path.join(DATABASE_DIR, 'backups')
DATABASE_FILES = ['attendance.db', 'students.db']
MAX_BACKUPS = 10

def create_backup():
    """Creates a timestamped backup of the specified database files."""
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)

    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')

    for db_file in DATABASE_FILES:
        source_path = os.path.join(DATABASE_DIR, db_file)
        if os.path.exists(source_path):
            backup_filename = f'{db_file}.{timestamp}'
            destination_path = os.path.join(BACKUP_DIR, backup_filename)
            shutil.copy2(source_path, destination_path)
            print(f'Created backup: {destination_path}')
        else:
            print(f'Warning: Database file not found: {source_path}')

def clean_old_backups():
    """Removes oldest backups, keeping only MAX_BACKUPS."""
    for db_file in DATABASE_FILES:
        # Get all backups for the current database file
        backups = []
        for f in os.listdir(BACKUP_DIR):
            if f.startswith(f'{db_file}.') and f != db_file: # Exclude the original db file itself
                backups.append(os.path.join(BACKUP_DIR, f))

        # Sort by modification time (oldest first)
        backups.sort(key=os.path.getmtime)

        # Remove oldest backups if count exceeds MAX_BACKUPS
        if len(backups) > MAX_BACKUPS:
            for i in range(len(backups) - MAX_BACKUPS):
                os.remove(backups[i])
                print(f'Removed old backup: {backups[i]}')

if __name__ == "__main__":
    print("Starting database backup...")
    create_backup()
    clean_old_backups()
    print("Database backup completed.")