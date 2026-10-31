"""编制管理 API 路由"""
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from models import db, Department, Employee
from services.tenancy import current_tenant_id
from services.headcount_service import HeadcountService

headcount_bp = Blueprint('headcount', __name__)


@headcount_bp.route('/headcount/summary', methods=['GET'])
@jwt_required()
def get_headcount_summary():
    """获取编制使用情况汇总"""
    tenant_id = current_tenant_id()

    if not tenant_id:
        return jsonify({'error': '租户信息缺失'}), 400

    try:
        result = HeadcountService.get_headcount_summary(tenant_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@headcount_bp.route('/departments/<department_id>/headcount', methods=['PUT'])
@jwt_required()
def update_department_headcount(department_id):
    """更新部门编制"""
    tenant_id = current_tenant_id()
    operator_id = get_jwt_identity()

    if not tenant_id:
        return jsonify({'error': '租户信息缺失'}), 400

    data = request.get_json()
    headcount_plan = data.get('headcount_plan')

    if headcount_plan is None:
        return jsonify({'error': '缺少 headcount_plan 参数'}), 400

    try:
        result = HeadcountService.update_department_headcount(
            department_id=department_id,
            tenant_id=tenant_id,
            headcount_plan=int(headcount_plan),
            operator_id=operator_id,
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@headcount_bp.route('/headcount/positions', methods=['GET'])
@jwt_required()
def get_position_headcount():
    """获取岗位编制统计"""
    tenant_id = current_tenant_id()

    if not tenant_id:
        return jsonify({'error': '租户信息缺失'}), 400

    try:
        stats = HeadcountService.get_position_headcount_stats(tenant_id)
        return jsonify({'stats': stats})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@headcount_bp.route('/headcount/alerts', methods=['GET'])
@jwt_required()
def get_headcount_alerts():
    """获取编制预警"""
    tenant_id = current_tenant_id()

    if not tenant_id:
        return jsonify({'error': '租户信息缺失'}), 400

    try:
        alerts = HeadcountService.check_headcount_alerts(tenant_id)
        return jsonify({'alerts': alerts})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
