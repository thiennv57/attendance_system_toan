"""
Database Backup Manager
Backup moi tuan 1 lan, toi da 10 file backup
"""

import os
import shutil
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
BACKUP_DIR = BASE_DIR / 'backups'
MAX_BACKUPS = 10
STUDENTS_DB = BASE_DIR / 'instance' / 'students.db'
ATTENDANCE_DB = BASE_DIR / 'instance' / 'attendance.db'


def ensure_backup_dir():
    """Tao thu muc backup neu chua co."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def backup_databases():
    """
    Tao backup cho ca 2 database.
    Ten file: students_DDMMYYYY_HHMMSS.db, attendance_DDMMYYYY_HHMMSS.db
    """
    ensure_backup_dir()
    timestamp = datetime.now().strftime('%d%m%Y_%H%M%S')

    try:
        if STUDENTS_DB.exists():
            backup_students = BACKUP_DIR / f'students_{timestamp}.db'
            shutil.copy2(STUDENTS_DB, backup_students)
            print(f"Backup students.db: {backup_students}")

        if ATTENDANCE_DB.exists():
            backup_attendance = BACKUP_DIR / f'attendance_{timestamp}.db'
            shutil.copy2(ATTENDANCE_DB, backup_attendance)
            print(f"Backup attendance.db: {backup_attendance}")

        cleanup_old_backups()
        print(f"Backup hoan tat vao luc {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
        return True
    except Exception as e:
        print(f"Loi backup: {str(e)}")
        return False


def cleanup_old_backups():
    """Xoa backup cu de giu toi da MAX_BACKUPS file."""
    ensure_backup_dir()

    backup_files = []
    for filename in os.listdir(BACKUP_DIR):
        if filename.endswith('.db'):
            filepath = BACKUP_DIR / filename
            backup_files.append((filepath, filepath.stat().st_mtime, filename))

    if len(backup_files) > MAX_BACKUPS:
        backup_files.sort(key=lambda x: x[1])
        to_delete = len(backup_files) - MAX_BACKUPS
        for i in range(to_delete):
            filepath, _, filename = backup_files[i]
            try:
                filepath.unlink()
                print(f"Da xoa backup cu: {filename}")
            except Exception as e:
                print(f"Loi khi xoa {filename}: {str(e)}")


def get_backup_files():
    """Lay danh sach tat ca file backup."""
    ensure_backup_dir()
    backup_files = []

    for filename in os.listdir(BACKUP_DIR):
        if filename.endswith('.db'):
            filepath = BACKUP_DIR / filename
            backup_files.append({
                'filename': filename,
                'filepath': str(filepath),
                'created_at': datetime.fromtimestamp(filepath.stat().st_mtime),
                'size_kb': filepath.stat().st_size / 1024,
            })

    backup_files.sort(key=lambda x: x['created_at'], reverse=True)
    return backup_files


def restore_backup(backup_filename):
    """Restore database tu backup file."""
    backup_path = BACKUP_DIR / backup_filename

    if not backup_path.exists():
        print(f"File backup khong ton tai: {backup_path}")
        return False

    try:
        if 'students' in backup_filename:
            target_db = STUDENTS_DB
        elif 'attendance' in backup_filename:
            target_db = ATTENDANCE_DB
        else:
            print("Khong the xac dinh loai database tu ten file")
            return False

        current_backup = target_db.parent / f"{target_db.name}.before_restore_{datetime.now().strftime('%d%m%Y_%H%M%S')}"
        if target_db.exists():
            shutil.copy2(target_db, current_backup)
            print(f"Da backup file hien tai: {current_backup}")

        shutil.copy2(backup_path, target_db)
        print(f"Da restore tu: {backup_filename} -> {target_db}")
        return True
    except Exception as e:
        print(f"Loi restore: {str(e)}")
        return False


if __name__ == '__main__':
    print("Bat dau backup...")
    backup_databases()
    print("\nDanh sach backup:")
    for bf in get_backup_files():
        print(f"  - {bf['filename']} ({bf['size_kb']:.1f} KB) - {bf['created_at'].strftime('%d/%m/%Y %H:%M:%S')}")
