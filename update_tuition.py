"""
Script cập nhật toàn bộ học phí từ tháng 05/2026 trở đi về mặc định là 0
"""

import os
import sys
from datetime import datetime

# Thêm workspace vào path
sys.path.insert(0, os.path.dirname(__file__))

from app import app, db
from models import Tuition

def update_tuition_to_zero():
    """Cập nhật toàn bộ học phí từ 05/2026 về sau = 0"""
    
    with app.app_context():
        try:
            # Lấy tất cả tuition records từ tháng 05/2026 trở đi
            tuitions = db.session.query(Tuition).filter(
                (Tuition.year > 2026) | 
                ((Tuition.year == 2026) & (Tuition.month >= 5))
            ).all()
            
            if not tuitions:
                print("❌ Không tìm thấy bản ghi học phí nào từ tháng 05/2026 trở đi")
                return False
            
            print(f"📋 Tìm thấy {len(tuitions)} bản ghi học phí từ 05/2026 trở đi")
            print("\n", "="*60)
            
            # Hiển thị danh sách records sẽ được cập nhật
            for i, tuition in enumerate(tuitions[:10], 1):  # Hiển thị 10 cái đầu
                print(f"{i}. Student ID: {tuition.student_id}, Tháng: {tuition.month}/{tuition.year}, Amount Due: {tuition.amount_due} → 0")
            
            if len(tuitions) > 10:
                print(f"... và {len(tuitions) - 10} bản ghi khác")
            
            print("="*60)
            
            # Xác nhận trước khi cập nhật
            confirm = input(f"\n⚠️  Bạn có chắc chắn muốn cập nhật {len(tuitions)} bản ghi học phí này về 0? (yes/no): ").strip().lower()
            
            if confirm != 'yes':
                print("❌ Hủy bỏ cập nhật")
                return False
            
            # Cập nhật tất cả records
            for tuition in tuitions:
                tuition.amount_due = 0
            
            db.session.commit()
            
            print(f"\n✅ Đã cập nhật thành công {len(tuitions)} bản ghi học phí!")
            print(f"📊 Toàn bộ học phí từ tháng 05/2026 trở đi đã được đặt về 0")
            
            return True
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Lỗi khi cập nhật: {str(e)}")
            return False

if __name__ == '__main__':
    print("🔄 Bắt đầu cập nhật học phí...")
    print(f"⏰ Thời gian: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")
    
    success = update_tuition_to_zero()
    
    if success:
        print("\n✅ Hoàn tất!")
    else:
        print("\n❌ Cập nhật thất bại!")
        sys.exit(1)
