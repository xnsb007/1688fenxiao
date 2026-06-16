# -*- coding: utf-8 -*-
"""
任务执行日志路由
"""

from flask import Blueprint, render_template, request, jsonify, session
from app.services.task_log_service import task_log_service
from app.utils.time_utils import get_current_app_date_str

logs_bp = Blueprint('logs', __name__)


@logs_bp.route('/logs')
def logs_page():
    """日志页面"""
    if not session.get('logged_in'):
        return render_template('login.html')
    return render_template(
        'logs.html',
        active_menu='logs',
        default_filter_date=get_current_app_date_str()
    )


@logs_bp.route('/api/logs')
def get_logs():
    """获取日志列表"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('page_size', 20))
    username = request.args.get('username', '')
    request_method = request.args.get('request_method', '')
    success = request.args.get('success', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    
    # 转换 success 参数
    success_bool = None
    if success != '':
        success_bool = success == '1'
    
    result = task_log_service.get_logs(
        page=page,
        page_size=page_size,
        username=username,
        request_method=request_method,
        success=success_bool,
        start_date=start_date,
        end_date=end_date
    )
    
    return jsonify(result)


@logs_bp.route('/api/logs/stats')
def get_log_stats():
    """获取日志统计"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    stats = task_log_service.get_stats()
    return jsonify({
        'success': True,
        'stats': stats
    })


@logs_bp.route('/api/logs/<int:log_id>')
def get_log_detail(log_id):
    """获取日志详情"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    log = task_log_service.get_log_detail(log_id)
    if log:
        return jsonify({
            'success': True,
            'log': log
        })
    else:
        return jsonify({
            'success': False,
            'error': '日志不存在'
        }), 404
