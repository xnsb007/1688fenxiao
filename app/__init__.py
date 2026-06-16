import os
from datetime import timedelta
from flask import Flask, render_template, redirect, url_for, session, request, jsonify
from functools import wraps
from app.config import SECRET_KEY
from app.routes.selection import selection_bp
from app.routes.api import api_bp
from app.routes.dingtalk import dingtalk_bp
from app.routes.logs import logs_bp
from app.routes.auth import auth_bp
from app.routes.ali1688_message import ali1688_message_bp
from app.models import init_db

def login_required(f):
    """同步页面路由的登录检查装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('auth.login_page'))
        return f(*args, **kwargs)
    return decorated_function

def api_login_required(f):
    """API 路由的登录检查装饰器，返回 JSON 错误码供前端跳转"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return jsonify({
                'success': False,
                'error': '账号未登录或登录已过期',
                'code': 'UNAUTHORIZED'
            }), 401
        return f(*args, **kwargs)
    return decorated_function

def create_app():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    from runtime_logging import setup_daily_file_logging
    setup_daily_file_logging('web', log_dir=os.path.join(base_dir, 'logs'))

    template_dir = os.path.join(base_dir, 'templates')
    static_dir = os.path.join(base_dir, 'static')
    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
    app.secret_key = SECRET_KEY

    # 配置 session 过期时间（31 天，浏览器关闭也不失效）
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=31)
    app.config['SESSION_REFRESH_EACH_REQUEST'] = True

    # 添加 CORS 支持
    @app.after_request
    def after_request(response):
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
        response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
        return response

    # 全局请求拦截器，检查 session 状态并自动刷新
    @app.before_request
    def before_request():
        # 跳过登录检查和静态文件
        if request.endpoint and 'static' in request.endpoint:
            return
        if request.endpoint and request.endpoint.startswith('auth.'):
            return
        # 验证码接口不需要登录
        if request.path in ('/api/captcha/get', '/api/captcha/check'):
            return
        if request.path.startswith('/1688/message/') or request.path.startswith('/api/1688/message/'):
            return
        if request.method == 'OPTIONS':
            return

        # 检查 session 是否有效
        if not session.get('logged_in'):
            # 对于 API 请求，返回 401 JSON 错误
            if request.endpoint and ('api' in request.endpoint or request.path.startswith('/api/')):
                return jsonify({
                    'success': False,
                    'error': '账号未登录或登录已过期',
                    'code': 'UNAUTHORIZED'
                }), 401
            # 对于页面请求，重定向到登录页
            if not request.path.startswith('/static'):
                return redirect(url_for('auth.login_page'))
        else:
            try:
                from app.routes.auth import get_valid_token_result
                token_result = get_valid_token_result()
            except Exception as exc:
                token_result = {
                    'success': False,
                    'retryable': True,
                    'code': 'TOKEN_REFRESH_RETRYABLE',
                    'error': str(exc) or '刷新登录状态暂时失败'
                }
            if not token_result.get('success'):
                if request.endpoint and ('api' in request.endpoint or request.path.startswith('/api/')):
                    if token_result.get('retryable'):
                        return jsonify({
                            'success': False,
                            'error': token_result.get('error') or '刷新登录状态暂时失败，请稍后重试',
                            'code': 'TOKEN_REFRESH_RETRYABLE'
                        }), 503
                    session.clear()
                    return jsonify({
                        'success': False,
                        'error': '登录已过期，请重新登录',
                        'code': 'UNAUTHORIZED'
                    }), 401
                if not request.path.startswith('/static'):
                    if token_result.get('retryable'):
                        return
                    session.clear()
                    return redirect(url_for('auth.login_page'))

        # 刷新 session 过期时间
        session.permanent = True

    app.register_blueprint(auth_bp)
    app.register_blueprint(selection_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(dingtalk_bp)
    app.register_blueprint(logs_bp)
    app.register_blueprint(ali1688_message_bp)

    @app.route('/')
    def index():
        if not session.get('logged_in'):
            return redirect(url_for('auth.login_page'))
        return redirect(url_for('products'))

    @app.route('/products')
    def products():
        if not session.get('logged_in'):
            return redirect(url_for('auth.login_page'))
        return render_template('products.html', active_menu='pool')

    @app.route('/products/import-exclusion-reports/<report_id>')
    def import_exclusion_report_page(report_id):
        if not session.get('logged_in'):
            return redirect(url_for('auth.login_page'))
        return render_template('import_exclusion_report.html', active_menu='pool', report_id=report_id)

    init_db()

    try:
        from app.services.erp_sync_reconciler import erp_sync_reconciler
        erp_sync_reconciler.start_background_thread()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception('Failed to start ERP sync reconciler: %s', exc)

    return app
