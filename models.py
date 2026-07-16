from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

# Create a single SQLAlchemy instance
db = SQLAlchemy()

class User(UserMixin, db.Model):
    __bind_key__ = 'attendance'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), default='giaovien', nullable=False) # Đảm bảo trường 'role' tồn tại
    is_admin = db.Column(db.Boolean, default=False)

class Student(db.Model):
    __tablename__ = 'student'
    __bind_key__ = 'students' # This model uses the 'students' database
    id = db.Column(db.Integer, primary_key=True)
    excel_student_id = db.Column(db.String(50), unique=True, nullable=True)
    name = db.Column(db.String(100), nullable=False)
    normalized_name = db.Column(db.String(100), nullable=True) # Add normalized_name field
    grade = db.Column(db.String(20), nullable=False)
    school = db.Column(db.String(100), nullable=False)
    
    father_name = db.Column(db.String(100))
    father_phone = db.Column(db.String(20))
    father_occupation = db.Column(db.String(100))
    mother_name = db.Column(db.String(100))
    mother_phone = db.Column(db.String(20))
    mother_occupation = db.Column(db.String(100))
    
    address = db.Column(db.String(200))
    notes = db.Column(db.Text)
    comment = db.Column(db.Text, nullable=True)
    comment_last_updated_by = db.Column(db.String(100), nullable=True)
    comment_last_updated_at = db.Column(db.DateTime, nullable=True)
    id_check = db.Column(db.String(50), unique=True, nullable=True) # Add IdCheck field
    last_update_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    schedules = db.relationship('Schedule', backref='student', lazy=True)
    default_monthly_fee = db.Column(db.Integer, default=900000, nullable=False)
    # No direct relationship to Attendance here, as Attendance is in a different database.

class Tuition(db.Model):
    __tablename__ = 'tuition'
    __bind_key__ = 'students' # This model uses the 'students' database
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id', ondelete='CASCADE'), nullable=False)
    month = db.Column(db.Integer, nullable=False)
    year = db.Column(db.Integer, nullable=False)
    amount_due = db.Column(db.Integer, nullable=False)
    is_paid = db.Column(db.Boolean, default=False)
    custom_fee = db.Column(db.Integer, nullable=True) # For custom monthly fee if different from default
    paid_date = db.Column(db.DateTime, nullable=True)
    recorded_by = db.Column(db.Integer, nullable=True)
    student = db.relationship('Student', backref='tuitions', lazy=True)

    __table_args__ = (db.UniqueConstraint('student_id', 'month', 'year', name='_student_month_year_uc'),)

class Schedule(db.Model):
    __tablename__ = 'schedule'
    __bind_key__ = 'students' # This model uses the 'students' database
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    day_of_week = db.Column(db.Integer, nullable=False) # 1=Monday, 7=Sunday
    time_slot = db.Column(db.String(50), nullable=False)

class Attendance(db.Model):
    __tablename__ = 'attendance'
    __bind_key__ = 'attendance' # This model uses the 'attendance' database
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, nullable=False) # This is NOT a ForeignKey to db_students.Student.id
    date = db.Column(db.Date, nullable=False)
    time_slot = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), nullable=False) # e.g., 'present', 'absent', 'late'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_by_user = db.relationship('User', foreign_keys=[created_by], backref='attendance_created')
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)
    update_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    update_by_user = db.relationship('User', foreign_keys=[update_by], backref='attendance_updated')
    comment = db.Column(db.Text, nullable=True)


class TestExam(db.Model):
    __tablename__ = 'test_exam'
    __bind_key__ = 'attendance'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    grade = db.Column(db.String(20), nullable=False)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    created_by_user = db.relationship('User', foreign_keys=[created_by], backref='test_exams_created')


class TestScore(db.Model):
    __tablename__ = 'test_score'
    __bind_key__ = 'attendance'
    id = db.Column(db.Integer, primary_key=True)
    test_exam_id = db.Column(db.Integer, db.ForeignKey('test_exam.id', ondelete='CASCADE'), nullable=False)
    student_id = db.Column(db.Integer, nullable=False)
    attempt_number = db.Column(db.Integer, nullable=False)
    score = db.Column(db.Float, nullable=True)
    comment = db.Column(db.Text, nullable=True)
    graded_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    graded_at = db.Column(db.DateTime, nullable=True)

    test_exam = db.relationship('TestExam', backref=db.backref('scores', lazy=True, cascade='all, delete-orphan'))
    graded_by_user = db.relationship('User', foreign_keys=[graded_by], backref='test_scores_graded')

    __table_args__ = (db.UniqueConstraint('test_exam_id', 'student_id', 'attempt_number', name='_test_exam_student_attempt_uc'),)



class TeacherStudentAssignment(db.Model):
    __tablename__ = 'teacher_student_assignment'
    __bind_key__ = 'attendance' # Or 'students', depending on where you want to store this. Let's use 'attendance' for now.
    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    student_id = db.Column(db.Integer, nullable=False) # Changed to regular Integer column

    # Add a unique constraint to prevent duplicate assignments
    __table_args__ = (db.UniqueConstraint('teacher_id', 'student_id', name='_teacher_student_uc'),)

    teacher = db.relationship('User', backref='assigned_students')


if __name__ == '__main__':
    admin = User(username='admin', password='admin', role='admin')
    giaovien = User(username='giaovien', password='giaovien', role='giaovien')
