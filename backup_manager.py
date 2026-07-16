"""
Database Backup Manager
Backup mỗi tuần 1 lần, tối đa 10 file backup
"""

import shutil
import os
import re
from datetime import datetime
from pathlib import Path

BACKUP_DIR = 'backups'
MAX_BACKUPS = 10
STUDENTS_DB = 'instance/students.db'
ATTENDANCE_DB = 'instance/attendance.db'

def ensure_backup_dir():
    """Tạo thư mục backup nếu chưa có"""
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
        print(f"✅ Thư mục backup '{BACKUP_DIR}' đã được tạo")

def backup_databases():
    """
    Tạo backup cho cả 2 database
    Tên file: students_DDMMYYYY_HHMMSS.db, attendance_DDMMYYYY_HHMMSS.db
    """
    ensure_backup_dir()
    
    timestamp = datetime.now().strftime('%d%m%Y_%H%M%S')
    
    try:
        # Backup students.db
        if os.path.exists(STUDENTS_DB):
            backup_students = os.path.join(BACKUP_DIR, f'students_{timestamp}.db')
            shutil.copy2(STUDENTS_DB, backup_students)
            print(f"✅ Backup students.db: {backup_students}")
        
        # Backup attendance.db
        if os.path.exists(ATTENDANCE_DB):
            backup_attendance = os.path.join(BACKUP_DIR, f'attendance_{timestamp}.db')
            shutil.copy2(ATTENDANCE_DB, backup_attendance)
            print(f"✅ Backup attendance.db: {backup_attendance}")
        
        # Cleanup old backups
        cleanup_old_backups()
        
        print(f"✅ Backup hoàn tất vào lúc {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
        return True
        
    except Exception as e:
        print(f"❌ Lỗi backup: {str(e)}")
        return False

def cleanup_old_backups():
    """
    Xoá backup tệp (cũ) để giữ tối đa 10 file
    """
    ensure_backup_dir()
    
    # Lấy tất cả file backup
    backup_files = []
    for filename in os.listdir(BACKUP_DIR):
        if filename.endswith('.db'):
            filepath = os.path.join(BACKUP_DIR, filename)
            # Lấy thời gian tạo file
            mtime = os.path.getmtime(filepath)
            backup_files.append((filepath, mtime, filename))
    
    # Nếu có > 10 file, xoá những file cũ nhất
    if len(backup_files) > MAX_BACKUPS:
        # Sắp xếp theo thời gian (cũ nhất trước)
        backup_files.sort(key=lambda x: x[1])
        
        # Xoá những file cũ nhất (giữ lại 10 file mới nhất)
        to_delete = len(backup_files) - MAX_BACKUPS
        for i in range(to_delete):
            filepath, _, filename = backup_files[i]
            try:
                os.remove(filepath)
                print(f"🗑️  Đã xoá backup cũ: {filename}")
            except Exception as e:
                print(f"❌ Lỗi khi xoá {filename}: {str(e)}")

def get_backup_files():
    """
    Lấy danh sách tất cả file backup
    """
    ensure_backup_dir()
    backup_files = []
    
    for filename in os.listdir(BACKUP_DIR):
        if filename.endswith('.db'):
            filepath = os.path.join(BACKUP_DIR, filename)
            mtime = os.path.getmtime(filepath)
            size = os.path.getsize(filepath)
            backup_files.append({
                'filename': filename,
                'filepath': filepath,
                'created_at': datetime.fromtimestamp(mtime),
                'size_kb': size / 1024
            })
    
    # Sắp xếp theo ngày (mới nhất trước)
    backup_files.sort(key=lambda x: x['created_at'], reverse=True)
    return backup_files

def restore_backup(backup_filename):
    """
    Restore database từ backup file
    """
    backup_path = os.path.join(BACKUP_DIR, backup_filename)
    
    if not os.path.exists(backup_path):
        print(f"❌ File backup không tồn tại: {backup_path}")
        return False
    
    try:
        # Xác định loại database từ tên file
        if 'students' in backup_filename:
            target_db = STUDENTS_DB
        elif 'attendance' in backup_filename:
            target_db = ATTENDANCE_DB
        else:
            print("❌ Không thể xác định loại database từ tên file")
            return False
        
        # Backup file hiện tại trước khi restore
        current_backup = f"{target_db}.before_restore_{datetime.now().strftime('%d%m%Y_%H%M%S')}"
        if os.path.exists(target_db):
            shutil.copy2(target_db, current_backup)
            print(f"✅ Đã backup file hiện tại: {current_backup}")
        
        # Restore từ backup
        shutil.copy2(backup_path, target_db)
        print(f"✅ Đã restore từ: {backup_filename} → {target_db}")
        return True
        
    except Exception as e:
        print(f"❌ Lỗi restore: {str(e)}")
        return False

if __name__ == '__main__':
    # Test backup
    print("🔄 Bắt đầu backup...")
    backup_databases()
    print("\n📋 Danh sách backup:")
    for bf in get_backup_files():
        print(f"  - {bf['filename']} ({bf['size_kb']:.1f} KB) - {bf['created_at'].strftime('%d/%m/%Y %H:%M:%S')}")
