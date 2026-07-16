import sqlite3
from datetime import datetime

# Kết nối đến cơ sở dữ liệu students.db
conn = sqlite3.connect('instance/students.db')
cursor = conn.cursor()

try:
    # 1. Thêm cột last_update_at mà không có giá trị mặc định (hoặc với giá trị mặc định là NULL)
    #cursor.execute("ALTER TABLE student ADD COLUMN comment_last_updated_by VARCHAR(100);")
    print("Đã thêm cột 'comment_last_updated_by' vào bảng 'student' thành công (ban đầu là NULL).")
    cursor.execute("ALTER TABLE student ADD COLUMN comment_last_updated_at DATETIME;")
    print("Đã thêm cột 'comment_last_updated_at' vào bảng 'student' thành công (ban đầu là NULL).")

    # 2. Cập nhật giá trị cho các hàng hiện có
    # Sử dụng strftime để định dạng datetime thành chuỗi tương thích với SQLite

    conn.commit()
    print("Các thay đổi đã được lưu vào cơ sở dữ liệu.")

except sqlite3.OperationalError as e:
    print(f"Lỗi khi thực hiện các lệnh SQL: {e}")
finally:
    conn.close()