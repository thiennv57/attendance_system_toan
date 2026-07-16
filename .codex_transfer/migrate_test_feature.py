from app import app, db
from models import TestExam, TestScore


def main():
    with app.app_context():
        TestExam.__table__.create(bind=db.engines['attendance'], checkfirst=True)
        TestScore.__table__.create(bind=db.engines['attendance'], checkfirst=True)
        print('Test feature tables are ready.')


if __name__ == '__main__':
    main()
