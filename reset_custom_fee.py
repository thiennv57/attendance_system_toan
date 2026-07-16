#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Script để reset custom_fee từ tháng 05/2026 trở đi về NULL
"""

import sqlite3
from datetime import datetime

# Kết nối tới database
conn = sqlite3.connect(r'instance\students.db')
cursor = conn.cursor()

print("🔄 Bắt đầu reset custom_fee từ 05/2026 trở đi...")
print(f"⏰ Thời gian: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")

# Tìm tất cả bản ghi từ tháng 05/2026 trở đi
cursor.execute('''
    SELECT id, student_id, month, year, amount_due, custom_fee FROM tuition 
    WHERE (year > 2026) OR (year = 2026 AND month >= 5)
    ORDER BY year, month, student_id
''')

records = cursor.fetchall()
print(f"📋 Tìm thấy {len(records)} bản ghi từ 05/2026 trở đi\n")

# Hiển thị 10 bản ghi đầu tiên
print("📝 Chi tiết 10 bản ghi đầu tiên:")
for i, record in enumerate(records[:10]):
    print(f"  ID {record[0]}: Student {record[1]}, Tháng {record[2]}/{record[3]}, Amount Due: {record[4]}, Custom Fee: {record[5]}")

if len(records) > 10:
    print(f"  ... và {len(records) - 10} bản ghi khác")

print("\n" + "="*60)
print("⚠️  CẢNH BÁO: Bạn sắp xóa tất cả custom_fee từ 05/2026 trở đi!")
print("="*60)

# Xin xác nhận
confirm = input("\nBạn có chắc chắn muốn tiếp tục? (gõ 'yes' để xác nhận): ").strip().lower()

if confirm != 'yes':
    print("❌ Đã hủy thao tác.")
    conn.close()
    exit()

# Reset custom_fee về NULL cho tất cả bản ghi từ 05/2026 trở đi
cursor.execute('''
    UPDATE tuition 
    SET custom_fee = NULL 
    WHERE (year > 2026) OR (year = 2026 AND month >= 5)
''')

conn.commit()

print(f"\n✅ Đã reset {cursor.rowcount} bản ghi!")
print(f"📊 Tất cả custom_fee từ tháng 05/2026 trở đi đã được xóa\n")

# Xác minh dữ liệu sau khi reset
cursor.execute('''
    SELECT COUNT(*) FROM tuition 
    WHERE ((year > 2026) OR (year = 2026 AND month >= 5)) AND custom_fee IS NOT NULL
''')

remaining = cursor.fetchone()[0]
print(f"✅ Xác minh: {remaining} bản ghi vẫn còn custom_fee (nếu = 0 thì đã hoàn tất)")
print("✅ Hoàn tất!")

conn.close()
