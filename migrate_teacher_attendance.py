"""Migration: thêm cột User.display_name và bảng teacher_attendance.

Chạy: python migrate_teacher_attendance.py
An toàn khi chạy lại nhiều lần (idempotent).
"""
from sqlalchemy import inspect, text

from app import app, db
from models import TeacherAttendance


def add_display_name_column():
    engine = db.engines['attendance']
    inspector = inspect(engine)
    columns = [c['name'] for c in inspector.get_columns('user')]
    if 'display_name' in columns:
        print('- Cột user.display_name đã tồn tại, bỏ qua.')
        return
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE "user" ADD COLUMN display_name VARCHAR(120)'))
    print('- Đã thêm cột user.display_name.')


def create_teacher_attendance_table():
    engine = db.engines['attendance']
    TeacherAttendance.__table__.create(bind=engine, checkfirst=True)
    print('- Bảng teacher_attendance đã sẵn sàng.')


def main():
    with app.app_context():
        add_display_name_column()
        create_teacher_attendance_table()
        print('Migration hoàn tất.')


if __name__ == '__main__':
    main()
