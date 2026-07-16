import re
import os
import unicodedata
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_file, abort, send_from_directory, make_response
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
import sqlite3
from apscheduler.schedulers.background import BackgroundScheduler
from backup_manager import backup_databases, get_backup_files, restore_backup
import atexit

def get_db_connection():
    conn = sqlite3.connect('instance/attendance.db')
    conn.row_factory = sqlite3.Row
    return conn
from functools import wraps
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_, cast, String, Integer, func
from sqlalchemy.orm import aliased
from datetime import datetime, date, timedelta
import pandas as pd
import calendar
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, time
from sqlalchemy import or_, and_, func # Add 'and_' for combined filters
import io
import csv
from urllib.parse import urlsplit

from models import db, User, Student, Schedule, Attendance, Tuition, TeacherStudentAssignment, TestExam, TestScore
from utils import import_excel_data, get_students_for_slot, remove_diacritics

DEFAULT_TEST_ATTEMPT_COUNT = 10
TEST_SCORE_PATTERN = re.compile(r'^(?:10(?:\.0+)?|[0-9](?:\.\d+)?)$')


def get_grade_sort_value(grade_name):
    if not grade_name:
        return 999

    match = re.search(r'\d+', grade_name)
    if match:
        try:
            return int(match.group())
        except ValueError:
            return 999
    return 999


def sort_students_for_display(students_list):
    return sorted(
        students_list,
        key=lambda student: (
            get_grade_sort_value(student.grade),
            student.name.split()[-1] if student.name and student.name.split() else '',
            student.name or ''
        )
    )


def parse_optional_score(raw_value):
    if raw_value is None:
        return None

    raw_value = raw_value.strip()
    if not raw_value:
        return None

    if ',' in raw_value:
        raise ValueError('Score must use "." as the decimal separator.')

    if not TEST_SCORE_PATTERN.fullmatch(raw_value):
        raise ValueError('Score must be a number from 0 to 10.')

    score = float(raw_value)
    if score < 0 or score > 10:
        raise ValueError('Score must be a number from 0 to 10.')
    return score


def format_test_attempt_label(attempt_number):
    return f'Lan {attempt_number}'


def get_exam_attempt_numbers(exam_id):
    attempt_rows = db.session.query(TestScore.attempt_number).filter_by(
        test_exam_id=exam_id
    ).distinct().order_by(TestScore.attempt_number.asc()).all()
    return [attempt_number for (attempt_number,) in attempt_rows]


def build_test_attempt_labels(attempt_numbers):
    return {
        attempt_number: format_test_attempt_label(attempt_number)
        for attempt_number in attempt_numbers
    }


def add_attempts_to_exam(exam_id, student_ids, start_attempt, count=1):
    for offset in range(count):
        attempt_number = start_attempt + offset
        for student_id in student_ids:
            db.session.add(TestScore(
                test_exam_id=exam_id,
                student_id=student_id,
                attempt_number=attempt_number
            ))


def build_current_url():
    query_string = request.query_string.decode('utf-8')
    if query_string:
        return f"{request.path}?{query_string}"
    return request.path


def redirect_back(default_endpoint, **values):
    return_to = request.form.get('return_to') or request.args.get('return_to')
    if return_to:
        parsed = urlsplit(return_to)
        if not parsed.scheme and not parsed.netloc and parsed.path.startswith('/'):
            return redirect(return_to)
    return redirect(url_for(default_endpoint, **values))

class Pagination:
    def __init__(self, page, per_page, total, items):
        self.page = page
        self.per_page = per_page
        self.total = total
        self.items = items
        self.pages = (total + per_page - 1) // per_page if total > 0 else 0

    @property
    def has_prev(self):
        return self.page > 1

    @property
    def prev_num(self):
        return self.page - 1

    @property
    def has_next(self):
        return self.page < self.pages

    @property
    def next_num(self):
        return self.page + 1

    def iter_pages(self, left_edge=2, right_edge=2, left_current=2, right_current=5):
        last = 0
        for num in range(1, self.pages + 1):
            if num <= left_edge or \
               (num > self.page - left_current - 1 and num < self.page + right_current) or \
               num > self.pages - right_edge:
                if last + 1 != num:
                    yield None
                yield num
                last = num

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Bạn không có quyền truy cập trang này.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Custom Jinja2 zip filter
def zip_filter(a, b):
    return zip(a, b)


app = Flask(__name__, instance_relative_config=True)
app.jinja_env.filters['zip'] = zip_filter


@app.context_processor
def inject_current_url():
    try:
        return {'current_url': build_current_url()}
    except RuntimeError:
        return {'current_url': ''}
app.config['SECRET_KEY'] = 'lopcophuong'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(app.instance_path, 'attendance.db')
print(f"SQLALCHEMY_DATABASE_URI: {app.config['SQLALCHEMY_DATABASE_URI']}")
app.config['SQLALCHEMY_BINDS'] = {
    'students': 'sqlite:///' + os.path.join(app.instance_path, 'students.db'),
    'attendance': 'sqlite:///' + os.path.join(app.instance_path, 'attendance.db')
}
print(f"SQLALCHEMY_BINDS: {app.config['SQLALCHEMY_BINDS']}")
app.config['UPLOAD_FOLDER'] = 'uploads'

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

db.init_app(app)

with app.app_context():
    instance_path = app.instance_path
    if not os.path.exists(os.path.join(instance_path, 'attendance.db')) or \
       not os.path.exists(os.path.join(instance_path, 'students.db')):
        db.create_all()
        print("Database tables created/updated by app.py on startup.")
    else:
        print("Database files already exist. Skipping db.create_all() in app.py.")

import re
def _sqlite_regexp_replace(expr, pattern, repl):
    return re.sub(pattern, repl, expr)

with app.app_context():
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    @event.listens_for(Engine, "connect")
    def set_sqlite_regexp(dbapi_connection, connection_record):
        dbapi_connection.create_function("regexp_replace", 3, _sqlite_regexp_replace)

# ============== BACKUP SCHEDULER ==============
scheduler = BackgroundScheduler()

def schedule_backup():
    """Chạy backup (được gọi bởi scheduler)"""
    print("\n" + "="*50)
    print("🔄 Bắt đầu backup tự động...")
    print("="*50)
    backup_databases()
    print("="*50 + "\n")

# Schedule backup mỗi tuần vào Thứ Hai lúc 00:00
try:
    scheduler.add_job(
        func=schedule_backup,
        trigger="cron",
        day_of_week="0",  # Thứ Hai (0 = Mon, 6 = Sun)
        hour=0,
        minute=0,
        id="weekly_backup",
        name="Weekly Database Backup"
    )
    scheduler.start()
    print("✅ Scheduler backup đã được khởi động (mỗi Thứ Hai 00:00)")
    
    # Đảm bảo scheduler dừng khi app đóng
    atexit.register(lambda: scheduler.shutdown())
except Exception as e:
    print(f"⚠️  Lỗi khởi động scheduler: {str(e)}")

# ============== END BACKUP SCHEDULER ==============

login_manager = LoginManager(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# Main routes
@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('student_lookup'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    return_to = request.values.get('return_to') or request.referrer or url_for('students')
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = db.session.query(User).filter_by(username=username).first()

        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid username or password', 'danger')
    return render_template('login.html')

@app.route('/dashboard')
@login_required
def dashboard():
    current_date = datetime.now().date()

    # Get month and year from request, default to current
    try:
        selected_month = int(request.args.get('month', current_date.month))
        selected_year = int(request.args.get('year', current_date.year))
    except ValueError:
        selected_month = current_date.month
        selected_year = current_date.year

    # Basic validation
    if not (1 <= selected_month <= 12):
        selected_month = current_date.month

    # Create start date for the selected month
    start_of_month = date(selected_year, selected_month, 1)

    # Determine end date
    if selected_month == current_date.month and selected_year == current_date.year:
        # If current month, query up to today
        end_date = current_date
    else:
        # If past/future month, query up to the last day of that month
        last_day = calendar.monthrange(selected_year, selected_month)[1]
        end_date = date(selected_year, selected_month, last_day)

    # --- NEW LOGIC FOR CALCULATING ABSENCES ---

    # 1. Get all attendance records for the selected month/period
    # This tells us which sessions (date + time_slot) actually happened
    attendance_records = db.session.query(Attendance).filter(
        Attendance.date >= start_of_month,
        Attendance.date <= end_date
    ).all()

    # Identify all unique sessions that occurred
    # Format: (date_obj, time_slot_string)
    occurred_sessions = set()

    # Map (student_id, date, time_slot) -> status
    student_attendance_map = {}

    for record in attendance_records:
        occurred_sessions.add((record.date, record.time_slot))
        student_attendance_map[(record.student_id, record.date, record.time_slot)] = record.status

    # 2. Get all students and their schedules
    all_students = Student.query.options(db.joinedload(Student.schedules)).all()

    # 2.1 Get actual present counts for all students in this period (to handle make-up sessions)
    actual_present_counts = db.session.query(
        Attendance.student_id, func.count(Attendance.id)
    ).filter(
        Attendance.date >= start_of_month,
        Attendance.date <= end_date,
        Attendance.status == 'present'
    ).group_by(Attendance.student_id).all()
    actual_present_map = {s_id: count for s_id, count in actual_present_counts}

    # 3. Calculate absences for each student
    student_absence_counts = {} # student_id -> count

    for student in all_students:
        required_sessions_count = 0

        # Convert student's schedule to a more usable format: {day_of_week: [time_slots]}
        student_schedules = {}
        for sched in student.schedules:
            if sched.day_of_week not in student_schedules:
                student_schedules[sched.day_of_week] = []
            student_schedules[sched.day_of_week].append(sched.time_slot)

        # Count sessions that occurred and were in the student's schedule
        for sess_date, sess_slot in occurred_sessions:
            iso_day = sess_date.isoweekday()
            db_day = iso_day + 1 if iso_day < 7 else None

            if db_day and db_day in student_schedules:
                if sess_slot in student_schedules[db_day]:
                    required_sessions_count += 1

        # Actual sessions attended (including make-ups)
        actual_present = actual_present_map.get(student.id, 0)

        # Absences = Required - Actual (but not less than 0)
        absence_count = max(0, required_sessions_count - actual_present)

        if absence_count >= 2:
            student_absence_counts[student.id] = absence_count

    # 4. Prepare data for display
    student_data = []
    # Filter students who have >= 1 absence
    students_with_absences = [s for s in all_students if s.id in student_absence_counts]

    for s in students_with_absences:
        student_data.append({
            'id': s.id,
            'name': s.name,
            'grade': s.grade,
            'school': s.school,
            'schedules': s.schedules,
            'father_phone': s.father_phone,
            'mother_phone': s.mother_phone,
            'absence_count': student_absence_counts[s.id]
        })

    # Sort by absence_count descending
    student_data.sort(key=lambda x: x['absence_count'], reverse=True)

    # Pagination
    page = request.args.get('page', 1, type=int)
    per_page = 25
    total = len(student_data)
    start = (page - 1) * per_page
    end = start + per_page
    paginated_data = student_data[start:end]

    pagination = Pagination(page, per_page, total, paginated_data)

    # Prepare filter options
    months = [{'value': i, 'name': f'Tháng {i}'} for i in range(1, 13)]
    # Create years list from current year down to 2023
    years = range(current_date.year, 2022, -1)

    return render_template('dashboard.html',
                           students=paginated_data,
                           pagination=pagination,
                           datetime=datetime,
                           start_date=start_of_month,
                           end_date=end_date,
                           selected_month=selected_month,
                           selected_year=selected_year,
                           months=months,
                           years=years)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# User Management Routes (Admin only)
@app.route('/users')
@login_required
def users():
    if current_user.role != 'admin':
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        abort(403)
    all_users = db.session.query(User).all()
    return render_template('users.html', users=all_users)

@app.route('/user/add', methods=['GET', 'POST'])
@login_required
def add_user():
    if current_user.role != 'admin':
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        abort(403)
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        role = request.form['role']

        if db.session.query(User).filter_by(username=username).first():
            flash('Tên đăng nhập đã tồn tại.', 'danger')
            return redirect(url_for('add_user'))

        new_user = User(username=username, password=generate_password_hash(password), role=role, is_admin=(role == 'admin'))
        db.session.add(new_user)
        db.session.commit()
        flash(f'Người dùng {username} đã được thêm thành công.', 'success')
        return redirect(url_for('users'))

    return render_template('user_form.html')

@app.route('/user/edit/<int:user_id>', methods=['GET', 'POST'])
@login_required
def edit_user(user_id):
    if current_user.role != 'admin':
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        abort(403)
    user = db.session.query(User).get_or_404(user_id)
    if request.method == 'POST':
        user.username = request.form['username']
        if 'password' in request.form and request.form['password']:
            user.password = generate_password_hash(request.form['password'])
        user.role = request.form['role']
        db.session.commit()
        flash(f'Người dùng {user.username} đã được cập nhật thành công.', 'success')
        return redirect(url_for('users'))
    return render_template('user_form.html', user=user)

@app.route('/user/delete/<int:user_id>', methods=['POST'])
@login_required
def delete_user(user_id):
    if current_user.role != 'admin':
        flash('Bạn không có quyền thực hiện hành động này.', 'danger')
        abort(403)
    user = db.session.query(User).get_or_404(user_id)
    if user.id == current_user.id:
        flash('Bạn không thể tự xóa tài khoản của mình.', 'danger')
        return redirect(url_for('users'))
    db.session.delete(user)
    db.session.commit()
    flash(f'Người dùng {user.username} đã được xóa.', 'success')
    return redirect(url_for('users'))

# Student management routes (Admin only)
def sort_students_by_grade_and_name(students_list):
    def get_grade_sort_key(s):
        grade_num = 999 # Default value
        try:
            match = re.search(r'\d+', s.grade)
            if match:
                grade_num = int(match.group())
            # If no match, grade_num remains 999
        except (ValueError, AttributeError):
            grade_num = 999 # Handle errors by setting to 999
        return (
            grade_num,  # Sắp xếp theo khối lớp
            s.name.split()[-1] if s.name and len(s.name.split()) > 0 else ''   # Sắp xếp theo từ cuối cùng của tên
        )
    students_list.sort(key=get_grade_sort_key)
    return students_list

class CustomPagination:
    def __init__(self, items, page, per_page, total):
        self.items = items
        self.page = page
        self.per_page = per_page
        self.total = total
        self.pages = (total + per_page - 1) // per_page
        self.has_prev = page > 1
        self.has_next = page < self.pages
        self.prev_num = page - 1 if self.has_prev else None
        self.next_num = page + 1 if self.has_next else None

    def iter_pages(self, left_edge=2, left_current=2, right_current=5, right_edge=2):
        last = 0
        for num in range(1, self.pages + 1):
            if num <= left_edge or \
               (num > self.page - left_current - 1 and \
                num < self.page + right_current) or \
               num > self.pages - right_edge:
                if last + 1 != num:
                    yield None
                yield num
                last = num

@app.route('/students')
@login_required
def students():
    if current_user.role not in ['admin', 'giaovien']:
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        abort(403)

    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)  # Default to 10 items per page

    filtered_students = Student.query

    search_query = request.args.get('search_query', '').strip()
    grade_filter = request.args.get('grade_filter', '')
    day_of_week_filter = request.args.get('day_of_week_filter', '') # NEW: Get day of week filter
    time_slot_filter = request.args.get('time_slot_filter', '')
    attendance_filter = request.args.get('attendance_filter', '') # Filter by current month attendance count
    sort_by = request.args.get('sort_by', 'grade')
    order = request.args.get('order', 'asc')

    # Get unique grades for filter dropdown
    grades = db.session.query(Student.grade).distinct().order_by(func.cast(func.replace(Student.grade, 'Lớp ', ''), db.Integer)).all()
    grades = [g[0] for g in grades if g[0]]

    # Define available time slots (same as in student_form.html)
    time_slots = ['Ca 1: 18h -19h30', 'Ca 2: 19h30 - 21h']

    # Define available days of week for filter dropdown
    days_of_week = {2: 'Thứ 2', 3: 'Thứ 3', 4: 'Thứ 4', 5: 'Thứ 5', 6: 'Thứ 6', 7: 'Thứ 7'}

    # Define attendance filter options
    attendance_options = [
        {'value': '0', 'label': '0 buổi'},
        {'value': '1-3', 'label': '1-3 buổi'},
        {'value': '4-7', 'label': '4-7 buổi'},
        {'value': '8+', 'label': '>= 8 buổi'}
    ]

    # Base query with eager loading for schedules
    query = db.session.query(Student).outerjoin(Schedule).options(db.joinedload(Student.schedules))

    # If current user is a teacher, filter students by assigned students
    if current_user.role == 'giaovien':
        assigned_student_ids = [assignment.student_id for assignment in TeacherStudentAssignment.query.filter_by(teacher_id=current_user.id).all()]
        query = query.filter(Student.id.in_(assigned_student_ids))

    # Apply search filter
    if search_query:
        query = query.filter(
            or_(
                Student.name.ilike(f'%{search_query}'),
                Student.normalized_name.ilike(f'%{remove_diacritics(search_query)}'),
                Student.excel_student_id.ilike(f'%{search_query}%')
            )
        )

    # Apply grade filter
    if grade_filter:
        query = query.filter(Student.grade == grade_filter)

    # Apply combined day and time slot filter
    if day_of_week_filter and time_slot_filter:
        query = query.filter(Student.schedules.any(
            and_(
                Schedule.day_of_week == int(day_of_week_filter), # Convert to int for comparison
                Schedule.time_slot == time_slot_filter
            )
        ))
    elif day_of_week_filter: # Filter only by day if time_slot is not selected
        query = query.filter(Student.schedules.any(Schedule.day_of_week == int(day_of_week_filter)))
    elif time_slot_filter: # Filter only by time slot if day is not selected
        query = query.filter(Student.schedules.any(Schedule.time_slot == time_slot_filter))

    query = query.distinct()

    # Lấy tất cả học sinh phù hợp với các bộ lọc DB
    all_filtered_students = query.all()

    # --- Attendance Calculation & Sorting Logic ---
    today = datetime.now().date()
    curr_month_start = today.replace(day=1)

    first_day_curr_month = today.replace(day=1)
    prev_month_end = first_day_curr_month - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)

    # Calculate for ALL filtered students to allow sorting and filtering
    student_ids = [s.id for s in all_filtered_students]
    curr_map = {}
    prev_map = {}

    if student_ids:
        # Current month counts
        curr_counts = db.session.query(
            Attendance.student_id, func.count(Attendance.id)
        ).filter(
            Attendance.student_id.in_(student_ids),
            Attendance.status == 'present',
            Attendance.date >= curr_month_start,
            Attendance.date <= today
        ).group_by(Attendance.student_id).all()
        curr_map = {s_id: count for s_id, count in curr_counts}

        # Previous month counts
        prev_counts = db.session.query(
            Attendance.student_id, func.count(Attendance.id)
        ).filter(
            Attendance.student_id.in_(student_ids),
            Attendance.status == 'present',
            Attendance.date >= prev_month_start,
            Attendance.date <= prev_month_end
        ).group_by(Attendance.student_id).all()
        prev_map = {s_id: count for s_id, count in prev_counts}

    # Attach data to objects
    for s in all_filtered_students:
        s.present_count_current = curr_map.get(s.id, 0)
        s.present_count_prev = prev_map.get(s.id, 0)
        # Adjust timezone for last_update_at here as we iterate anyway
        if s.last_update_at:
             s.last_update_at = s.last_update_at + timedelta(hours=7)

    # Filter by attendance if needed
    if attendance_filter:
        filtered_by_attendance = []
        for s in all_filtered_students:
            count = s.present_count_current
            include = False
            if attendance_filter == '0':
                if count == 0: include = True
            elif attendance_filter == '1-3':
                if 1 <= count <= 3: include = True
            elif attendance_filter == '4-7':
                if 4 <= count <= 7: include = True
            elif attendance_filter == '8+':
                if count >= 8: include = True

            if include:
                filtered_by_attendance.append(s)
        all_filtered_students = filtered_by_attendance

    # Sort
    reverse = (order == 'desc')

    def get_sort_key(s):
        if sort_by == 'name':
            # Sort by last word of name
            name_parts = s.name.split() if s.name else []
            last_name = name_parts[-1] if name_parts else ''
            return (last_name, s.name)
        elif sort_by == 'grade':
             grade_num = 999
             try:
                 match = re.search(r'\d+', s.grade)
                 if match: grade_num = int(match.group())
             except: pass
             name_parts = s.name.split() if s.name else []
             last_name = name_parts[-1] if name_parts else ''
             return (grade_num, last_name)
        elif sort_by == 'prev_month':
            name_parts = s.name.split() if s.name else []
            last_name = name_parts[-1] if name_parts else ''
            return (s.present_count_prev, last_name)
        elif sort_by == 'curr_month':
            name_parts = s.name.split() if s.name else []
            last_name = name_parts[-1] if name_parts else ''
            return (s.present_count_current, last_name)
        elif sort_by == 'id':
            return s.id
        else:
             # Default grade
             grade_num = 999
             try:
                 match = re.search(r'\d+', s.grade)
                 if match: grade_num = int(match.group())
             except: pass
             name_parts = s.name.split() if s.name else []
             last_name = name_parts[-1] if name_parts else ''
             return (grade_num, last_name)

    all_filtered_students.sort(key=get_sort_key, reverse=reverse)

    # Pagination
    total = len(all_filtered_students)
    start = (page - 1) * per_page
    end = start + per_page
    paginated_students = all_filtered_students[start:end]

    students_pagination = Pagination(page, per_page, total, paginated_students)
    students = paginated_students
    current_url = build_current_url()

    return render_template('students.html', students=students, search_query=search_query, grade_filter=grade_filter, day_of_week_filter=day_of_week_filter, time_slot_filter=time_slot_filter, attendance_filter=attendance_filter, available_grades=grades, available_time_slots=time_slots, available_days_of_week=days_of_week, attendance_options=attendance_options, students_pagination=students_pagination, current_user_role=current_user.role, prev_month_name=f"Tháng {prev_month_start.month}", curr_month_name=f"Tháng {curr_month_start.month}", sort_by=sort_by, order=order)
@app.route('/students/management', methods=['GET'])
@login_required
def student_management():
    if current_user.role != 'admin':
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        abort(403)
    students = Student.query.all()
    students = sort_students_by_grade_and_name(students)
    current_url = build_current_url()
    return render_template('student_management.html', students=students, current_url=current_url)

@app.route('/bulk_update_tuition_status', methods=['POST'])
@admin_required
def bulk_update_tuition_status():
    if request.method == 'POST':
        tuition_ids_on_page = request.form.getlist('tuition_ids')

        if not tuition_ids_on_page:
            flash('Không có học phí nào để cập nhật.', 'warning')
            return redirect_back('tuition_management')

        try:
            for tuition_id in tuition_ids_on_page:
                is_paid = True if request.form.get(f'is_paid_{tuition_id}') == 'on' else False
                tuition_record = Tuition.query.get(tuition_id)
                if tuition_record:
                    tuition_record.is_paid = is_paid
            db.session.commit()
            flash('Đã cập nhật trạng thái học phí thành công.', 'success')
        except Exception as e:
            flash(f'Lỗi khi cập nhật học phí: {e}', 'danger')
            db.session.rollback()

    return redirect_back('tuition_management')

@app.route('/tuition_management', methods=['GET', 'POST'])
@login_required
def tuition_management():
    if not current_user.is_admin:
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        return redirect(url_for('dashboard'))

    # Logic for filtering and displaying tuition will go here
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int) # Default to 50 items per page

    current_month = datetime.now().month
    current_year = datetime.now().year

    month = int(request.args.get('month', current_month))
    year = int(request.args.get('year', current_year))
    search_query = request.args.get('search_query', '').strip()
    grade_filter = request.args.get('grade_filter', 'all')
    payment_status = request.args.get('payment_status', 'all')

    # Start with a query for all students
    students_query = Student.query

    # Apply search query filter
    if search_query:
        students_query = students_query.filter(
            or_(
                Student.name.ilike(f'%{search_query}%'),
                Student.normalized_name.ilike(f'%{remove_diacritics(search_query)}%')
            )
        )

    # Apply grade filter
    if grade_filter != 'all':
        students_query = students_query.filter(Student.grade == grade_filter)

    # Get all students from the query, then sort them using the custom function
    all_students = students_query.all()
    sorted_students = sort_students_by_grade_and_name(all_students)

    # Manually paginate the sorted list
    total = len(sorted_students)
    start = (page - 1) * per_page
    end = start + per_page
    students = sorted_students[start:end]

    # Create a CustomPagination object for rendering in the template
    students_pagination = CustomPagination(students, page, per_page, total)

    tuition_data = []
    for student in students:
        tuition = Tuition.query.filter_by(student_id=student.id, month=month, year=year).first()
        if not tuition:
            # Create a default tuition record if it doesn't exist
            amount = student.default_monthly_fee
            tuition = Tuition(student_id=student.id, month=month, year=year, amount_due=amount, is_paid=False)
            db.session.add(tuition)
            db.session.commit()

        # Apply custom fee if it exists
        display_amount = tuition.custom_fee if tuition.custom_fee is not None else tuition.amount_due

        # Apply payment status filter
        if payment_status == 'all' or \
           (payment_status == 'paid' and tuition.is_paid) or \
           (payment_status == 'unpaid' and not tuition.is_paid):
            tuition_data.append({
                'student_id': student.id,
                'student_name': student.name,
                'grade': student.grade,
                'father_phone': student.father_phone,
                'mother_phone': student.mother_phone,
                'amount_due': display_amount,
                'is_paid': tuition.is_paid,
                'tuition_id': tuition.id,
                'custom_fee': tuition.custom_fee
            })

    # Get all unique grades for the filter dropdown and sort them numerically
    raw_grades = db.session.query(Student.grade).distinct().all()
    processed_grades = []
    for grade_tuple in raw_grades:
        grade_string = grade_tuple[0]
        match = re.search(r'\d+', grade_string)
        if match:
            processed_grades.append(int(match.group()))

    all_grades = sorted(list(set(processed_grades)))
    available_grades = [f'Lớp {i}' for i in all_grades]

    # Get available months and years for filter dropdowns
    available_months = range(1, 13)
    available_years = range(current_year - 5, current_year + 2) # 5 years back, current year, next year

    # Calculate total collected and uncollected amounts for ALL tuition records of the month
    all_tuition_data = []
    for student in sorted_students:
        tuition = Tuition.query.filter_by(student_id=student.id, month=month, year=year).first()
        if not tuition:
            amount = student.default_monthly_fee
            tuition = Tuition(student_id=student.id, month=month, year=year, amount_due=amount, is_paid=False)
            db.session.add(tuition)
            db.session.commit()

        display_amount = tuition.custom_fee if tuition.custom_fee is not None else tuition.amount_due

        if payment_status == 'all' or \
           (payment_status == 'paid' and tuition.is_paid) or \
           (payment_status == 'unpaid' and not tuition.is_paid):
            all_tuition_data.append({
                'student_id': student.id,
                'student_name': student.name,
                'grade': student.grade,
                'father_phone': student.father_phone,
                'mother_phone': student.mother_phone,
                'amount_due': display_amount,
                'is_paid': tuition.is_paid,
                'tuition_id': tuition.id,
                'custom_fee': tuition.custom_fee
            })

    total_collected_amount = sum((item['amount_due'] or 0) for item in all_tuition_data if item['is_paid'])
    total_uncollected_amount = sum((item['amount_due'] or 0) for item in all_tuition_data if not item['is_paid'])
    current_url = build_current_url()

    return render_template('tuition_management.html',
                           tuition_data=tuition_data,
                           month=month,
                           year=year,
                           search_query=search_query,
                           grade_filter=grade_filter,
                           payment_status=payment_status,
                           available_months=available_months,
                           available_years=available_years,
                           available_grades=available_grades,
                           pagination=students_pagination,
                           per_page=per_page,
                           record_count=students_pagination.total,
                           total_collected_amount=total_collected_amount,
                           total_uncollected_amount=total_uncollected_amount,
                           current_url=current_url)

@app.route('/tuition/update_status', methods=['POST'])
@login_required
def update_tuition_status():
    if not current_user.is_admin:
        flash('Bạn không có quyền thực hiện hành động này.', 'danger')
        return redirect(url_for('dashboard'))

    tuition_id = request.form.get('tuition_id')
    is_paid = request.form.get('is_paid') == 'true'

    tuition = Tuition.query.get(tuition_id)
    if tuition:
        tuition.is_paid = is_paid
        db.session.commit()
        flash('Cập nhật trạng thái học phí thành công.', 'success')
    else:
        flash('Không tìm thấy bản ghi học phí.', 'danger')

    return redirect_back('tuition_management')

@app.route('/tuition/update_fee', methods=['POST'])
@login_required
def update_tuition_fee():
    if not current_user.is_admin:
        flash('Bạn không có quyền thực hiện hành động này.', 'danger')
        return redirect(url_for('dashboard'))

    tuition_id = request.form.get('tuition_id')
    custom_fee = request.form.get('custom_fee')
    month = request.form.get('month')
    year = request.form.get('year')
    search_query = request.form.get('search_query')

    tuition = Tuition.query.get(tuition_id)
    if tuition:
        try:
            custom_fee = float(custom_fee) if custom_fee else None

            # Update custom_fee ONLY for this specific month/year
            # Do NOT affect other months
            tuition.custom_fee = custom_fee
            db.session.commit()
            print(f"Successfully updated custom_fee for tuition {tuition_id} (Student {tuition.student_id}, {month}/{year}) to: {custom_fee}")
            flash(f'Cập nhật học phí tùy chỉnh thành công cho tháng {month}/{year}.', 'success')
        except ValueError:
            print(f"ValueError: Invalid custom_fee received: {custom_fee}")
            flash('Mức học phí tùy chỉnh không hợp lệ.', 'danger')
        except Exception as e:
            print(f"Database commit error: {e}")
            db.session.rollback()
            flash('Đã xảy ra lỗi khi cập nhật học phí.', 'danger')
    else:
        print(f"No tuition record found for ID: {tuition_id}")
        flash('Không tìm thấy bản ghi học phí.', 'danger')

    return redirect_back('tuition_management')

@app.route('/student/add', methods=['GET', 'POST'])
@login_required
def add_student():
    return_to = request.values.get('return_to') or request.referrer or url_for('students')

    if current_user.role != 'admin':
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        abort(403)
    if request.method == 'POST':
        name = request.form['name']
        excel_student_id = request.form.get('excel_student_id')
        grade = request.form.get('grade')
        school = request.form.get('school')
        father_name = request.form.get('father_name')
        father_phone = request.form.get('father_phone')
        father_occupation = request.form.get('father_occupation')
        mother_name = request.form.get('mother_name')
        mother_phone = request.form.get('mother_phone')
        mother_occupation = request.form.get('mother_occupation')
        address = request.form.get('address')

        if not name:
            flash('Tên học sinh không được để trống.', 'danger')
            return render_template('student_form.html', student=None, return_to=return_to)

        if not grade:
            flash('Khối lớp không được để trống.', 'danger')
            return render_template('student_form.html', student=None, return_to=return_to)

        if current_user.role == 'admin' and not school:
            flash('Trường học không được để trống.', 'danger')
            return render_template('student_form.html', student=None, return_to=return_to)

        # Check if excel_student_id already exists if provided
        if excel_student_id and db.session.query(Student).filter_by(excel_student_id=excel_student_id).first():
            flash(f'Mã học sinh Excel "{excel_student_id}" đã tồn tại.', 'danger')
            return render_template('student_form.html', student=None, return_to=return_to)

        new_student = Student(
            name=name,
            normalized_name=name.lower(), # Add this line
            excel_student_id=excel_student_id,
            grade=grade,
            school=school,
            father_name=father_name,
            father_phone=father_phone,
            father_occupation=father_occupation,
            mother_name=mother_name,
            mother_phone=mother_phone,
            mother_occupation=mother_occupation,
            address=address,

            comment=request.form.get('comment')
        )
        db.session.add(new_student)
        db.session.flush() # Flush to get new_student.id for schedules

        # Handle schedules (only if coming from detailed form, which has schedule fields)
        days_of_week = {2: 'Monday', 3: 'Tuesday', 4: 'Wednesday', 5: 'Thursday', 6: 'Friday', 7: 'Saturday'}
        double_slot_value = '__double_slot__'
        base_time_slots = ['Ca 1: 18h -19h30', 'Ca 2: 19h30 - 21h']
        for day_num in days_of_week.keys():
            time_slot = request.form.get(f'day_{day_num}')
            if time_slot == double_slot_value:
                for slot in base_time_slots:
                    new_schedule = Schedule(
                        student_id=new_student.id,
                        day_of_week=day_num,
                        time_slot=slot
                    )
                    db.session.add(new_schedule)
            elif time_slot: # Only add if a slot is selected
                new_schedule = Schedule(
                    student_id=new_student.id,
                    day_of_week=day_num,
                    time_slot=time_slot
                )
                db.session.add(new_schedule)

        # Mặc định phân công nhận xét cho giaovien1
        giaovien1 = User.query.filter_by(username='giaovien1').first()
        if giaovien1:
            new_assignment = TeacherStudentAssignment(
                teacher_id=giaovien1.id,
                student_id=new_student.id
            )
            db.session.add(new_assignment)

        db.session.commit()
        flash(f'Học sinh {name} đã được thêm thành công.', 'success')
        return redirect_back('students')
    return render_template('student_form.html', student=None, return_to=return_to)

# NEW ROUTE: For quick adding student from student_management.html
@app.route('/student/add_quick', methods=['POST'])
@login_required
def add_quick_student():
    if current_user.role != 'admin':
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        abort(403)
    name = request.form['name']
    excel_student_id = request.form.get('excel_student_id')

    if not name:
        flash('Tên học sinh không được để trống.', 'danger')
        return redirect_back('student_management')

    if excel_student_id and db.session.query(Student).filter_by(excel_student_id=excel_student_id).first():
        flash(f'Mã học sinh Excel "{excel_student_id}" đã tồn tại.', 'danger')
        return redirect_back('student_management')

    new_student = Student(
        name=name,
        normalized_name=name.lower(), # Add this line
        excel_student_id=excel_student_id,
        created_by=current_user.id
    )
    db.session.add(new_student)
    db.session.flush() # Flush to get new_student.id

    # Mặc định phân công nhận xét cho giaovien1
    giaovien1 = User.query.filter_by(username='giaovien1').first()
    if giaovien1:
        new_assignment = TeacherStudentAssignment(
            teacher_id=giaovien1.id,
            student_id=new_student.id
        )
        db.session.add(new_assignment)

    db.session.commit()
    flash(f'Học sinh {name} đã được thêm nhanh thành công.', 'success')
    return redirect_back('student_management')

@app.route('/student/edit/<int:student_id>', methods=['GET', 'POST'])
@login_required
def edit_student(student_id):
    student = db.session.query(Student).options(db.joinedload(Student.schedules)).get_or_404(student_id)
    return_to = request.values.get('return_to') or request.referrer or url_for('students')

    if current_user.role not in ['admin', 'giaovien']:
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        abort(403)

    # For teachers, check if they are assigned to this student
    if current_user.role == 'giaovien':
        assignment = TeacherStudentAssignment.query.filter_by(teacher_id=current_user.id, student_id=student.id).first()
        if not assignment:
            flash('Bạn không có quyền xem thông tin học sinh này.', 'danger')
            return redirect_back('students')

    if request.method == 'POST':
        if current_user.role == 'admin':
            student.name = request.form['name']
            if not student.name:
                flash('Tên học sinh không được để trống.', 'danger')
                return render_template('student_form.html', student=student, current_user_role=current_user.role, return_to=return_to)

            student.normalized_name = request.form['name'].lower() # Add this line
            student.excel_student_id = request.form.get('excel_student_id')
            student.grade = request.form.get('grade')
            if not student.grade:
                flash('Khối lớp không được để trống.', 'danger')
                return render_template('student_form.html', student=student, current_user_role=current_user.role, return_to=return_to)

            student.school = request.form.get('school')
            if current_user.role == 'admin' and not student.school:
                flash('Trường học không được để trống.', 'danger')
                return render_template('student_form.html', student=student, current_user_role=current_user.role, return_to=return_to)
            student.father_name = request.form.get('father_name')
            student.father_phone = request.form.get('father_phone')
            student.father_occupation = request.form.get('father_occupation')
            student.mother_name = request.form.get('mother_name')
            student.mother_phone = request.form.get('mother_phone')
            student.mother_occupation = request.form.get('mother_occupation')
            student.address = request.form.get('address')
            student.notes = request.form.get('notes')
            student.comment = request.form.get('comment')
            student.last_comment_date = datetime.now() # Update comment date

            # Check if excel_student_id already exists for another student
            if student.excel_student_id:
                existing_student_with_excel_id = db.session.query(Student).filter(
                    Student.excel_student_id == student.excel_student_id,
                    Student.id != student_id
                ).first()
                if existing_student_with_excel_id:
                    flash(f'Mã học sinh Excel "{student.excel_student_id}" đã tồn tại cho học sinh khác.', 'danger')
                    return render_template('student_form.html', student=student, current_user_role=current_user.role, return_to=return_to)

            # Update schedules
            # First, delete all existing schedules for this student
            db.session.query(Schedule).filter_by(student_id=student.id).delete()

            # Then, add new schedules based on form data
            days_of_week = {2: 'Monday', 3: 'Tuesday', 4: 'Wednesday', 5: 'Thursday', 6: 'Friday', 7: 'Saturday'}
            double_slot_value = '__double_slot__'
            base_time_slots = ['Ca 1: 18h -19h30', 'Ca 2: 19h30 - 21h']
            for day_num in days_of_week.keys():
                time_slot = request.form.get(f'day_{day_num}')
                if time_slot == double_slot_value:
                    for slot in base_time_slots:
                        new_schedule = Schedule(
                            student_id=student.id,
                            day_of_week=day_num,
                            time_slot=slot
                        )
                        db.session.add(new_schedule)
                elif time_slot: # Only add if a slot is selected
                    new_schedule = Schedule(
                        student_id=student.id,
                        day_of_week=day_num,
                        time_slot=time_slot
                    )
                    db.session.add(new_schedule)
        elif current_user.role == 'giaovien':
            # Check if the teacher is assigned to this student
            assignment = TeacherStudentAssignment.query.filter_by(teacher_id=current_user.id, student_id=student.id).first()
            if not assignment:
                flash('Bạn không có quyền cập nhật nhận xét cho học sinh này.', 'danger')
                return redirect_back('students')
            student.comment = request.form.get('comment')

        db.session.commit()
        flash(f'Học sinh {student.name} đã được cập nhật thành công.', 'success')
        return redirect_back('students')
    return render_template('student_form.html', student=student, current_user_role=current_user.role, return_to=return_to)

@app.route('/student/delete/<int:student_id>', methods=['POST'])
@login_required
def delete_student(student_id):
    if current_user.role != 'admin':
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        abort(403)
    student = db.session.query(Student).get_or_404(student_id)

    # Delete associated schedules
    db.session.query(Schedule).filter_by(student_id=student.id).delete()
    # Delete associated attendance records
    db.session.query(Attendance).filter_by(student_id=student.id).delete()
    # Delete associated tuition records
    db.session.query(Tuition).filter_by(student_id=student.id).delete()

    db.session.delete(student)
    db.session.commit()
    flash(f'Học sinh {student.name} và tất cả dữ liệu liên quan đã được xóa thành công.', 'success')
    return redirect_back('students')

# Attendance routes (Admin and Giaovien)
@app.route('/take_attendance', methods=['GET', 'POST'])
@login_required
def take_attendance():

    if current_user.role not in ['admin', 'giaovien']:
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        abort(403)

    selected_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    date_str = selected_date # Initialize date_str for GET requests
    attendance_date = datetime.strptime(selected_date, '%Y-%m-%d').date() # Initialize attendance_date for GET requests
    selected_slot = request.args.get('slot', '')
    time_slot = selected_slot # Initialize time_slot for GET requests
    recorded_by_filter = request.args.get('recorded_by_filter', '')
    available_users = User.query.all()
    selected_grade = request.args.get('grade', '').strip()
    search_query = request.args.get('search_query', '') # Truyền search_query vào template
    # Normalize the search query for diacritic-insensitive comparison
    normalized_search_query = remove_diacritics(search_query) if search_query else ''

    students_to_display = []
    search_results = []
    existing_attendance_map = {}
    scheduled_student_ids = []
    all_students_involved = []
    total_students_in_slot = 0
    total_students_displayed = 0

    if request.method == 'POST':
        date_str = request.form.get('date')
        time_slot = request.form.get('time_slot')

        if not date_str or not time_slot:
            flash('Date and Time Slot are required to save attendance.', 'error')
            return redirect_back('take_attendance')

        try:
            attendance_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            flash('Invalid date format.', 'error')
            return redirect_back('take_attendance')

        # Re-determine students for the selected date and time slot
        attendance_date_obj = attendance_date
        day_map = {1:2, 2:3, 3:4, 4:5, 5:6, 6:7, 7:None}
        iso_day = attendance_date_obj.isoweekday()
        day_of_week_for_schedule = day_map.get(iso_day)

        students_on_schedule = []
        scheduled_students_for_day = []
        selected_grade = request.form.get('grade', '').strip()

        if day_of_week_for_schedule:
            scheduled_students_query = db.session.query(Student).join(Schedule).filter(
                Schedule.day_of_week == day_of_week_for_schedule,
                Schedule.time_slot == time_slot
            )
            if selected_grade:
                scheduled_students_query = scheduled_students_query.filter(Student.grade == selected_grade)
            scheduled_students_for_day = scheduled_students_query.all()
            scheduled_student_ids = [s.id for s in scheduled_students_for_day]

        all_students_expected_ids = {s.id for s in scheduled_students_for_day}
        submitted_student_ids = set()

        for student_id_str in request.form.keys():
            if student_id_str.startswith('attendance_'):
                try:
                    student_id = int(student_id_str.replace('attendance_', ''))
                    submitted_student_ids.add(student_id)
                except ValueError:
                    flash(f'Invalid student ID in attendance data: {student_id_str}', 'error')
                    continue

                status = 'present' if request.form.get(student_id_str) == 'present' else 'absent'

                existing_attendance = db.session.query(Attendance).filter_by(
                    student_id=student_id,
                    date=attendance_date,
                    time_slot=time_slot
                ).first()

                if existing_attendance:
                    if existing_attendance.status != status:
                        existing_attendance.status = status
                        existing_attendance.update_by = current_user.id
                        existing_attendance.updated_at = datetime.now()
                else:
                    new_attendance = Attendance(
                        student_id=student_id,
                        date=attendance_date,
                        time_slot=time_slot,
                        status=status,
                        created_by=current_user.id,
                        update_by=current_user.id
                    )
                    db.session.add(new_attendance)

        # Handle students who were expected but not submitted (i.e., unticked in the form)
        # → update existing 'present' to 'absent', or CREATE a new 'absent' record if none exists
        for student_id in all_students_expected_ids:
            if student_id not in submitted_student_ids:
                existing_attendance = db.session.query(Attendance).filter_by(
                    student_id=student_id,
                    date=attendance_date,
                    time_slot=time_slot
                ).first()

                if existing_attendance:
                    if existing_attendance.status == 'present':
                        existing_attendance.status = 'absent'
                        existing_attendance.update_by = current_user.id
                        existing_attendance.updated_at = datetime.now()
                else:
                    absent_record = Attendance(
                        student_id=student_id,
                        date=attendance_date,
                        time_slot=time_slot,
                        status='absent',
                        created_by=current_user.id,
                        update_by=current_user.id
                    )
                    db.session.add(absent_record)

        # Handle unscheduled students who were previously marked present but are now unticked
        all_existing_attendance_records = db.session.query(Attendance).filter_by(
            date=attendance_date,
            time_slot=time_slot
        ).all()

        for record in all_existing_attendance_records:
            # Check if the student is not a scheduled student AND not in the submitted form
            if record.student_id not in all_students_expected_ids and record.student_id not in submitted_student_ids:
                db.session.delete(record)

        db.session.commit()
        flash('Điểm danh đã được lưu thành công.', 'success')
        return redirect_back('take_attendance', date=date_str, slot=time_slot)

    # Logic for GET requests - only prepare data for display
    if selected_date and selected_slot:
        attendance_date_obj = datetime.strptime(selected_date, '%Y-%m-%d').date()

        day_map = {1:2, 2:3, 3:4, 4:5, 5:6, 6:7, 7:None}
        iso_day = attendance_date_obj.isoweekday()
        day_of_week_for_schedule = day_map.get(iso_day)

        students_on_schedule = []
        if day_of_week_for_schedule:
            students_on_schedule = get_students_for_slot(db, Student, Schedule, day_of_week_for_schedule, selected_slot)
            if selected_grade:
                students_on_schedule = [student for student in students_on_schedule if student.grade == selected_grade]
            all_students_involved = students_on_schedule

        scheduled_student_ids = [s.id for s in students_on_schedule]

        # Get existing attendance records for the selected date and slot
        query = db.session.query(Attendance).filter(
            Attendance.date == attendance_date_obj,
            Attendance.time_slot == selected_slot
        )
        if selected_grade:
            selected_grade_student_ids = [
                student_id for (student_id,) in db.session.query(Student.id).filter(Student.grade == selected_grade).all()
            ]
            if selected_grade_student_ids:
                query = query.filter(Attendance.student_id.in_(selected_grade_student_ids))
            else:
                query = query.filter(Attendance.student_id.in_([-1]))
        if recorded_by_filter:
            try:
                recorded_by_filter_id = int(recorded_by_filter)
                query = query.filter(or_(and_(Attendance.update_by.isnot(None), Attendance.update_by == recorded_by_filter_id), and_(Attendance.update_by.is_(None), Attendance.created_by == recorded_by_filter_id)))
            except ValueError:
                # Handle case where recorded_by_filter is not a valid integer (e.g., empty string or invalid input)
                pass
        all_attendance_records_for_slot = query.all()

        existing_attendance_map = {}
        for record in all_attendance_records_for_slot:
            existing_attendance_map[record.student_id] = {
                'status': record.status,
                'recorded_by': record.update_by_user.username if record.update_by_user else record.created_by_user.username
            }

        # Update attendance map with existing records
        if recorded_by_filter:
            # Chỉ lấy học sinh có mặt khi có bộ lọc người điểm danh
            all_student_ids_for_display = {record.student_id for record in all_attendance_records_for_slot if record.status == 'present'}
        else:
            all_student_ids_for_display = set(scheduled_student_ids).union({record.student_id for record in all_attendance_records_for_slot})

        # Fetch student details for all relevant IDs
        if all_student_ids_for_display:
            students_to_display = db.session.query(Student).filter(Student.id.in_(all_student_ids_for_display)).all()
            # Sort students by name for consistent display

        # Tính toán số lượng học sinh
        total_students_in_slot = len(students_on_schedule)
        total_students_displayed = len(students_to_display)

        if all_student_ids_for_display:
            # Sắp xếp học sinh theo khối lớp và từ cuối cùng của tên
            students_to_display.sort(key=lambda s: (
                int(s.grade.split(' ')[1]) if s.grade and ' ' in s.grade else 99,  # Sắp xếp theo khối lớp
                s.name.split()[-1] if s.name and len(s.name.split()) > 0 else ''   # Sắp xếp theo từ cuối cùng của tên
            ))

        # Handle search functionality for students not on schedule
        if search_query:
            # Search for students whose names match the normalized search query
            # and are not already in the main display list
            search_results = db.session.query(Student).filter(
                or_(
                    Student.name.ilike(f'%{search_query}'),
                Student.normalized_name.ilike(f'%{normalized_search_query}'),
                    Student.excel_student_id.ilike(f'%{search_query}%')
                ),
                ~Student.id.in_(all_student_ids_for_display) # Exclude students already being displayed
            ).all()

            # Sắp xếp kết quả tìm kiếm theo khối lớp và từ cuối cùng của tên
            search_results.sort(key=lambda s: (
                int(s.grade.split(' ')[1]) if s.grade and ' ' in s.grade else 99,  # Sắp xếp theo khối lớp
                s.name.split()[-1] if s.name and len(s.name.split()) > 0 else ''   # Sắp xếp theo từ cuối cùng của tên
            ))

    # Compute per-recorder present counts for display
    from collections import Counter as _Counter
    _rpc = _Counter()
    _recorders_order = []
    _recorders_seen_set = set()
    for info in existing_attendance_map.values():
        rec = info.get('recorded_by') or 'Không rõ'
        if rec not in _recorders_seen_set:
            _recorders_seen_set.add(rec)
            _recorders_order.append(rec)
        if info.get('status') == 'present':
            _rpc[rec] += 1
    _rc_palette = ['primary', 'success', 'danger', 'warning', 'info', 'dark', 'secondary']
    recorder_colors_ta = {name: _rc_palette[i % len(_rc_palette)] for i, name in enumerate(_recorders_order)}
    recorder_present_counts_ta = {rec: _rpc[rec] for rec in _recorders_order if _rpc[rec] > 0}
    current_url = build_current_url()

    return render_template('take_attendance.html',
                         date=selected_date,
                         selected_slot=selected_slot,
                         selected_grade=selected_grade,
                         students=students_to_display,
                         search_results=search_results,
                         existing_attendance=existing_attendance_map,
                         scheduled_student_ids=scheduled_student_ids,
                         search_query=search_query,
                         available_users=available_users,
                         recorded_by_filter=recorded_by_filter,
                         total_students_in_slot=total_students_in_slot,
                         total_students_displayed=total_students_displayed,
                         recorder_present_counts=recorder_present_counts_ta,
                         recorder_colors=recorder_colors_ta,
                         current_url=current_url)

@app.route('/api/search_students')
@login_required
def api_search_students():
    """AJAX: tìm kiếm học sinh theo tên/mã, trả về JSON."""
    q = request.args.get('q', '').strip()
    date_str = request.args.get('date', '')
    time_slot = request.args.get('time_slot', '')

    if not q:
        return jsonify([])

    normalized_q = remove_diacritics(q).lower()
    all_students = db.session.query(Student).all()
    matched = [s for s in all_students if normalized_q in remove_diacritics(s.name).lower()
               or (s.excel_student_id and q.lower() in s.excel_student_id.lower())]

    # Lấy danh sách id đã có record trong ca này
    already_ids = set()
    if date_str and time_slot:
        try:
            att_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            records = db.session.query(Attendance.student_id).filter_by(date=att_date, time_slot=time_slot).all()
            already_ids = {r[0] for r in records}
        except ValueError:
            pass

    result = [
        {
            'id': s.id,
            'name': s.name,
            'grade': s.grade or '',
            'excel_id': s.excel_student_id or '',
            'already_added': s.id in already_ids,
        }
        for s in matched[:30]
    ]
    return jsonify(result)


@app.route('/api/add_makeup_student', methods=['POST'])
@login_required
def api_add_makeup_student():
    """AJAX: thêm học sinh học bù vào ca điểm danh, trả về JSON."""
    if current_user.role not in ['admin', 'giaovien']:
        return jsonify({'ok': False, 'msg': 'Không có quyền'}), 403

    data = request.get_json(force=True)
    student_id = data.get('student_id')
    date_str = data.get('date')
    time_slot = data.get('time_slot')

    if not student_id or not date_str or not time_slot:
        return jsonify({'ok': False, 'msg': 'Thiếu thông tin'}), 400

    try:
        att_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'ok': False, 'msg': 'Ngày không hợp lệ'}), 400

    existing = db.session.query(Attendance).filter_by(
        student_id=student_id, date=att_date, time_slot=time_slot
    ).first()

    if existing:
        return jsonify({'ok': False, 'msg': 'Học sinh đã có trong ca này'})

    new_att = Attendance(
        student_id=student_id,
        date=att_date,
        time_slot=time_slot,
        status='present',
        created_by=current_user.id,
        update_by=current_user.id
    )
    db.session.add(new_att)
    db.session.commit()

    student = db.session.query(Student).get(student_id)
    return jsonify({'ok': True, 'msg': 'Đã thêm', 'student': {
        'id': student.id,
        'name': student.name,
        'grade': student.grade or '',
    }})


@app.route('/add_unscheduled_student_to_attendance', methods=['POST'])
@login_required
def add_unscheduled_student_to_attendance():
    if current_user.role not in ['admin', 'giaovien']:
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        abort(403)

    date_str = request.form.get('date')
    time_slot = request.form.get('time_slot')
    search_query = request.form.get('search_query')
    student_id_to_add = request.form.get('student_id_to_add')
    grade_filter = request.form.get('grade')

    scheduled_student_ids = []

    if not date_str or not time_slot:
        flash('Date and Time Slot are required.', 'error')
        return redirect_back('take_attendance')

    try:
        attendance_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        flash('Invalid date format.', 'error')
        return redirect_back('take_attendance')

    if student_id_to_add:
        student_id = int(student_id_to_add)
        existing_attendance = db.session.query(Attendance).filter_by(
            student_id=student_id,
            date=attendance_date,
            time_slot=time_slot
        ).first()

        if existing_attendance:
            flash(f'Student already marked for attendance on {date_str} in {time_slot}.', 'info')
        else:
            attendance = Attendance(
                student_id=student_id,
                date=attendance_date,
                time_slot=time_slot,
                status='present',
                created_by=current_user.id,
                update_by=current_user.id
            )
            db.session.add(attendance)
            db.session.commit()
            flash(f'Student added to attendance for {date_str} - {time_slot}.', 'success')

        return redirect_back('take_attendance', date=date_str, slot=time_slot, search_query=search_query)

    elif search_query:
        search_results = db.session.query(Student).filter(
            or_(
                Student.name.ilike(f'%{search_query}%'),
                Student.normalized_name.ilike(f'%{remove_diacritics(search_query)}%'),
                Student.excel_student_id.ilike(f'%{search_query}%')
            )
        ).order_by(func.cast(func.replace(Student.grade, 'Lớp ', ''), db.Integer))

        # Lọc theo khối lớp nếu được chọn
        # if grade_filter:
        #     search_results = search_results.filter(Student.grade == grade_filter)

        # print("DEBUG: Entering search results processing block.")
        search_results = search_results.all()
        # print(f"DEBUG: Search Results Count: {len(search_results)}")
        # print(f"DEBUG: Sorted Search Results Grades: {[s.grade for s in search_results]}")

        students_on_schedule = []
        day_map = {1:2, 2:3, 3:4, 4:5, 5:6, 6:7, 7:None}
        iso_day = datetime.strptime(date_str, '%Y-%m-%d').isoweekday()
        day_of_week_for_schedule = day_map.get(iso_day)

        if day_of_week_for_schedule:
            scheduled_students_for_day = db.session.query(Student).join(Schedule).filter(Schedule.day_of_week == day_of_week_for_schedule).filter(Schedule.time_slot == time_slot).all()
            scheduled_student_ids = [s.id for s in students_on_schedule]

        existing_attendance_map = {}
        if students_on_schedule or search_results:
            all_student_ids = list(set([s.id for s in students_on_schedule] + [s.id for s in search_results]))
            if all_student_ids:
                existing_records = db.session.query(Attendance).filter(
                    Attendance.date == datetime.strptime(date_str, '%Y-%m-%d').date(),
                    Attendance.time_slot == time_slot,
                    Attendance.student_id.in_(all_student_ids)
                ).all()
                for record in existing_records:
                    existing_attendance_map[record.student_id] = record.status

        return render_template('attendance.html',
                               date=date_str,
                               selected_slot=time_slot,
                               # selected_grade=grade_filter, # Removed grade filter
                               students=[], # Explicitly pass empty list for students
                               search_results=search_results,
                               existing_attendance=existing_attendance_map,
                               scheduled_student_ids=scheduled_student_ids,
                               search_query=search_query)

    flash('No student selected or search query provided.', 'error')
    return redirect_back('take_attendance', date=date_str, slot=time_slot)

@app.route('/attendance_history')
@login_required
def attendance_history():
    if current_user.role != 'admin':
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        return redirect(url_for('dashboard'))
    from collections import defaultdict
    today_str = datetime.now().strftime('%Y-%m-%d')
    start_date_str = request.args.get('start_date', today_str)
    end_date_str = request.args.get('end_date', today_str)
    time_slot = request.args.get('time_slot')
    selected_student_id = request.args.get('selected_student_id', type=int)
    grade_filter = request.args.get('grade_filter')
    student_search_query = request.args.get('student_search_query')
    recorded_by_filter = request.args.get('recorded_by_filter')
    selected_status = request.args.get('status_filter')
    if selected_status == '':
        selected_status = None
    if recorded_by_filter:
        try:
            recorded_by_filter = int(recorded_by_filter)
        except ValueError:
            recorded_by_filter = None

    available_users = db.session.query(User).all()

    student_search_results = []
    selected_student_obj = None

    if student_search_query:
        normalized_query = remove_diacritics(student_search_query)
        all_students = db.session.query(Student).all()
        student_search_results = [
            student for student in all_students
            if normalized_query.lower() in remove_diacritics(student.name).lower()
        ]

    if selected_student_id:
        selected_student_obj = db.session.query(Student).get(selected_student_id)

    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)

    grades = db.session.query(Student.grade).distinct().order_by(Student.grade).all()
    grades = [g[0] for g in grades if g[0]]

    # Query only real attendance records (no inferred absences)
    User_updater = aliased(User)
    attendance_query = db.session.query(
            Attendance,
            User.username.label('created_by_username'),
            User_updater.username.label('updated_by_username')
        )\
        .outerjoin(User, Attendance.created_by == User.id)\
        .outerjoin(User_updater, Attendance.update_by == User_updater.id)

    if start_date_str:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        attendance_query = attendance_query.filter(Attendance.date >= start_date)
    if end_date_str:
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        attendance_query = attendance_query.filter(Attendance.date <= end_date)
    if time_slot and time_slot != 'all':
        attendance_query = attendance_query.filter(Attendance.time_slot == time_slot)
    if selected_student_id:
        attendance_query = attendance_query.filter(Attendance.student_id == selected_student_id)
    if recorded_by_filter:
        try:
            recorded_by_filter_id = int(recorded_by_filter)
            attendance_query = attendance_query.filter(or_(
                and_(Attendance.update_by.isnot(None), Attendance.update_by == recorded_by_filter_id),
                and_(Attendance.update_by.is_(None), Attendance.created_by == recorded_by_filter_id)
            ))
        except ValueError:
            flash('ID người điểm danh không hợp lệ.', 'warning')
            recorded_by_filter = ''
    if selected_status == 'present':
        attendance_query = attendance_query.filter(Attendance.status == 'present')
    elif selected_status == 'absent':
        attendance_query = attendance_query.filter(Attendance.status == 'absent')

    existing_attendances = attendance_query.all()

    # Fetch all relevant student objects
    student_ids = {a.student_id for a, _, _ in existing_attendances}
    students_map = {s.id: s for s in db.session.query(Student).filter(Student.id.in_(student_ids)).all()} if student_ids else {}

    day_map = {1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 7, 7: None}

    # Group records by (date, time_slot)
    sessions_dict = defaultdict(list)
    for attendance, created_by_username, updated_by_username in existing_attendances:
        student = students_map.get(attendance.student_id)
        if not student:
            continue
        if grade_filter and student.grade != grade_filter:
            continue

        make_up_class_note = ''
        if attendance.status == 'present':
            iso_day = attendance.date.isoweekday()
            dow = day_map.get(iso_day)
            if dow:
                scheduled = db.session.query(Schedule).filter(
                    Schedule.student_id == attendance.student_id,
                    Schedule.day_of_week == dow,
                    Schedule.time_slot == attendance.time_slot
                ).first()
                if not scheduled:
                    make_up_class_note = 'học bù'

        key = (attendance.date, attendance.time_slot)
        sessions_dict[key].append({
            'student': student,
            'status': attendance.status,
            'make_up_class_note': make_up_class_note,
            'recorded_by': updated_by_username or created_by_username or '',
        })

    # Build sorted session list (newest first), sort students within each session by grade then name
    def grade_sort_key(grade_str):
        if grade_str and ' ' in grade_str:
            try:
                return int(grade_str.split()[-1])
            except ValueError:
                pass
        return 99

    session_list = []
    for (sess_date, slot), students in sorted(sessions_dict.items(), key=lambda x: (x[0][0], x[0][1]), reverse=True):
        students_sorted = sorted(
            students,
            key=lambda s: (grade_sort_key(s['student'].grade), s['student'].name or '')
        )
        session_list.append({
            'date': sess_date,
            'time_slot': slot,
            'students': students_sorted,
        })

    # Build grade → Bootstrap color mapping
    all_grades_in_results = sorted(
        set(s['student'].grade for sess in session_list for s in sess['students'] if s['student'].grade),
        key=grade_sort_key
    )
    color_palette = ['primary', 'success', 'danger', 'warning', 'info', 'secondary', 'dark']
    grade_colors = {g: color_palette[i % len(color_palette)] for i, g in enumerate(all_grades_in_results)}

    total_records = len(session_list)
    total_present = sum(1 for sess in session_list for s in sess['students'] if s['status'] == 'present')
    total_absent = sum(1 for sess in session_list for s in sess['students'] if s['status'] == 'absent')
    total_makeup = sum(1 for sess in session_list for s in sess['students'] if s.get('make_up_class_note'))
    start_index = (page - 1) * per_page
    paginated_sessions = session_list[start_index:start_index + per_page]

    class Pagination:
        def __init__(self, page, per_page, total, items):
            self.page = page
            self.per_page = per_page
            self.total = total
            self.items = items
            self.pages = (total + per_page - 1) // per_page if total > 0 else 0
            self.has_prev = self.page > 1
            self.prev_num = self.page - 1
            self.has_next = self.page < self.pages
            self.next_num = self.page + 1

        def iter_pages(self, left_edge=1, left_current=1, right_current=2, right_edge=1):
            last = 0
            for num in range(1, self.pages + 1):
                if num <= left_edge or \
                   (num > self.page - left_current - 1 and num < self.page + right_current) or \
                   num > self.pages - right_edge:
                    if last + 1 != num:
                        yield None
                    yield num
                    last = num

    pagination = Pagination(page, per_page, total_records, paginated_sessions)

    return render_template('attendance_history.html',
                           sessions=paginated_sessions,
                           attendance_pagination=pagination,
                           grade_colors=grade_colors,
                           total_present=total_present,
                           total_absent=total_absent,
                           total_makeup=total_makeup,
                           start_date=start_date_str,
                           end_date=end_date_str,
                           time_slot=time_slot,
                           available_grades=grades,
                           grade_filter=grade_filter,
                           student_search_query=student_search_query,
                           student_search_results=student_search_results,
                           selected_student_id=selected_student_id,
                           selected_student_obj=selected_student_obj,
                           available_users=available_users,
                           recorded_by_filter=recorded_by_filter,
                           selected_status=selected_status)

# Import Excel route (Admin only)
@app.route('/import_excel', methods=['POST'])
@login_required
def import_excel():
    if current_user.role != 'admin':
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        abort(403)

    if 'file' not in request.files:
        flash('No file uploaded')
        return redirect_back('student_management')

    file = request.files['file']
    if file.filename == '':
        flash('No file selected')
        return redirect_back('student_management')

    if file:
        filename = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        file.save(filename)
        import_excel_data(filename, db, Student, Schedule)
        flash('Data imported successfully')
        return redirect_back('student_management')

# Export Attendance History route (Admin only)
@app.route('/export_attendance_history', methods=['GET'])
@login_required
def export_attendance_history():
    if current_user.role != 'admin':
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        abort(403)

    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    time_slot = request.args.get('time_slot')
    selected_student_id = request.args.get('selected_student_id', type=int)
    grade_filter = request.args.get('grade_filter')

    query = db.session.query(Attendance)

    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            query = query.filter(Attendance.date >= start_date)
        except ValueError:
            flash('Invalid start date format.', 'error')
            return redirect(url_for('attendance_history'))
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            query = query.filter(Attendance.date <= end_date)
        except ValueError:
            flash('Invalid end date format.', 'error')
            return redirect(url_for('attendance_history'))
    if time_slot and time_slot != 'all':
        query = query.filter(Attendance.time_slot == time_slot)

    if selected_student_id:
        query = query.filter(Attendance.student_id == selected_student_id)

    records = query.order_by(Attendance.date.desc(), Attendance.time_slot).all()

    # Filter records by grade_filter if present
    if grade_filter:
        # Fetch all unique student IDs from the current records
        current_record_student_ids = list(set([record.student_id for record in records]))
        # Fetch students that match the grade_filter
        filtered_students = db.session.query(Student).filter(Student.id.in_(current_record_student_ids), Student.grade == grade_filter).all()
        filtered_student_ids = {s.id for s in filtered_students}
        # Keep only records whose student_id is in the filtered_student_ids set
        records = [record for record in records if record.student_id in filtered_student_ids]

    student_ids = [record.student_id for record in records]
    user_ids = [record.created_by for record in records]

    unique_student_ids = list(set(student_ids))
    students_map = {s.id: s for s in db.session.query(Student).filter(Student.id.in_(unique_student_ids)).all()}

    unique_user_ids = list(set(user_ids))
    users_map = {u.id: u for u in db.session.query(User).filter(User.id.in_(unique_user_ids)).all()}

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(['Date', 'Time Slot', 'Student Name', 'Grade', 'Status'])

    for record in records:
        student = students_map.get(record.student_id)

        student_name_display = student.name if student else 'N/A'
        student_grade_display = student.grade if student else 'N/A'

        writer.writerow([
            record.date.strftime('%Y-%m-%d'),
            record.time_slot,
            student_name_display,
            student_grade_display,
            record.status
        ])

    output.seek(0)

    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name='attendance_history.csv'
    )

# Student Lookup Route (Accessible by all logged-in users)
@app.route('/export_student_csv', methods=['GET'])
@login_required
def export_student_csv():
    if current_user.role != 'admin':
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        abort(403)

    search_query = request.args.get('search_query', '').strip()
    grade_filter = request.args.get('grade_filter', '').strip()

    students_query = db.session.query(Student)

    if search_query:
        students_query = students_query.filter(
            (Student.name.ilike(f'%{search_query}%')) |
            (Student.excel_student_id.ilike(f'%{search_query}%'))
        )

    if grade_filter:
        students_query = students_query.filter(Student.grade == grade_filter)

    students = students_query.all()

    def sort_by_last_name(student):
        name_parts = student.name.split()
        return name_parts[-1] if name_parts else ""

    students.sort(key=sort_by_last_name)

    si = io.StringIO()
    cw = csv.writer(si)

    # Write header
    cw.writerow(['Mã số', 'Tên', 'Khối lớp', 'Trường học', 'Ca học đăng kí'])

        # Write data
    day_names = {
        2: 'T2',
        3: 'T3',
        4: 'T4',
        5: 'T5',
        6: 'T6',
        7: 'T7'
    }
    for student in students:
        schedules_str = "; ".join([f"{day_names.get(s.day_of_week, 'Không xác định')}: {s.time_slot}" for s in student.schedules])
        cw.writerow([
            student.id,
            student.name,
            student.grade,
            student.school,
            schedules_str
        ])

    output = make_response(si.getvalue())
    output.headers['Content-Disposition'] = 'attachment; filename=student_records.csv'
    output.headers['Content-type'] = 'text/csv'
    return output


@app.route('/student_lookup', methods=['GET'])
# @login_required # NEW: Add login_required
def student_lookup():
    search_query = request.args.get('search_query')
    selected_student_id = request.args.get('selected_student_id', type=int)

    current_month = datetime.now().month
    current_year = datetime.now().year

    month_filter = request.args.get('month_filter', type=int, default=current_month)
    year_filter = request.args.get('year_filter', type=int, default=current_year)

    student_search_results = []
    selected_student = None
    attendance_records_for_display = [] # Use a different name to avoid confusion with raw records

    if search_query:
        normalized_search_query = remove_diacritics(search_query).lower()

        # Check if search_query is purely numeric
        if search_query.isdigit():
            student_search_results = db.session.query(Student).filter(
                or_(
                    Student.excel_student_id == search_query,
                    cast(Student.id, String) == search_query
                )
            ).all()
        else:
            student_search_results = db.session.query(Student).filter(
                or_(
                    Student.name.ilike(f'%{search_query}%'),
                    Student.normalized_name.ilike(f'%{normalized_search_query}%'),
                    Student.excel_student_id.ilike(f'%{search_query}%'),
                    cast(Student.id, String).ilike(f'%{search_query}%')
                )
            ).all()

    if selected_student_id:
        selected_student = db.session.query(Student).get(selected_student_id)
        if selected_student:
            # Get first and last day of the selected month
            first_day_of_month = date(year_filter, month_filter, 1)
            # Calculate the last day of the selected month
            next_month = first_day_of_month.replace(day=28) + timedelta(days=4)
            last_day_of_month = next_month - timedelta(days=next_month.day)

            # Fetch attendance records for the selected student for the chosen month
            raw_attendance_records = db.session.query(Attendance).filter(
                Attendance.student_id == selected_student.id,
                Attendance.date >= first_day_of_month,
                Attendance.date <= last_day_of_month
            ).order_by(Attendance.date.desc(), Attendance.time_slot).all()

            # Manually fetch user details for display (who recorded it)
            user_ids = list(set([record.created_by for record in raw_attendance_records]))
            users_map = {u.id: u for u in db.session.query(User).filter(User.id.in_(user_ids)).all()}

            # Prepare records with associated user objects
            for record in raw_attendance_records:
                record_dict = record.__dict__.copy()
                record_dict['recorder_obj'] = users_map.get(record.created_by)

                # Determine if it's a make-up class
                record_dict['make_up_class_note'] = ''
                if record.status == 'present':
                    day_map = {1:2, 2:3, 3:4, 4:5, 5:6, 6:7, 7:None} # Monday is 1 in isoweekday, but 2 in Schedule.day_of_week
                    iso_day = record.date.isoweekday()
                    day_of_week_for_schedule = day_map.get(iso_day)

                    if day_of_week_for_schedule:
                        # Check if the student has a schedule for this day and time slot
                        scheduled = db.session.query(Schedule).filter(
                            Schedule.student_id == record.student_id,
                            Schedule.day_of_week == day_of_week_for_schedule,
                            Schedule.time_slot == record.time_slot
                        ).first()
                        if not scheduled:
                            record_dict['make_up_class_note'] = 'học bù'

                attendance_records_for_display.append(record_dict)

    months = [
        {'name': 'Tháng 1', 'value': 1}, {'name': 'Tháng 2', 'value': 2}, {'name': 'Tháng 3', 'value': 3},
        {'name': 'Tháng 4', 'value': 4}, {'name': 'Tháng 5', 'value': 5}, {'name': 'Tháng 6', 'value': 6},
        {'name': 'Tháng 7', 'value': 7}, {'name': 'Tháng 8', 'value': 8}, {'name': 'Tháng 9', 'value': 9},
        {'name': 'Tháng 10', 'value': 10}, {'name': 'Tháng 11', 'value': 11}, {'name': 'Tháng 12', 'value': 12}
    ]
    years = list(range(datetime.now().year - 5, datetime.now().year + 2)) # Current year +/- 5 years

    # Count records for display
    search_results_count = len(student_search_results)
    attendance_records_count = len(attendance_records_for_display)

    return render_template('student_lookup.html',
                           search_query=search_query,
                           student_search_results=student_search_results,
                           search_results_count=search_results_count,
                           selected_student=selected_student,
                           attendance_records=attendance_records_for_display,
                           attendance_records_count=attendance_records_count,
                           datetime=datetime,
                           months=months,
                           years=years,
                           current_month=month_filter,
                           current_year=year_filter)

@app.route('/user/reset_password/<int:user_id>', methods=['GET', 'POST'])
@login_required
def reset_user_password(user_id):
    if current_user.role != 'admin':
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        abort(403)
    user = db.session.query(User).get_or_404(user_id)
    if request.method == 'POST':
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']

        if new_password != confirm_password:
            flash('Mật khẩu mới và xác nhận mật khẩu không khớp.', 'danger')
            return redirect(url_for('reset_user_password', user_id=user.id))

        user.password = generate_password_hash(new_password)
        db.session.commit()
        flash(f'Mật khẩu của người dùng {user.username} đã được đặt lại thành công.', 'success')
        return redirect(url_for('users'))

    return render_template('reset_password_form.html', user=user)

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


# Admin function to manage teacher-student assignments
@app.route('/admin/manage_assignments', methods=['GET'])
@login_required
@admin_required
def manage_assignments():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int)  # Get per_page from request, default to 25

    search_query = request.args.get('search_query', '').strip()
    grade_filter = request.args.get('grade_filter', '')

    selected_teacher_id = request.args.get('teacher_id', type=int)

    # print(f"DEBUG: search_query={search_query}, grade_filter={grade_filter}, selected_teacher_id={selected_teacher_id}, per_page={per_page}")

    teachers = User.query.filter_by(role='giaovien').all()

    students_query = Student.query

    if search_query:
        students_query = students_query.filter(
            or_(
                Student.name.ilike(f'%{search_query}%'),
                Student.normalized_name.ilike(f'%{remove_diacritics(search_query)}%')
            )
        )

    if grade_filter:
        # Filter by grade, extracting the numeric part from the stored string
        expected_grade_string = f'Lớp {grade_filter}'
        # print(f"DEBUG: Filtering by grade string: {expected_grade_string}")
        students_query = students_query.filter(Student.grade == expected_grade_string)

    if selected_teacher_id:
        assigned_student_ids = [assignment.student_id for assignment in db.session.query(TeacherStudentAssignment.student_id).filter_by(teacher_id=selected_teacher_id).all()]
        students_query = students_query.filter(Student.id.in_(assigned_student_ids))

    students_query = students_query.order_by(
        db.case(
            {
                "Lớp 6": 6,
                "Lớp 7": 7,
                "Lớp 8": 8,
                "Lớp 9": 9,
                "Lớp 10": 10,
                "Lớp 11": 11,
                "Lớp 12": 12,
            },
            value=Student.grade,
            else_=99 # A high number for any grades not explicitly listed
        ),
        Student.name
    )

    students_pagination = students_query.paginate(page=page, per_page=per_page, error_out=False)
    students = students_pagination.items

    # Get all assignments
    all_assignments = TeacherStudentAssignment.query.all()

    # Create a map for all assignments (teacher_id -> list of student_ids)
    assigned_students_map = {}
    student_to_teacher_map = {}
    for assignment in all_assignments:
        if assignment.teacher_id not in assigned_students_map:
            assigned_students_map[assignment.teacher_id] = []
        assigned_students_map[assignment.teacher_id].append(assignment.student_id)
        student_to_teacher_map[assignment.student_id] = assignment.teacher_id

    # Prepare assigned student IDs for the currently selected teacher for checkbox pre-filling
    assigned_student_ids_for_selected_teacher = []
    if selected_teacher_id and selected_teacher_id in assigned_students_map:
        assigned_student_ids_for_selected_teacher = assigned_students_map[selected_teacher_id]

    # Get all unique grades for the filter dropdown and sort them numerically


    # Get all unique grades for the filter dropdown and sort them numerically
    raw_grades = db.session.query(Student.grade).distinct().all()
    processed_grades = []
    for grade_tuple in raw_grades:
        grade_string = grade_tuple[0]
        match = re.search(r'\d+', grade_string)
        if match:
            processed_grades.append(int(match.group()))

    all_grades = sorted(list(set(processed_grades)))

    return render_template('manage_assignments.html',
                           teachers=teachers,
                           students=students,
                           assigned_students_map=assigned_students_map,
                           student_to_teacher_map=student_to_teacher_map,
                           pagination=students_pagination,
                           search_query=search_query,
                           grade_filter=grade_filter,
                           all_grades=all_grades,
                           selected_teacher_id=selected_teacher_id,
                           assigned_student_ids_for_selected_teacher=assigned_student_ids_for_selected_teacher,
                           per_page=per_page,
                           show_existing_assignments=False)

@app.route('/admin/assign_students', methods=['POST'])
@login_required
@admin_required
def assign_students():
    updated_count = 0
    for key, value in request.form.items():
        if key.startswith('assigned_teacher_'):
            student_id_to_update = int(key.replace('assigned_teacher_', ''))
            new_teacher_id_for_student = int(value) if value else None

            existing_assignment = TeacherStudentAssignment.query.filter_by(student_id=student_id_to_update).first()

            if new_teacher_id_for_student:
                if existing_assignment:
                    if existing_assignment.teacher_id != new_teacher_id_for_student:
                        existing_assignment.teacher_id = new_teacher_id_for_student
                        db.session.commit()
                        updated_count += 1
                else:
                    new_assignment = TeacherStudentAssignment(student_id=student_id_to_update, teacher_id=new_teacher_id_for_student)
                    db.session.add(new_assignment)
                    db.session.commit()
                    updated_count += 1
            else:
                if existing_assignment:
                    db.session.delete(existing_assignment)
                    db.session.commit()
                    updated_count += 1

    if updated_count > 0:
        flash(f'Đã cập nhật thành công {updated_count} phân công.', 'success')
    else:
        flash('Không có thay đổi nào được lưu.', 'info')

    # Redirect back to the manage_assignments page, preserving filters
    search_query = request.form.get('search_query', '')
    grade_filter = request.form.get('grade_filter', '')
    teacher_id = request.form.get('teacher_id', '')
    page = request.form.get('page', 1, type=int)
    return redirect(url_for(
        'manage_assignments',
        search_query=search_query,
        grade_filter=grade_filter,
        teacher_id=teacher_id,
        page=page
    ))

@app.route('/comment_management')
@login_required
def comment_management():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)  # Số lượng học sinh trên mỗi trang
    sort_by = request.args.get('sort_by', 'grade_name') # Default sort by grade then name
    sort_order = request.args.get('sort_order', 'asc') # Default sort order ascending

    search_query = request.args.get('search_query', '').strip()
    grade_filter = request.args.get('grade_filter', '').strip()

    all_grades = [grade[0] for grade in db.session.query(Student.grade).distinct().order_by(Student.grade).all()]

    students_query = Student.query

    if not current_user.is_admin:
        assigned_student_ids = [assignment.student_id for assignment in TeacherStudentAssignment.query.filter_by(teacher_id=current_user.id).all()]
        students_query = students_query.filter(Student.id.in_(assigned_student_ids))

    if search_query:
        students_query = students_query.filter(or_(
            Student.name.ilike(f'%{search_query}%'),
            Student.excel_student_id.ilike(f'%{search_query}%')
        ))

    if grade_filter:
        students_query = students_query.filter_by(grade=grade_filter)

    # Apply sorting logic
    if sort_by == 'grade_name':
        # Sort by grade (numeric part) then by the last word of the name
        grade_sort_key = func.cast(func.substr(Student.grade, 5), Integer) # Assuming "Lớp X" format
        name_last_word_sort_key = func.substr(Student.name, func.instr(Student.name, ' ') + 1)

        if sort_order == 'asc':
            students_query = students_query.order_by(grade_sort_key.asc(), name_last_word_sort_key.asc())
        else:
            students_query = students_query.order_by(grade_sort_key.desc(), name_last_word_sort_key.desc())
    elif sort_by == 'name':
        if sort_order == 'asc':
            students_query = students_query.order_by(Student.name.asc())
        else:
            students_query = students_query.order_by(Student.name.desc())
    elif sort_by == 'grade':
        grade_sort_key = func.cast(func.substr(Student.grade, 5), Integer)
        if sort_order == 'asc':
            students_query = students_query.order_by(grade_sort_key.asc())
        else:
            students_query = students_query.order_by(grade_sort_key.desc())

    students_pagination = students_query.paginate(page=page, per_page=per_page, error_out=False)
    students = students_pagination.items

    # Thêm thông tin nhận xét gần nhất từ bảng Student cho mỗi học sinh
    students_with_latest_comment = []
    for student in students:
        latest_comment = {
            'content': student.comment if student.comment else None,
            'updated_at': student.comment_last_updated_at if student.comment_last_updated_at else None
        }

        students_with_latest_comment.append({
            'student': student,
            'latest_comment': latest_comment
        })

    return render_template('comment_management.html',
                           students=students,
                           students_with_latest_comment=students_with_latest_comment,
                           students_pagination=students_pagination,
                           search_query=search_query,
                           grade_filter=grade_filter,
                           available_grades=all_grades,
                           sort_by=sort_by,
                           sort_order=sort_order)

@app.route('/update_comment', methods=['POST'])
@login_required
def update_comment():
    data = request.get_json()
    student_id = data.get('student_id')
    comment = data.get('comment')

    if not student_id:
        return jsonify({'success': False, 'message': 'Thiếu student_id.'}), 400

    student = Student.query.get(student_id)
    if not student:
        return jsonify({'success': False, 'message': 'Không tìm thấy học sinh.'}), 404

    # Authorization check: Teachers can only update comments for their assigned students
    if not current_user.is_admin:
        assignment = TeacherStudentAssignment.query.filter_by(teacher_id=current_user.id, student_id=student_id).first()
        if not assignment:
            return jsonify({'success': False, 'message': 'Bạn không có quyền chỉnh sửa nhận xét của học sinh này.'}), 403

    try:
        student.comment = comment
        student.comment_last_updated_by = current_user.username
        student.comment_last_updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'success': True, 'message': 'Cập nhật nhận xét thành công.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
@app.route('/update_attendance_comment', methods=['POST'])
@login_required
def update_attendance_comment():
    attendance_id = request.form.get('attendance_id', type=int)
    student_id = request.form.get('student_id', type=int)
    comment = request.form.get('comment', '').strip()

    if not attendance_id or not student_id:
        flash('Thiếu thông tin bắt buộc.', 'danger')
        return redirect(url_for('comment_management'))

    # Authorization check: Teachers can only update comments for their assigned students
    if not current_user.is_admin:
        assignment = TeacherStudentAssignment.query.filter_by(teacher_id=current_user.id, student_id=student_id).first()
        if not assignment:
            flash('Bạn không có quyền chỉnh sửa nhận xét của học sinh này.', 'danger')
            return redirect(url_for('comment_management'))

    try:
        attendance = Attendance.query.get(attendance_id)
        if not attendance:
            flash('Không tìm thấy bản ghi điểm danh.', 'danger')
            return redirect(url_for('student_comments', student_id=student_id))

        # Update the comment
        attendance.comment = comment
        attendance.update_by = current_user.id
        attendance.updated_at = datetime.utcnow()
        db.session.commit()

        flash('Cập nhật nhận xét thành công.', 'success')
        return redirect(url_for('student_comments', student_id=student_id))
    except Exception as e:
        db.session.rollback()
        flash(f'Đã xảy ra lỗi: {str(e)}', 'danger')
        return redirect(url_for('student_comments', student_id=student_id))

@app.route('/student_comments/<int:student_id>')
@login_required
def student_comments(student_id):
    student = Student.query.get_or_404(student_id)

    # Kiểm tra quyền truy cập
    if not current_user.is_admin:
        assignment = TeacherStudentAssignment.query.filter_by(teacher_id=current_user.id, student_id=student_id).first()
        if not assignment:
            flash('Bạn không có quyền xem nhận xét của học sinh này.', 'danger')
            return redirect(url_for('comment_management'))

    # Lấy tất cả nhận xét từ bảng Attendance, sắp xếp theo ngày giảm dần
    all_attendances = db.session.query(Attendance).filter_by(student_id=student_id).order_by(Attendance.date.desc(), Attendance.time_slot.desc()).all()

    return render_template('student_comments.html', student=student, all_attendances=all_attendances)

@app.route('/tests')
@login_required
def test_management():
    if current_user.role not in ['admin', 'giaovien']:
        flash('Ban khong co quyen truy cap trang nay.', 'danger')
        abort(403)

    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    search_query = request.args.get('search_query', '').strip()
    grade_filter = request.args.get('grade_filter', '').strip()

    available_grades = [grade[0] for grade in db.session.query(Student.grade).distinct().order_by(Student.grade).all()]

    exams_query = TestExam.query
    if search_query:
        exams_query = exams_query.filter(TestExam.title.ilike(f'%{search_query}%'))
    if grade_filter:
        exams_query = exams_query.filter(TestExam.grade == grade_filter)

    exams_pagination = exams_query.order_by(TestExam.created_at.desc(), TestExam.id.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False
    )
    exams = exams_pagination.items

    exam_ids = [exam.id for exam in exams]
    student_counts = {}
    if exam_ids:
        student_count_rows = db.session.query(
            TestScore.test_exam_id,
            func.count(TestScore.id)
        ).filter(
            TestScore.test_exam_id.in_(exam_ids),
            TestScore.attempt_number == 1
        ).group_by(TestScore.test_exam_id).all()
        student_counts = {exam_id: count for exam_id, count in student_count_rows}

    return render_template(
        'test_management.html',
        exams=exams,
        exams_pagination=exams_pagination,
        student_counts=student_counts,
        search_query=search_query,
        grade_filter=grade_filter,
        available_grades=available_grades
    )


@app.route('/tests/create', methods=['GET', 'POST'])
@login_required
def create_test_exam():
    if not current_user.is_admin:
        flash('Ban khong co quyen truy cap trang nay.', 'danger')
        abort(403)

    available_grades = [grade[0] for grade in db.session.query(Student.grade).distinct().order_by(Student.grade).all()]

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        grade = request.form.get('grade', '').strip()
        description = request.form.get('description', '').strip()

        if not title:
            flash('Ten bai kiem tra khong duoc de trong.', 'danger')
            return render_template('test_form.html', available_grades=available_grades)

        if not grade:
            flash('Ban can chon khoi lop.', 'danger')
            return render_template('test_form.html', available_grades=available_grades)

        students_in_grade = sort_students_for_display(Student.query.filter_by(grade=grade).all())
        if not students_in_grade:
            flash('Khoi nay hien chua co hoc sinh de tao bai kiem tra.', 'danger')
            return render_template('test_form.html', available_grades=available_grades)

        exam = TestExam(
            title=title,
            grade=grade,
            description=description or None,
            created_by=current_user.id
        )
        db.session.add(exam)
        db.session.flush()

        add_attempts_to_exam(
            exam_id=exam.id,
            student_ids=[student.id for student in students_in_grade],
            start_attempt=1,
            count=DEFAULT_TEST_ATTEMPT_COUNT
        )

        db.session.commit()
        flash(f'Da tao bai kiem tra "{title}" cho {grade}.', 'success')
        return redirect(url_for('manage_test_exam', exam_id=exam.id))

    return render_template('test_form.html', available_grades=available_grades)


@app.route('/tests/<int:exam_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_test_exam(exam_id):
    exam = TestExam.query.get_or_404(exam_id)
    exam_title = exam.title
    db.session.delete(exam)
    db.session.commit()
    flash(f'Da xoa bai kiem tra "{exam_title}".', 'success')
    return redirect(url_for('test_management'))


@app.route('/tests/<int:exam_id>/attempts/add', methods=['POST'])
@login_required
def add_test_attempt(exam_id):
    if not current_user.is_admin:
        flash('Ban khong co quyen truy cap trang nay.', 'danger')
        abort(403)

    exam = TestExam.query.get_or_404(exam_id)
    student_ids = [
        student_id for (student_id,) in db.session.query(TestScore.student_id).filter_by(
            test_exam_id=exam.id
        ).distinct().all()
    ]

    if not student_ids:
        flash('Bai kiem tra nay chua co hoc sinh de them lan thi.', 'danger')
        return redirect(url_for('manage_test_exam', exam_id=exam.id))

    attempt_numbers = get_exam_attempt_numbers(exam.id)
    start_attempt = (attempt_numbers[-1] + 1) if attempt_numbers else 1

    add_attempts_to_exam(
        exam_id=exam.id,
        student_ids=student_ids,
        start_attempt=start_attempt,
        count=1
    )
    db.session.commit()

    flash('Da them 1 lan kiem tra moi.', 'success')
    return redirect(url_for('manage_test_exam', exam_id=exam.id, attempt=start_attempt))


@app.route('/tests/<int:exam_id>/attempts/<int:attempt>/delete', methods=['POST'])
@login_required
@admin_required
def delete_test_attempt(exam_id, attempt):
    exam = TestExam.query.get_or_404(exam_id)
    attempt_numbers = get_exam_attempt_numbers(exam.id)

    if attempt not in attempt_numbers:
        flash('Khong tim thay lan kiem tra can xoa.', 'danger')
        return redirect(url_for('manage_test_exam', exam_id=exam.id))

    if len(attempt_numbers) <= 1:
        flash('Bai kiem tra phai con it nhat 1 lan.', 'danger')
        return redirect(url_for('manage_test_exam', exam_id=exam.id, attempt=attempt))

    TestScore.query.filter_by(test_exam_id=exam.id, attempt_number=attempt).delete(synchronize_session=False)
    db.session.flush()

    rows_to_shift = TestScore.query.filter(
        TestScore.test_exam_id == exam.id,
        TestScore.attempt_number > attempt
    ).all()

    for row in rows_to_shift:
        row.attempt_number += 1000
    db.session.flush()

    for row in rows_to_shift:
        row.attempt_number -= 1001

    db.session.commit()

    next_attempt_numbers = get_exam_attempt_numbers(exam.id)
    redirect_attempt = attempt if attempt in next_attempt_numbers else next_attempt_numbers[-1]
    flash(f'Da xoa lan {attempt} va don cac lan phia sau.', 'success')
    return redirect(url_for('manage_test_exam', exam_id=exam.id, attempt=redirect_attempt))


@app.route('/tests/<int:exam_id>/export_csv_internal')
@login_required
def export_test_exam_csv_internal(exam_id):
    if current_user.role not in ['admin', 'giaovien']:
        flash('Ban khong co quyen truy cap trang nay.', 'danger')
        abort(403)

    exam = TestExam.query.get_or_404(exam_id)
    attempt_numbers = get_exam_attempt_numbers(exam.id)
    if not attempt_numbers:
        flash('Bai kiem tra nay chua co du lieu de xuat.', 'warning')
        return redirect(url_for('manage_test_exam', exam_id=exam.id))

    attempt = request.args.get('attempt', attempt_numbers[0], type=int)
    if attempt not in attempt_numbers:
        attempt = attempt_numbers[0]

    results = TestScore.query.filter_by(test_exam_id=exam.id, attempt_number=attempt).all()
    student_map = {
        student.id: student
        for student in Student.query.filter(Student.id.in_([result.student_id for result in results])).all()
    }

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Ten hoc sinh', 'Diem', 'Nhan xet'])

    for result in results:
        student = student_map.get(result.student_id)
        if not student:
            continue
        writer.writerow([
            student.name,
            '' if result.score is None else result.score,
            result.comment or ''
        ])

    filename = f'test_exam_{exam.id}_attempt_{attempt}.csv'
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8-sig')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=filename
    )


@app.route('/tests/<int:exam_id>', methods=['GET', 'POST'])
@login_required
def manage_test_exam(exam_id):
    if current_user.role not in ['admin', 'giaovien']:
        flash('Ban khong co quyen truy cap trang nay.', 'danger')
        abort(403)

    exam = TestExam.query.get_or_404(exam_id)
    attempt_numbers = get_exam_attempt_numbers(exam.id)
    if not attempt_numbers:
        flash('Bai kiem tra nay chua co lan kiem tra nao.', 'warning')
        return redirect(url_for('test_management'))

    attempt = request.values.get('attempt', attempt_numbers[0], type=int)
    if attempt not in attempt_numbers:
        attempt = attempt_numbers[0]

    sort_by = request.values.get('sort_by', 'name')
    sort_order = request.values.get('sort_order', 'asc')

    results = TestScore.query.filter_by(test_exam_id=exam.id, attempt_number=attempt).all()

    if request.method == 'POST':
        try:
            has_changes = False
            for result in results:
                score = parse_optional_score(request.form.get(f'score_{result.id}', ''))
                comment = (request.form.get(f'comment_{result.id}') or '').strip()
                normalized_comment = comment or None

                if result.score != score or result.comment != normalized_comment:
                    result.score = score
                    result.comment = normalized_comment
                    result.graded_by = current_user.id
                    result.graded_at = datetime.utcnow()
                    has_changes = True

            if has_changes:
                db.session.commit()
                flash(f'Da luu {format_test_attempt_label(attempt)} cho bai kiem tra.', 'success')
            else:
                flash('Khong co thay doi nao de luu.', 'info')
        except ValueError:
            db.session.rollback()
            flash('Diem khong hop le. Chi nhap so tu 0 den 10 va dung dau "." nhu 6.5.', 'danger')

        return redirect(url_for(
            'manage_test_exam',
            exam_id=exam.id,
            attempt=attempt,
            sort_by=sort_by,
            sort_order=sort_order
        ))

    student_map = {
        student.id: student
        for student in Student.query.filter(Student.id.in_([result.student_id for result in results])).all()
    }

    rows = []
    for result in results:
        student = student_map.get(result.student_id)
        if not student:
            continue

        rows.append({
            'student': student,
            'result': result
        })

    if sort_by == 'score':
        if sort_order == 'asc':
            rows.sort(key=lambda row: (
                row['result'].score is None,
                row['result'].score if row['result'].score is not None else 0,
                row['student'].name or ''
            ))
        else:
            rows.sort(key=lambda row: (
                row['result'].score is None,
                -(row['result'].score if row['result'].score is not None else -1),
                row['student'].name or ''
            ))
    else:
        reverse_name = sort_order == 'desc'
        rows.sort(
            key=lambda row: (
                row['student'].name.split()[-1] if row['student'].name and row['student'].name.split() else '',
                row['student'].name or ''
            ),
            reverse=reverse_name
        )

    return render_template(
        'test_detail.html',
        exam=exam,
        rows=rows,
        attempt=attempt,
        attempt_labels=build_test_attempt_labels(attempt_numbers),
        attempt_numbers=attempt_numbers,
        sort_by=sort_by,
        sort_order=sort_order
    )


@app.route('/test_scores/export_csv')
def export_public_test_scores_csv():
    flash('Public test score view is disabled.', 'info')
    return redirect(url_for('test_management'))


@app.route('/test_scores')
def public_test_scores():
    flash('Public test score view is disabled.', 'info')
    return redirect(url_for('test_management'))

# ============== BACKUP MANAGEMENT ROUTES ==============

@app.route('/admin/backup_management', methods=['GET'])
@login_required
@admin_required
def backup_management():
    """Hiển thị danh sách các file backup"""
    backups = get_backup_files()
    return render_template('backup_management.html', backups=backups)

@app.route('/admin/trigger_backup', methods=['POST'])
@login_required
@admin_required
def trigger_backup():
    """Trigger backup thủ công"""
    try:
        backup_databases()
        flash('✅ Backup thủ công đã được tạo thành công.', 'success')
    except Exception as e:
        flash(f'❌ Lỗi khi backup: {str(e)}', 'danger')
    return redirect(url_for('backup_management'))

@app.route('/admin/restore_backup', methods=['POST'])
@login_required
@admin_required
def restore_backup_route():
    """Restore từ backup file"""
    backup_filename = request.form.get('backup_filename')
    
    if not backup_filename:
        flash('❌ Chọn file backup để restore.', 'danger')
        return redirect(url_for('backup_management'))
    
    try:
        restore_backup(backup_filename)
        flash(f'✅ Restore từ {backup_filename} thành công. Vui lòng reload lại trang.', 'success')
    except Exception as e:
        flash(f'❌ Lỗi khi restore: {str(e)}', 'danger')
    
    return redirect(url_for('backup_management'))

@app.route('/admin/download_backup/<filename>', methods=['GET'])
@login_required
@admin_required
def download_backup(filename):
    """Download backup file"""
    try:
        backup_dir = os.path.join(os.path.dirname(app.instance_path), 'backups')
        return send_file(
            os.path.join(backup_dir, filename),
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        flash(f'❌ Lỗi khi download: {str(e)}', 'danger')
        return redirect(url_for('backup_management'))

# ============== END BACKUP MANAGEMENT ROUTES ==============

if __name__ == '__main__':
    with app.app_context():
        # Create tables for all binds
        db.create_all()

        # Add default users if they don't exist
        if not db.session.query(User).filter_by(username='admin').first():
            admin = User(
                username='admin',
                password=generate_password_hash('Cophuong@2025'),
                role='admin',
                is_admin=True # Explicitly set is_admin to True for admin user
            )
            db.session.add(admin)
            db.session.commit()
            print("Default admin user created: admin/Cophuong@2025")

        if not db.session.query(User).filter_by(username='giaovien1').first():
            giaovien1 = User(
                username='giaovien1',
                password=generate_password_hash('Giaovien@1'),
                role='giaovien'
            )
            db.session.add(giaovien1)
            db.session.commit()
            print("Default giaovien user created: giaovien1/Giaovien@1")

        if not db.session.query(User).filter_by(username='giaovien2').first():
            giaovien2 = User(
                username='giaovien2',
                password=generate_password_hash('Giaovien@2'),
                role='giaovien'
            )
            db.session.add(giaovien2)
            db.session.commit()
            print("Default giaovien user created: giaovien2/Giaovien@2")

        if not db.session.query(User).filter_by(username='giaovien3').first():
            giaovien3 = User(
                username='giaovien3',
                password=generate_password_hash('Giaovien@3'),
                role='giaovien'
            )
            db.session.add(giaovien3)
            db.session.commit()
            print("Default giaovien user created: giaovien3/Giaovien@3")

        if not db.session.query(User).filter_by(username='giaovien4').first():
            giaovien4 = User(
                username='giaovien4',
                password=generate_password_hash('Giaovien@4'),
                role='giaovien'
            )
            db.session.add(giaovien4)
            db.session.commit()
            print("Default giaovien user created: giaovien4/Giaovien@4")

        if not db.session.query(User).filter_by(username='giaovien5').first():
            giaovien5 = User(
                username='giaovien5',
                password=generate_password_hash('Giaovien@5'),
                role='giaovien'
            )
            db.session.add(giaovien5)
            db.session.commit()
            print("Default giaovien user created: giaovien5/Giaovien@5")

    app.run(debug=True, host='0.0.0.0')
