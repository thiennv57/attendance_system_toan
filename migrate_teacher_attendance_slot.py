"""Migration: tách điểm danh giáo viên theo CA (Ca 1 / Ca 2).

Thay đổi:
  - Thêm cột teacher_attendance.time_slot
  - Đổi ràng buộc UNIQUE (teacher_id, date) -> (teacher_id, date, time_slot)

Dữ liệu cũ được GIỮ NGUYÊN và gán vào Ca 1 (dữ liệu cũ ghi theo ngày, không có ca).
SQLite không ALTER được ràng buộc nên phải dựng lại bảng; script tự backup DB trước.

Chạy: python migrate_teacher_attendance_slot.py
An toàn khi chạy lại nhiều lần (idempotent).
"""
import os
import shutil
from datetime import datetime

from sqlalchemy import inspect, text

from app import app, db, TIME_SLOT_1
from models import TeacherAttendance

NEW_TABLE_SQL = """
CREATE TABLE teacher_attendance_new (
    id INTEGER NOT NULL PRIMARY KEY,
    teacher_id INTEGER NOT NULL REFERENCES "user"(id),
    date DATE NOT NULL,
    time_slot VARCHAR(50) NOT NULL,
    is_present BOOLEAN NOT NULL DEFAULT 0,
    is_late BOOLEAN NOT NULL DEFAULT 0,
    note TEXT,
    created_at DATETIME,
    created_by INTEGER REFERENCES "user"(id),
    updated_at DATETIME,
    update_by INTEGER REFERENCES "user"(id),
    CONSTRAINT _teacher_attendance_slot_uc UNIQUE (teacher_id, date, time_slot)
)
"""


def backup_db():
    src = os.path.join(app.instance_path, 'attendance.db')
    if not os.path.exists(src):
        return None
    dst = f"{src}.bak_slot_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(src, dst)
    print(f'- Đã backup DB: {dst}')
    return dst


def migrate():
    engine = db.engines['attendance']
    inspector = inspect(engine)

    if 'teacher_attendance' not in inspector.get_table_names():
        TeacherAttendance.__table__.create(bind=engine, checkfirst=True)
        print('- Chưa có bảng cũ, đã tạo bảng teacher_attendance theo schema mới.')
        return

    columns = [c['name'] for c in inspector.get_columns('teacher_attendance')]
    if 'time_slot' in columns:
        print('- Bảng teacher_attendance đã có cột time_slot, bỏ qua.')
        return

    backup_db()

    with engine.begin() as conn:
        conn.execute(text('DROP TABLE IF EXISTS teacher_attendance_new'))
        conn.execute(text(NEW_TABLE_SQL))
        conn.execute(
            text("""
                INSERT INTO teacher_attendance_new
                    (id, teacher_id, date, time_slot, is_present, is_late, note,
                     created_at, created_by, updated_at, update_by)
                SELECT id, teacher_id, date, :slot, is_present, is_late, note,
                       created_at, created_by, updated_at, update_by
                FROM teacher_attendance
            """),
            {'slot': TIME_SLOT_1},
        )
        moved = conn.execute(text('SELECT COUNT(*) FROM teacher_attendance_new')).scalar()
        conn.execute(text('DROP TABLE teacher_attendance'))
        conn.execute(text('ALTER TABLE teacher_attendance_new RENAME TO teacher_attendance'))

    print(f'- Đã thêm cột time_slot và đổi ràng buộc UNIQUE.')
    print(f'- Giữ nguyên {moved} bản ghi cũ, gán vào "{TIME_SLOT_1}".')


def main():
    with app.app_context():
        migrate()
        print('Migration hoàn tất.')


if __name__ == '__main__':
    main()
