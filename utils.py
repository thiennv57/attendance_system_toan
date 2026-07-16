import pandas as pd
import unicodedata
import re
from datetime import datetime
from sqlalchemy import and_

def remove_diacritics(input_str):
    if not isinstance(input_str, str):
        return input_str
    nfkd_form = unicodedata.normalize('NFKD', input_str)
    # Remove combining characters (diacritics)
    no_diacritics = "".join([c for c in nfkd_form if not unicodedata.combining(c)])
    # Replace spaces and non-alphanumeric characters with nothing, and convert to lowercase
    return re.sub(r'[^\w\s]', '', no_diacritics).lower()

# The db parameter passed here will be the single db instance from app.py
def import_excel_data(file_path, db_instance, Student_model, Schedule_model):
    df = pd.read_excel(file_path)
    
    def get_safe_string(value, default_if_na=""): # Modified to return empty string by default
        if pd.isna(value):
            return default_if_na
        return str(value).strip()

    print("Starting Excel data import...")
    imported_students_count = 0
    total_students_found = 0

    for index, row in df.iterrows():
        # Extract student data
        raw_excel_student_id = row.get('Mã học sinh (nếu có)')
        raw_student_name = row.get('Họ và tên con')
        raw_student_grade = row.get('Khối lớp con đang học')
        raw_student_school = row.get('Trường con đang học')
        raw_id_check = row.get('IdCheck')

        excel_student_id = get_safe_string(raw_excel_student_id)
        student_name = get_safe_string(raw_student_name)
        student_grade = get_safe_string(raw_student_grade)
        student_school = get_safe_string(raw_student_school)
        id_check = get_safe_string(raw_id_check)

        # Check for duplicate student name before proceeding
        # existing_student = db_instance.session.query(Student_model).filter_by(normalized_name=remove_diacritics(student_name)).first()
        # if existing_student:
        #     print(f"Skipping student {student_name} (Row {index + 2}) due to duplicate name.")
        #     continue

        # Only consider this row a "student" if it has at least a name or an excel_student_id
        if not student_name and not excel_student_id and not id_check:
            print(f"Skipping row {index + 2} due to missing student name, Excel ID, and IdCheck.")
            continue
        
        # Check for duplicate IdCheck
        if id_check:
            existing_student_by_id_check = db_instance.session.query(Student_model).filter_by(id_check=id_check).first()
            if existing_student_by_id_check:
                print(f"Skipping student {student_name} (Row {index + 2}) due to duplicate IdCheck: {id_check}.")
                continue

        total_students_found += 1 # Increment only for rows that are potential students

        print(f"  Raw Excel ID: {raw_excel_student_id}, Raw Name: {raw_student_name}, Raw Grade: {raw_student_grade}, Raw School: {raw_student_school}, Raw IdCheck: {raw_id_check}")
        print(f"  Processed Excel ID: {excel_student_id}, Processed Name: {student_name}, Processed Grade: {student_grade}, Processed School: {student_school}, Processed IdCheck: {id_check}")

        # Debugging phone numbers
        debug_father_phone = row.get('SĐT/Zalo của bố')
        debug_mother_phone = row.get('SĐT/Zalo của mẹ')
        print(f"  Debug Father Phone: {debug_father_phone}, Debug Mother Phone: {debug_mother_phone}")

        # Collect schedules first
        collected_schedules = []
        days = ['Thứ 2', 'Thứ 3', 'Thứ 4', 'Thứ 5', 'Thứ 6', 'Thứ 7']
        for day_index, day in enumerate(days, 2):
            column_name = f'Ca học theo đăng kí của con [{day}]'
            if column_name in row:
                time_slot_value = get_safe_string(row[column_name])
                if time_slot_value:
                    collected_schedules.append({'day_of_week': day_index, 'time_slot': time_slot_value})

        if not collected_schedules:
            print(f"Skipping student {student_name} (Row {index + 2}) due to no valid schedules.")
            continue # Skip to the next row if no schedules are found

        # Always create new student
        print(f"Creating new student: {student_name}")
        student = Student_model(
                excel_student_id=None,
                name=student_name,
                grade=student_grade,
                school=student_school,
                father_name=get_safe_string(row.get('Họ và tên bố')),
                father_phone=get_safe_string(row.get('SĐT/Zalo của bố')),
                mother_phone=get_safe_string(row.get('SĐT/Zalo của mẹ')),
                father_occupation=get_safe_string(row.get('Nghề nghiệp của bố')),
                mother_name=get_safe_string(row.get('Họ và tên mẹ')),
                mother_occupation=get_safe_string(row.get('Nghề nghiệp của mẹ')),
                address=get_safe_string(row.get('Địa chỉ nhà')),
                notes=get_safe_string(row.get('Đôi lời nhắn nhủ hoặc nhận xét về con')),
                comment=get_safe_string(row.get('Đôi lời nhắn nhủ hoặc nhận xét về con')),
                normalized_name=remove_diacritics(student_name),
                id_check=id_check
            )
        db_instance.session.add(student)
        db_instance.session.flush()  # To get the student.id for new students

        for schedule_data in collected_schedules:
            print(f"Adding schedule for day {schedule_data['day_of_week']}: {schedule_data['time_slot']}")
            schedule = Schedule_model(
                student_id=student.id,
                day_of_week=schedule_data['day_of_week'],
                time_slot=schedule_data['time_slot']
            )
            db_instance.session.add(schedule)
        imported_students_count += 1

    print("Committing all changes to database")
    db_instance.session.commit()
    print(f"Successfully imported {imported_students_count} students out of {total_students_found} found in the Excel file.")

def get_students_for_slot(db_instance, Student_model, Schedule_model, day_of_week, time_slot):
    return db_instance.session.query(Student_model).join(Schedule_model).filter(
        and_(
            Schedule_model.day_of_week == day_of_week,
            Schedule_model.time_slot == time_slot
        )
    ).order_by(Student_model.grade, Student_model.name).all()

def update_all_student_normalized_names():
    from models import db, Student
    from utils import remove_diacritics
    students = db.session.query(Student).all()
    for student in students:
        if student.name and not student.normalized_name:
            student.normalized_name = remove_diacritics(student.name)
    db.session.commit()
    print("All student normalized names updated successfully.")