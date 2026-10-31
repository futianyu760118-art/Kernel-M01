"""User ↔ Employee 绑定逻辑测试。

覆盖：邮箱匹配绑定、已绑定跳过、无匹配、大小写不敏感、幂等。
"""

from datetime import date

import pytest
from flask import Flask
from flask_jwt_extended import JWTManager

from models import Employee, User, db
from services.user_employee_binding import bind_users_by_email


@pytest.fixture()
def app():
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY='test-secret',
        JWT_SECRET_KEY='test-jwt-secret',
        SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    JWTManager(app)
    with app.app_context():
        db.create_all()
        yield app


def _seed(app, users, employees):
    with app.app_context():
        for emp in employees:
            db.session.add(Employee(id=emp['id'], employee_no=emp['id'], name=emp['name'],
                                    email=emp.get('email'), status='active',
                                    hire_date=date(2025, 1, 1)))
        for u in users:
            db.session.add(User(id=u['id'], email=u['email'], password_hash='x',
                                name=u['name'], role='employee', status='active',
                                employee_id=u.get('employee_id')))
        db.session.commit()


def test_bind_matches_by_email(app):
    _seed(app,
          users=[{'id': 'u1', 'email': 'zhang@corp.com', 'name': 'Zhang'}],
          employees=[{'id': 'e1', 'email': 'zhang@corp.com', 'name': 'Zhang San'}])
    with app.app_context():
        stats = bind_users_by_email(dry_run=False)
        assert stats['matched'] == 1
        assert User.query.get('u1').employee_id == 'e1'


def test_bind_is_case_insensitive_and_strips(app):
    _seed(app,
          users=[{'id': 'u1', 'email': '  Li@Corp.COM  ', 'name': 'Li'}],
          employees=[{'id': 'e1', 'email': 'li@corp.com', 'name': 'Li Si'}])
    with app.app_context():
        stats = bind_users_by_email(dry_run=False)
        assert stats['matched'] == 1
        assert User.query.get('u1').employee_id == 'e1'


def test_already_bound_skipped(app):
    _seed(app,
          users=[{'id': 'u1', 'email': 'a@corp.com', 'name': 'A', 'employee_id': 'e1'}],
          employees=[{'id': 'e1', 'email': 'a@corp.com', 'name': 'A Emp'}])
    with app.app_context():
        stats = bind_users_by_email(dry_run=False)
        assert stats['matched'] == 0
        assert stats['already_bound'] == 1
        assert User.query.get('u1').employee_id == 'e1'  # 未变


def test_no_employee_match(app):
    _seed(app,
          users=[{'id': 'u1', 'email': 'ghost@corp.com', 'name': 'Ghost'}],
          employees=[{'id': 'e1', 'email': 'real@corp.com', 'name': 'Real'}])
    with app.app_context():
        stats = bind_users_by_email(dry_run=False)
        assert stats['matched'] == 0
        assert stats['no_employee_match'] == 1
        assert User.query.get('u1').employee_id is None


def test_idempotent_second_run(app):
    _seed(app,
          users=[{'id': 'u1', 'email': 'a@corp.com', 'name': 'A'}],
          employees=[{'id': 'e1', 'email': 'a@corp.com', 'name': 'A Emp'}])
    with app.app_context():
        first = bind_users_by_email(dry_run=False)
        assert first['matched'] == 1
        second = bind_users_by_email(dry_run=False)
        assert second['matched'] == 0
        assert second['already_bound'] == 1


def test_dry_run_does_not_write(app):
    _seed(app,
          users=[{'id': 'u1', 'email': 'a@corp.com', 'name': 'A'}],
          employees=[{'id': 'e1', 'email': 'a@corp.com', 'name': 'A Emp'}])
    with app.app_context():
        stats = bind_users_by_email(dry_run=True)
        assert stats['matched'] == 1  # 会匹配
        assert User.query.get('u1').employee_id is None  # 但未写库


def test_two_users_only_one_matches_employee(app):
    """两个账号都未绑定，但只有一个 email 匹配到员工档案；只绑那个。"""
    _seed(app,
          users=[{'id': 'u1', 'email': 'shared@corp.com', 'name': 'A'},
                 {'id': 'u2', 'email': 'other@corp.com', 'name': 'B'}],
          employees=[{'id': 'e1', 'email': 'shared@corp.com', 'name': 'Shared'}])
    with app.app_context():
        stats = bind_users_by_email(dry_run=False)
        assert stats['matched'] == 1
        assert User.query.get('u1').employee_id == 'e1'
        assert User.query.get('u2').employee_id is None  # email 不匹配
