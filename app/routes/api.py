from flask import Blueprint, render_template, request, jsonify, Response, session, current_app, send_file
from functools import lru_cache
import hashlib
from app.services.product_service import product_service
from app.services.product_import_service import product_import_service
from app.services.product_import_job_service import product_import_job_service
from app.services.import_exclusion_report_service import import_exclusion_report_service
from app.services.spu_price_service import spu_price_service
from app.services.synced_adjusted_price_service import synced_adjusted_price_service
from app.services.ali1688_service import ali1688_service
from app.services.erp_category_service import erp_category_service
from app.services.erp_sync_task_store import erp_sync_task_store
from app.services.ali1688_message_service import ali1688_message_service
from app.services.ali1688_product_follow_service import ali1688_product_follow_service
from app.services.erp_sync_progress_applier import (
    apply_task_progress_to_local,
    extract_failed_offer_ids_by_details as _extract_failed_offer_ids_by_details_impl,
    extract_offer_reason_map_by_details as _extract_offer_reason_map_by_details_impl,
    update_failed_sync_status_by_details as _update_failed_sync_status_by_details_impl,
)
from app.routes.auth import get_valid_token, require_auth
from app.models import get_db
from app.services.task_log_service import task_log_service
import threading
import requests
import base64
import os
import json
import time
import logging
import tempfile
from collections import Counter
from decimal import Decimal
from datetime import datetime
from uuid import uuid4

api_bp = Blueprint('api', __name__)
logger = logging.getLogger(__name__)
_IMPORT_PROGRESS = {}
_IMPORT_PROGRESS_LOCK = threading.Lock()
_SPU_PRICE_PROGRESS = {}
_SPU_PRICE_PROGRESS_LOCK = threading.Lock()
_SYNCED_ADJUSTED_PRICE_PROGRESS = {}
_SYNCED_ADJUSTED_PRICE_PROGRESS_LOCK = threading.Lock()
TOP_LEVEL_CATEGORY_NOT_ALLOWED_CODE = 'TOP_LEVEL_CATEGORY_NOT_ALLOWED'
TOP_LEVEL_CATEGORY_NOT_ALLOWED_MESSAGE = '顶级类目不允许直接同步，请选择二级或更深类目后重试。'

def _set_import_progress(task_id, progress=0, message=''):
    if not task_id:
        return
    with _IMPORT_PROGRESS_LOCK:
        _IMPORT_PROGRESS[task_id] = {
            'progress': max(0, min(100, int(progress))),
            'message': message or '',
            'updated_at': int(time.time() * 1000)
        }


def _set_spu_price_progress(
    task_id,
    current=0,
    total=0,
    success_count=0,
    fail_count=0,
    message='',
    done=False,
    result=None,
    error='',
):
    if not task_id:
        return
    total_value = max(0, int(total or 0))
    current_value = max(0, int(current or 0))
    progress = 100 if done else (int((current_value / total_value) * 100) if total_value else 0)
    with _SPU_PRICE_PROGRESS_LOCK:
        _SPU_PRICE_PROGRESS[task_id] = {
            'success': True,
            'task_id': task_id,
            'current': current_value,
            'total': total_value,
            'success_count': int(success_count or 0),
            'fail_count': int(fail_count or 0),
            'progress': max(0, min(100, progress)),
            'message': message or '',
            'done': bool(done),
            'updated_at': int(time.time() * 1000),
            'error': error or ''
        }
        if result is not None:
            _SPU_PRICE_PROGRESS[task_id]['result'] = result


def _set_synced_adjusted_price_progress(
    task_id,
    current=0,
    total=0,
    success_count=0,
    fail_count=0,
    message='',
    done=False,
    result=None,
    error='',
):
    if not task_id:
        return
    total_value = max(0, int(total or 0))
    current_value = max(0, int(current or 0))
    progress = 100 if done else (int((current_value / total_value) * 100) if total_value else 0)
    with _SYNCED_ADJUSTED_PRICE_PROGRESS_LOCK:
        _SYNCED_ADJUSTED_PRICE_PROGRESS[task_id] = {
            'success': True,
            'task_id': task_id,
            'current': current_value,
            'total': total_value,
            'success_count': int(success_count or 0),
            'fail_count': int(fail_count or 0),
            'progress': max(0, min(100, progress)),
            'message': message or '',
            'done': bool(done),
            'updated_at': int(time.time() * 1000),
            'error': error or ''
        }
        if result is not None:
            _SYNCED_ADJUSTED_PRICE_PROGRESS[task_id]['result'] = result

def _collect_token_snapshot_for_task():
    """在当前 Flask 请求上下文里抓取当前登录用户的 token / tenant / 过期时间。
    供 _register_erp_sync_task 将其快照到 erp_sync_tasks 表中，后台 reconciler 使用。"""
    try:
        access_token = str(session.get('token', '') or session.get('access_token', '') or '').strip()
        refresh_token = str(session.get('refresh_token', '') or session.get('refreshToken', '') or '').strip()
        tenant_id = str(session.get('tenant_id', '') or '').strip()
        expire_time = int(session.get('token_expire_time', 0) or session.get('expiresTime', 0) or 0)
        username = str(session.get('username', '') or '').strip()
        return {
            'access_token': access_token,
            'refresh_token': refresh_token,
            'tenant_id': tenant_id,
            'expire_time': expire_time,
            'username': username,
        }
    except Exception:
        return {'access_token': '', 'refresh_token': '', 'tenant_id': '', 'expire_time': 0, 'username': ''}

def _register_erp_sync_task(task_no, offer_ids, source, immediate_failed_offer_ids=None, token_snapshot=None):
    if not task_no:
        return
    snapshot = token_snapshot if isinstance(token_snapshot, dict) else _collect_token_snapshot_for_task()
    erp_sync_task_store.register_task(
        task_no=task_no,
        offer_ids=[str(x) for x in (offer_ids or []) if x],
        source=source or 'ALIBABA_1688',
        immediate_failed_offer_ids=[str(x) for x in (immediate_failed_offer_ids or []) if x],
        access_token=snapshot.get('access_token', ''),
        refresh_token=snapshot.get('refresh_token', ''),
        tenant_id=snapshot.get('tenant_id', ''),
        expire_time=snapshot.get('expire_time', 0),
        username=snapshot.get('username', ''),
    )

def _get_erp_sync_task(task_no):
    return erp_sync_task_store.get_task(task_no)

def _mark_erp_sync_task_finalized(task_no, last_task_status=None, last_task_status_desc='', reconcile_error=''):
    erp_sync_task_store.mark_finalized(
        task_no,
        last_task_status=last_task_status,
        last_task_status_desc=last_task_status_desc,
        reconcile_error=reconcile_error,
    )

def _extract_failed_offer_ids_by_details(failed_details, offer_ids):
    return _extract_failed_offer_ids_by_details_impl(failed_details, offer_ids)

def _extract_success_offer_ids_by_details(success_details, offer_ids):
    offer_ids_str = [str(x) for x in (offer_ids or [])]
    offer_id_set = set(offer_ids_str)
    success_offer_ids = set()
    for item in (success_details or []):
        if not isinstance(item, dict):
            continue
        index = item.get('index')
        if isinstance(index, int) and 0 <= index < len(offer_ids_str):
            success_offer_ids.add(offer_ids_str[index])
        spu_id = item.get('spuId') or item.get('offer_id') or item.get('spu_id')
        spu_id_str = str(spu_id) if spu_id is not None else ''
        if spu_id_str and spu_id_str in offer_id_set:
            success_offer_ids.add(spu_id_str)
    return list(success_offer_ids)

def _extract_offer_reason_map_by_details(failed_details, offer_ids, fallback_reason=''):
    return _extract_offer_reason_map_by_details_impl(failed_details, offer_ids, fallback_reason=fallback_reason)

def _update_failed_sync_status_by_details(failed_details, offer_ids, fallback_reason='', source='ALIBABA_1688'):
    return _update_failed_sync_status_by_details_impl(failed_details, offer_ids, fallback_reason=fallback_reason, source=source)

def _build_sync_failure_summary(failed_details, fallback_reason=''):
    reason_counter = Counter()
    sample_items = []
    for item in (failed_details or []):
        if not isinstance(item, dict):
            continue
        reason = str(item.get('reason') or item.get('message') or '').strip()
        if not reason:
            continue
        reason_counter[reason] += 1
        if len(sample_items) < 3:
            sample_items.append({
                'index': item.get('index'),
                'spuId': item.get('spuId') or item.get('offer_id') or item.get('spu_id') or '',
                'spuName': item.get('spuName') or item.get('name') or '',
                'reason': reason
            })

    if not reason_counter and fallback_reason:
        reason_counter[str(fallback_reason).strip()] += 1

    reason_counts = [
        {'reason': reason, 'count': count}
        for reason, count in reason_counter.most_common(3)
    ]
    primary_reason = reason_counts[0]['reason'] if reason_counts else str(fallback_reason or '').strip()

    guidance = ''
    code = ''
    if TOP_LEVEL_CATEGORY_NOT_ALLOWED_MESSAGE in primary_reason or TOP_LEVEL_CATEGORY_NOT_ALLOWED_CODE in primary_reason:
        code = TOP_LEVEL_CATEGORY_NOT_ALLOWED_CODE
        guidance = TOP_LEVEL_CATEGORY_NOT_ALLOWED_MESSAGE
    elif '商品分类不正确' in primary_reason or '类目' in primary_reason:
        code = 'CATEGORY_VALIDATION_FAILED'
        guidance = '请检查商品 ERP 类目映射是否有效后重试。'
    elif '系统异常' in primary_reason:
        code = 'ERP_INTERNAL_ERROR'
        guidance = 'ERP返回系统异常，建议稍后重试；若持续失败，请结合任务号排查ERP服务日志。'
    elif '登录已过期' in primary_reason or '未登录' in primary_reason or '权限' in primary_reason:
        code = 'AUTH_OR_PERMISSION'
        guidance = '请重新登录，并确认当前账号具备ERP商品创建权限及正确的tenant-id。'

    return {
        'code': code,
        'primary_reason': primary_reason,
        'reason_counts': reason_counts,
        'sample_items': sample_items,
        'guidance': guidance
    }

def _collect_top_level_category_failures(products):
    failed_details = []
    for index, product in enumerate(products or []):
        if not isinstance(product, dict):
            continue
        path_info = erp_category_service.get_category_path_info(product.get('erp_category_id'))
        if path_info.get('level') != 1:
            continue
        path_names = path_info.get('path_names') or [str(product.get('erp_category_name') or '').strip()]
        failed_details.append({
            'index': index,
            'spuId': str(product.get('offer_id') or '').strip(),
            'spuName': str(product.get('title') or '').strip(),
            'reason': TOP_LEVEL_CATEGORY_NOT_ALLOWED_MESSAGE,
            'erpCategoryId': path_info.get('category_id'),
            'erpCategoryName': (path_names[-1] if path_names else str(product.get('erp_category_name') or '').strip()),
            'erpCategoryPath': ' / '.join(path_names),
            'erpCategoryLevel': path_info.get('level', 0)
        })
    return failed_details

def _build_top_level_category_block_result(products):
    failed_details = _collect_top_level_category_failures(products)
    if not failed_details:
        return None
    result = {
        'success': False,
        'code': TOP_LEVEL_CATEGORY_NOT_ALLOWED_CODE,
        'error': TOP_LEVEL_CATEGORY_NOT_ALLOWED_MESSAGE,
        'total': len(products or []),
        'success_count': 0,
        'fail_count': len(failed_details),
        'failed_details': failed_details
    }
    result['failure_summary'] = _build_sync_failure_summary(failed_details, TOP_LEVEL_CATEGORY_NOT_ALLOWED_MESSAGE)
    return result

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)

def _log_task(request_method, request_path, request_params, response_data, success, error_message='', execution_time=0):
    """记录任务执行日志"""
    try:
        username = session.get('username', 'unknown')
        ip_address = request.remote_addr or ''
        user_agent = request.headers.get('User-Agent', '')[:500]
        
        task_log_service.log(
            username=username,
            request_method=request_method,
            request_path=request_path,
            request_params=request_params,
            response_data=response_data,
            success=success,
            error_message=error_message,
            ip_address=ip_address,
            user_agent=user_agent,
            execution_time=execution_time
        )
    except Exception as e:
        print(f"[Task Log] Failed to log: {e}")

def _normalize_offer_ids(offer_ids):
    normalized = []
    seen = set()
    for offer_id in offer_ids or []:
        offer_id_str = str(offer_id or '').strip()
        if not offer_id_str or offer_id_str in seen:
            continue
        normalized.append(offer_id_str)
        seen.add(offer_id_str)
    return normalized

def _fetch_detail_by_source(offer_id, source='ALIBABA_1688'):
    source_value = str(source or '').lower()
    if source_value in ('df', 'alibaba_1688'):
        return ali1688_service.get_distribution_product_info(offer_id)
    return ali1688_service.get_product_detail(offer_id)

def _has_import_detail_data(product):
    if not isinstance(product, dict):
        return False
    return bool(
        (product.get('description') and str(product.get('description')).strip())
        or (product.get('sku_info') and str(product.get('sku_info')).strip())
        or (product.get('images') and str(product.get('images')).strip())
        or (product.get('attributes') and str(product.get('attributes')).strip())
    )

@api_bp.route('/api/products')
def list_products():
    source = request.args.get('source', 'ALIBABA_1688')
    status = request.args.get('status', 'all')
    sync_status = request.args.get('sync_status', 'pending')  # 默认未同步
    if not status or status == 'all':
        status = 'all'
    # sync_status 为'all'时不筛选，否则按值筛选
    if sync_status == 'all':
        sync_status = None
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('page_size', 20))

    filters = {
        'keyword': request.args.get('keyword'),
        'price_min': float(request.args.get('price_min')) if request.args.get('price_min') else None,
        'price_max': float(request.args.get('price_max')) if request.args.get('price_max') else None,
        'sales_min': int(request.args.get('sales_min')) if request.args.get('sales_min') else None,
        'sales_max': int(request.args.get('sales_max')) if request.args.get('sales_max') else None,
        'supplier': request.args.get('supplier'),
        'location': request.args.get('location'),
        'sync_status': sync_status,
        'price_adjusted': request.args.get('price_adjusted') or None,
        'include_keywords': [k.strip() for k in request.args.get('include_keywords', '').split(',') if k.strip()],
        'exclude_keywords': [k.strip() for k in request.args.get('exclude_keywords', '').split(',') if k.strip()]
    }

    filters = {k: v for k, v in filters.items() if v is not None and v != []}

    result = product_service.list_products(status, page, page_size, filters if filters else None, source=source)
    return jsonify(result)

@api_bp.route('/api/products/stats')
def get_stats():
    source = request.args.get('source', 'ALIBABA_1688')
    stats = product_service.get_stats(source=source)
    stats['product_changes'] = ali1688_message_service.get_product_change_stats(source=source)
    return jsonify({'success': True, 'stats': stats})


@api_bp.route('/api/products/spu-price/counts', methods=['GET'])
def get_spu_price_operation_counts():
    return jsonify({
        'success': True,
        'all_count': spu_price_service.count_all_products(),
        'synced_count': spu_price_service.count_synced_products()
    })


@api_bp.route('/api/products/spu-price/min-sku', methods=['POST'])
def update_spu_price_from_min_sku():
    start_time = time.time()
    data = request.get_json(silent=True) or {}
    batch_size = int(data.get('batch_size') or 1000)
    task_id = str(data.get('task_id') or uuid4().hex).strip()
    _set_spu_price_progress(task_id, 0, 0, 0, 0, '准备更新SPU价格...')
    operator = session.get('username', 'unknown')
    ip_address = request.remote_addr or ''
    user_agent = request.headers.get('User-Agent', '')[:500]
    app_obj = current_app._get_current_object()

    def run_min_sku_task():
        task_started = time.time()

        def progress_callback(current, total, success_count, fail_count, message=''):
            _set_spu_price_progress(task_id, current, total, success_count, fail_count, message)

        with app_obj.app_context():
            try:
                result = spu_price_service.update_spu_prices_from_min_sku(
                    batch_size=batch_size,
                    progress_callback=progress_callback
                )
                result['task_id'] = task_id
                _set_spu_price_progress(
                    task_id,
                    result.get('processed_count', 0),
                    result.get('total', 0),
                    result.get('success_count', 0),
                    result.get('fail_count', 0),
                    result.get('message') or ('SPU价格更新完成' if result.get('success') else result.get('error', 'SPU价格更新失败')),
                    done=True,
                    result=result,
                    error='' if result.get('success') else result.get('error', 'SPU最低SKU价格更新失败')
                )
                execution_time = int((time.time() - task_started) * 1000)
                task_log_service.log(
                    username=operator,
                    request_method='POST',
                    request_path='/api/products/spu-price/min-sku',
                    request_params={'batch_size': batch_size, 'task_id': task_id, 'operator': operator},
                    response_data={
                        'job_id': result.get('job_id'),
                        'total': result.get('total'),
                        'processed_count': result.get('processed_count'),
                        'success_count': result.get('success_count'),
                        'fail_count': result.get('fail_count'),
                        'success_log_id': result.get('success_log_id'),
                        'failure_log_id': result.get('failure_log_id'),
                        'success_details': result.get('success_details', [])[:10],
                        'failed_details': result.get('failed_details', [])[:10],
                    },
                    success=bool(result.get('success')) or bool(result.get('partial_success')),
                    error_message='' if result.get('success') else result.get('error', 'SPU最低SKU价格更新失败'),
                    ip_address=ip_address,
                    user_agent=user_agent,
                    execution_time=execution_time
                )
            except Exception as exc:
                logger.exception('[SPU Min SKU Price] background task failed')
                result = {
                    'success': False,
                    'task_id': task_id,
                    'error': str(exc),
                    'total': 0,
                    'processed_count': 0,
                    'success_count': 0,
                    'fail_count': 0,
                    'success_details': [],
                    'failed_details': [],
                }
                _set_spu_price_progress(task_id, 0, 0, 0, 0, str(exc), done=True, result=result, error=str(exc))

    thread = threading.Thread(target=run_min_sku_task, daemon=True)
    thread.start()

    return jsonify({
        'success': True,
        'async_task': True,
        'task_id': task_id,
        'message': 'SPU价格更新任务已开始',
        'accepted_time_ms': int((time.time() - start_time) * 1000)
    }), 202


@api_bp.route('/api/products/spu-price/sync-erp', methods=['POST'])
def sync_spu_price_to_erp():
    start_time = time.time()
    data = request.get_json(silent=True) or {}
    batch_size = int(data.get('batch_size') or 1000)
    status_policy = str(data.get('status_policy') or 'keep_synced').strip()
    task_id = str(data.get('task_id') or uuid4().hex).strip()
    _set_spu_price_progress(task_id, 0, 0, 0, 0, '准备同步SPU价格到ERP...')

    access_token = get_valid_token()
    if not access_token:
        return jsonify({'success': False, 'code': 'UNAUTHORIZED', 'error': '登录已过期，请重新登录'}), 401

    def progress_callback(current, total, success_count, fail_count, message=''):
        _set_spu_price_progress(task_id, current, total, success_count, fail_count, message)

    result = spu_price_service.sync_synced_spu_prices_to_erp(
        access_token=access_token,
        tenant_id=session.get('tenant_id', ''),
        batch_size=batch_size,
        timeout=30,
        max_retries=3,
        status_policy=status_policy,
        progress_callback=progress_callback,
    )
    _set_spu_price_progress(
        task_id,
        result.get('processed_count', 0),
        result.get('total', 0),
        result.get('success_count', 0),
        result.get('fail_count', 0),
        result.get('message') or ('同步SPU价格完成' if result.get('success') else result.get('error', '同步SPU价格失败')),
        done=True
    )
    result['task_id'] = task_id
    execution_time = int((time.time() - start_time) * 1000)
    log_success = bool(result.get('success')) or bool(result.get('empty')) or bool(result.get('partial_success'))
    _log_task(
        'POST',
        '/api/products/spu-price/sync-erp',
        {
            'batch_size': batch_size,
            'status_policy': status_policy,
            'task_id': task_id,
            'operator': session.get('username', 'unknown'),
        },
        {
            'job_id': result.get('job_id'),
            'total': result.get('total'),
            'processed_count': result.get('processed_count'),
            'success_count': result.get('success_count'),
            'fail_count': result.get('fail_count'),
            'failure_log_id': result.get('failure_log_id'),
            'erp_request_snapshots': result.get('erp_request_snapshots', [])[:1],
            'erp_response_snapshots': result.get('erp_response_snapshots', [])[:1],
            'failed_details': result.get('failed_details', [])[:10],
        },
        log_success,
        '' if log_success else result.get('error', '同步SPU价格失败'),
        execution_time,
    )
    status_code = 200 if result.get('success') or result.get('empty') or result.get('partial_success') else 500
    return jsonify(result), status_code


@api_bp.route('/api/products/spu-price/progress/<task_id>', methods=['GET'])
def get_spu_price_progress(task_id):
    task_id = str(task_id or '').strip()
    if not task_id:
        return jsonify({'success': False, 'error': 'task_id required'}), 400
    with _SPU_PRICE_PROGRESS_LOCK:
        progress_data = dict(_SPU_PRICE_PROGRESS.get(task_id) or {})
    if not progress_data:
        return jsonify({'success': False, 'error': 'task not found'}), 404
    return jsonify(progress_data)


@api_bp.route('/api/products/spu-price/failure-log/<log_id>', methods=['GET'])
def download_spu_price_failure_log(log_id):
    content = spu_price_service.export_failure_log_csv(log_id)
    if content is None:
        return jsonify({'success': False, 'error': 'failure log not found'}), 404
    return Response(
        content,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=spu_price_failures_{log_id}.csv'}
    )


@api_bp.route('/api/products/spu-price/success-log/<log_id>', methods=['GET'])
def download_spu_price_success_log(log_id):
    content = spu_price_service.export_success_log_csv(log_id)
    if content is None:
        return jsonify({'success': False, 'error': 'success log not found'}), 404
    return Response(
        content,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=spu_price_successes_{log_id}.csv'}
    )

@api_bp.route('/api/products/changes')
def list_product_changes():
    source = request.args.get('source', 'ALIBABA_1688')
    change_type = request.args.get('change_type', 'all')
    keyword = request.args.get('keyword', '')
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('page_size', 20))
    result = ali1688_message_service.list_product_changes(
        page=page,
        page_size=page_size,
        change_type=change_type,
        keyword=keyword,
        source=source,
    )
    return jsonify(result)

@api_bp.route('/api/1688/products/follow/batch', methods=['POST'])
def batch_follow_synced_products():
    start_time = time.time()
    data = request.get_json(silent=True) or {}
    offer_ids = data.get('offer_ids', [])
    source = data.get('source', 'ALIBABA_1688')
    max_retries = int(data.get('max_retries', 3))
    qps = float(data.get('qps', 1.0))

    if not offer_ids:
        return jsonify({'success': False, 'error': 'offer_ids required'}), 400

    conn = get_db()
    cursor = conn.cursor()
    try:
        placeholders = ','.join(['%s'] * len(offer_ids))
        cursor.execute(
            f'''
                SELECT id, offer_id, source_type, sync_status,
                       COALESCE(follow_status, 'not_followed') AS follow_status
                FROM import_product
                WHERE source_type = %s
                  AND sync_status = 'synced'
                  AND offer_id IN ({placeholders})
            ''',
            [source] + list(offer_ids)
        )
        products = [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

    if not products:
        return jsonify({'success': False, 'error': '未找到已同步商品'}), 400

    followed = 0
    failed = 0
    skipped = 0
    details = []
    interval = 1.0 / max(float(qps), 0.1)

    for index, product in enumerate(products):
        if index > 0:
            time.sleep(interval)
        detail = ali1688_product_follow_service.follow_one_product(product, max_retries=max_retries)
        details.append(detail)
        if detail.get('success'):
            followed += 1
        elif detail.get('skipped'):
            skipped += 1
        else:
            failed += 1

    result = {
        'success': failed == 0,
        'total': len(products),
        'followed': followed,
        'failed': failed,
        'skipped': skipped,
        'details': details
    }

    execution_time = int((time.time() - start_time) * 1000)
    _log_task(
        'POST',
        '/api/1688/products/follow/batch',
        {'offer_ids': offer_ids, 'source': source},
        result,
        bool(result.get('success')),
        '' if result.get('success') else f"{failed} follow failures",
        execution_time
    )
    return jsonify(result)

@api_bp.route('/api/1688/products/follow/run', methods=['POST'])
def run_1688_product_follow_job():
    start_time = time.time()
    data = request.get_json(silent=True) or {}
    dry_run = bool(data.get('dry_run') or request.args.get('dry_run') in ('1', 'true', 'yes'))
    retry_failed_only = bool(
        data.get('retry_failed_only')
        or request.args.get('retry_failed_only') in ('1', 'true', 'yes')
    )
    date_text = data.get('date') or request.args.get('date')
    source = data.get('source') or request.args.get('source') or 'ALIBABA_1688'
    limit = int(data.get('limit') or request.args.get('limit') or 500)
    max_retries = int(data.get('max_retries') or request.args.get('max_retries') or 3)
    qps = float(data.get('qps') or request.args.get('qps') or 1.0)

    result = ali1688_product_follow_service.run_daily_follow(
        date_text=date_text,
        retry_failed_only=retry_failed_only,
        dry_run=dry_run,
        limit=limit,
        max_retries=max_retries,
        qps=qps,
        source=source,
    )
    execution_time = int((time.time() - start_time) * 1000)
    _log_task(
        'POST',
        '/api/1688/products/follow/run',
        {
            'date': date_text,
            'dry_run': dry_run,
            'retry_failed_only': retry_failed_only,
            'limit': limit,
            'max_retries': max_retries,
            'qps': qps,
            'source': source,
        },
        result,
        bool(result.get('success')),
        '' if result.get('success') else result.get('message') or result.get('error') or 'follow job failed',
        execution_time,
    )
    return jsonify(result), (200 if result.get('success') or result.get('lock_skipped') else 500)

@api_bp.route('/api/1688/products/follow/logs')
def list_1688_product_follow_logs():
    source = request.args.get('source', 'ALIBABA_1688')
    offer_id = request.args.get('offer_id', '')
    status = request.args.get('status', '')
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('page_size', 20))
    result = ali1688_product_follow_service.list_follow_logs(
        page=page,
        page_size=page_size,
        offer_id=offer_id,
        status=status,
        source=source,
    )
    return jsonify(result)

@api_bp.route('/api/1688/products/follow/stats')
def get_1688_product_follow_stats():
    source = request.args.get('source', 'ALIBABA_1688')
    return jsonify(ali1688_product_follow_service.get_follow_stats(source=source))

@api_bp.route('/api/erp/categories/sync', methods=['POST'])
def sync_erp_categories():
    from app.services.erp_category_service import erp_category_service
    access_token = session.get('token', '').strip()
    if not access_token:
        access_token = (request.headers.get('token') or request.headers.get('accessToken') or request.headers.get('Authorization') or '').strip()
        if access_token.lower().startswith('bearer '):
            access_token = access_token[7:].strip()
    tenant_id = session.get('tenant_id', '')
    result = erp_category_service.sync_categories(access_token=access_token, tenant_id=tenant_id)
    if result.get('success'):
        result['stats'] = erp_category_service.get_stats()
    print(f"[ERP Category Sync] Result: {json.dumps(result, ensure_ascii=False, cls=DecimalEncoder)}")
    return jsonify(result)

@api_bp.route('/api/erp/categories/stats', methods=['GET'])
def get_erp_category_stats():
    from app.services.erp_category_service import erp_category_service
    stats = erp_category_service.get_stats()
    return jsonify({'success': True, 'stats': stats})

@api_bp.route('/api/products/<offer_id>', methods=['GET'])
def get_product(offer_id):
    source = request.args.get('source', 'ALIBABA_1688')
    product = product_service.get_product(offer_id, source=source)
    if product:
        return jsonify({'success': True, 'product': product})
    return jsonify({'success': False, 'error': 'Product not found'}), 404

@api_bp.route('/api/products/<offer_id>/sku-info', methods=['GET'])
def get_product_sku_info(offer_id):
    source = request.args.get('source', 'ALIBABA_1688')
    sku_info = product_service.get_product_sku_info(offer_id, source=source)
    if sku_info is not None:
        return jsonify({'success': True, 'sku_info': sku_info})
    return jsonify({'success': False, 'error': 'Product not found'}), 404

@api_bp.route('/api/products/<offer_id>/shipping-info', methods=['GET'])
def get_product_shipping_info(offer_id):
    source = request.args.get('source', 'ALIBABA_1688')
    shipping_info = product_service.get_product_shipping_info(offer_id, source=source)
    if shipping_info is not None:
        return jsonify({'success': True, 'shipping_info': shipping_info})
    return jsonify({'success': False, 'error': 'Product not found'}), 404

@api_bp.route('/api/products/<offer_id>/list', methods=['POST'])
def list_product(offer_id):
    data = request.get_json() or {}
    source = data.get('source', request.args.get('source', 'ALIBABA_1688'))
    success = product_service.update_status(offer_id, 'listed', source=source)
    return jsonify({'success': success})

@api_bp.route('/api/products/<offer_id>/unlist', methods=['POST'])
def unlist_product(offer_id):
    data = request.get_json() or {}
    source = data.get('source', request.args.get('source', 'ALIBABA_1688'))
    success = product_service.update_status(offer_id, 'selected', source=source)
    return jsonify({'success': success})

@api_bp.route('/api/products/<offer_id>/price', methods=['POST'])
def update_price(offer_id):
    data = request.get_json()
    sell_price = data.get('sell_price')
    source = data.get('source', 'ALIBABA_1688')
    if sell_price:
        success = product_service.update_price(offer_id, sell_price, source=source)
        return jsonify({'success': success})
    return jsonify({'success': False, 'error': 'sell_price required'}), 400

@api_bp.route('/api/products/batch/list', methods=['POST'])
def batch_list_products():
    data = request.get_json()
    offer_ids = data.get('offer_ids', [])
    source = data.get('source', 'ALIBABA_1688')
    if not offer_ids:
        return jsonify({'success': False, 'error': 'offer_ids required'}), 400
    
    affected = product_service.batch_update_status(offer_ids, 'listed', source=source)
    return jsonify({'success': True, 'affected': affected})

@api_bp.route('/api/products/batch/unlist', methods=['POST'])
def batch_unlist_products():
    data = request.get_json()
    offer_ids = data.get('offer_ids', [])
    source = data.get('source', 'ALIBABA_1688')
    if not offer_ids:
        return jsonify({'success': False, 'error': 'offer_ids required'}), 400
    
    affected = product_service.batch_update_status(offer_ids, 'selected', source=source)
    return jsonify({'success': True, 'affected': affected})

@api_bp.route('/api/products/<int:product_id>/delete', methods=['POST'])
def delete_product_by_id(product_id):
    start_time = time.time()
    data = request.get_json(silent=True) or {}
    source = data.get('source', 'ALIBABA_1688')

    def _erp_delete_executor(product):
        access_token = get_valid_token()
        if not access_token:
            return {
                'success': False,
                'code': 'UNAUTHORIZED',
                'error': '登录已过期，请重新登录'
            }
        from app.services.erp_sync_service import erp_sync_service
        return erp_sync_service.delete_product(
            product,
            access_token=access_token,
            tenant_id=session.get('tenant_id', '')
        )

    result = product_service.delete_product_by_id_transactional(
        product_id,
        source=source,
        erp_delete_executor=_erp_delete_executor
    )
    response_result = dict(result)
    response_result.pop('product', None)

    execution_time = int((time.time() - start_time) * 1000)
    if result.get('success'):
        _log_task(
            'POST',
            f'/api/products/{product_id}/delete',
            {'product_id': product_id, 'source': source},
            response_result,
            True,
            '',
            execution_time
        )
        return jsonify(response_result)

    status_code = 500
    if result.get('code') == 'UNAUTHORIZED':
        status_code = 401
    elif result.get('code') == 'ERP_DELETE_SERVICE_ERROR':
        status_code = 503
    elif result.get('code') == 'ERP_DELETE_INVALID_ID':
        status_code = 400

    _log_task(
        'POST',
        f'/api/products/{product_id}/delete',
        {'product_id': product_id, 'source': source},
        response_result,
        False,
        result.get('error', '删除失败'),
        execution_time
    )
    return jsonify(response_result), status_code

@api_bp.route('/api/products/batch/delete', methods=['POST'])
def batch_delete_products():
    start_time = time.time()
    data = request.get_json()
    offer_ids = data.get('offer_ids', [])
    source = data.get('source', 'ALIBABA_1688')
    if not offer_ids:
        return jsonify({'success': False, 'error': 'offer_ids required'}), 400
    
    affected = product_service.batch_delete(offer_ids, source=source)
    execution_time = int((time.time() - start_time) * 1000)
    _log_task('POST', '/api/products/batch/delete', {'offer_ids': offer_ids, 'source': source}, {'affected': affected}, True, '', execution_time)
    return jsonify({'success': True, 'affected': affected})


@api_bp.route('/api/products/batch/delete-synced', methods=['POST'])
@require_auth
def batch_delete_synced_products():
    """删除已同步商品 - 先调用ERP API删除，再删除本地数据"""
    start_time = time.time()
    data = request.get_json() or {}
    offer_ids = data.get('offer_ids', [])
    source = data.get('source', 'ALIBABA_1688')
    
    if not offer_ids:
        return jsonify({'success': False, 'error': 'offer_ids required'}), 400
    
    print(f"[Delete Synced] Request: offer_ids count={len(offer_ids)}, source={source}")
    
    # 获取access_token
    access_token = get_valid_token()
    if not access_token:
        return jsonify({'success': False, 'code': 'UNAUTHORIZED', 'error': '登录已过期，请重新登录'}), 401
    
    tenant_id = session.get('tenant_id', '')
    
    # 调用ERP API删除商品
    erp_api_url = os.environ.get('ERP_API_URL', 'https://dev.1bgo.com').rstrip('/')
    target_url = f'{erp_api_url}/admin-api/product/spu/batch-delete'
    
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }
    if tenant_id:
        headers['tenant-id'] = str(tenant_id)
        headers['tenantId'] = str(tenant_id)
    
    ids_param = []
    for oid in offer_ids:
        try:
            ids_param.append(int(oid))
        except (ValueError, TypeError):
            continue
    
    request_body = {'ids': ids_param}
    
    print(f"[Delete Synced] Calling ERP API: {target_url}")
    print(f"[Delete Synced] Request body: {json.dumps(request_body)}")
    
    try:
        response = requests.put(
            target_url,
            json=request_body,
            headers=headers,
            timeout=30
        )
        
        print(f"[Delete Synced] Response status: {response.status_code}")
        print(f"[Delete Synced] Response text: {response.text[:500]}")
        
        if not response.headers.get('content-type', '').startswith('application/json'):
            return jsonify({
                'success': False,
                'error': f'ERP返回非JSON响应: {response.text[:200]}'
            }), 500
        
        resp_data = response.json()
        
        # 检查ERP响应
        if response.status_code not in (200, 201) or str(resp_data.get('code')) != '0':
            error_msg = resp_data.get('msg') or resp_data.get('message') or 'ERP删除失败'
            return jsonify({
                'success': False,
                'error': error_msg,
                'erp_response': resp_data
            }), 400
        
        # ERP删除成功，解析响应
        erp_data = resp_data.get('data', {})
        task_no = erp_data.get('taskNo', '')
        immediate_fail_count = erp_data.get('immediateFailCount', 0)
        immediate_fail_details = erp_data.get('immediateFailDetails', [])
        immediate_success_details = erp_data.get('immediateSuccessDetails', [])
        
        print(f"[Delete Synced] ERP task_no: {task_no}, immediate_fail_count: {immediate_fail_count}")
        
        # 获取即时成功的offer_id列表
        success_offer_ids = set(offer_ids)
        
        # 从失败详情中移除失败的offer_id
        if immediate_fail_details:
            for item in immediate_fail_details:
                failed_id = str(item.get('spuId') or item.get('spu_id') or item.get('offer_id') or '')
                if failed_id in success_offer_ids:
                    success_offer_ids.discard(failed_id)
        
        # 删除本地数据库记录（只删除成功的）
        if success_offer_ids:
            affected = product_service.batch_delete(list(success_offer_ids), source=source)
        else:
            affected = 0
        
        execution_time = int((time.time() - start_time) * 1000)
        _log_task('POST', '/api/products/batch/delete-synced', 
                  {'offer_ids': offer_ids, 'source': source}, 
                  {'affected': affected, 'task_no': task_no}, 
                  True, '', execution_time)
        
        result = {
            'success': True,
            'affected': affected,
            'erp_task_no': task_no,
            'immediate_fail_count': immediate_fail_count,
            'immediate_fail_details': immediate_fail_details
        }
        
        if immediate_fail_count > 0:
            result['message'] = f'成功删除 {affected} 件商品，{immediate_fail_count} 件删除失败'
        else:
            result['message'] = f'成功删除 {affected} 件商品'
        
        return jsonify(result)
        
    except requests.exceptions.Timeout:
        print(f"[Delete Synced] Request timeout")
        return jsonify({'success': False, 'error': '请求ERP系统超时'}), 504
    except requests.exceptions.ConnectionError as e:
        print(f"[Delete Synced] Connection error: {e}")
        return jsonify({'success': False, 'error': '无法连接到ERP系统'}), 503
    except Exception as e:
        print(f"[Delete Synced] Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/api/products/batch/adjust-price', methods=['POST'])
def batch_adjust_price():
    """批量调价接口 - 支持百分比调价"""
    data = request.get_json()
    offer_ids = data.get('offer_ids', [])
    adjust_rate = data.get('adjust_rate', 0)
    source = data.get('source', 'ALIBABA_1688')

    if not offer_ids:
        return jsonify({'success': False, 'error': 'offer_ids required'}), 400

    try:
        adjust_rate = float(adjust_rate) if adjust_rate is not None else 0
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': '调价参数格式错误'}), 400

    if adjust_rate < -0.5:
        return jsonify({'success': False, 'error': '调价比例不能低于 -50%'}), 400
    if adjust_rate > 5:
        return jsonify({'success': False, 'error': '调价比例不能高于 500%'}), 400

    result = product_service.batch_adjust_price(offer_ids, adjust_rate, 0, source=source)

    if result.get('success'):
        return jsonify({
            'success': True,
            'message': f'成功调整 {result["affected"]} 件商品价格',
            'affected': result['affected'],
            'total': result['total'],
            'details': result['details']
        })
    else:
        return jsonify({
            'success': False,
            'error': '调价失败，请检查商品是否存在',
            'details': result.get('details', [])
        }), 400

_PRICE_SYNC_TASKS = {}
_PRICE_SYNC_TASKS_LOCK = threading.Lock()


def _register_price_sync_task(task_no, offer_ids, source, adjust_ratio=1.0):
    if not task_no:
        return
    with _PRICE_SYNC_TASKS_LOCK:
        _PRICE_SYNC_TASKS[task_no] = {
            'offer_ids': [str(x) for x in (offer_ids or []) if x],
            'source': source or 'ALIBABA_1688',
            'adjust_ratio': adjust_ratio,
            'finalized': False,
            'updated_at': int(time.time() * 1000)
        }


def _get_price_sync_task(task_no):
    with _PRICE_SYNC_TASKS_LOCK:
        return dict(_PRICE_SYNC_TASKS.get(task_no) or {})


def _mark_price_sync_task_finalized(task_no):
    with _PRICE_SYNC_TASKS_LOCK:
        task = _PRICE_SYNC_TASKS.get(task_no)
        if task:
            task['finalized'] = True
            task['updated_at'] = int(time.time() * 1000)


@api_bp.route('/api/products/batch/auto-adjust-price', methods=['POST'])
def batch_auto_adjust_price():
    start_time = time.time()
    data = request.get_json() or {}
    offer_ids = data.get('offer_ids', [])
    source = data.get('source', 'ALIBABA_1688')

    if not offer_ids:
        return jsonify({'success': False, 'error': 'offer_ids required'}), 400

    result = product_service.batch_auto_adjust_price_by_sell_price(offer_ids, source=source)
    execution_time = int((time.time() - start_time) * 1000)
    _log_task(
        'POST',
        '/api/products/batch/auto-adjust-price',
        {'offer_ids': offer_ids, 'source': source},
        result,
        bool(result.get('success')),
        '' if result.get('success') else result.get('error', '自动调价失败'),
        execution_time
    )
    status_code = 200 if result.get('success') else 400
    return jsonify(result), status_code


@api_bp.route('/api/products/batch/auto-adjust-price/rollback', methods=['POST'])
def rollback_batch_auto_adjust_price():
    start_time = time.time()
    data = request.get_json(silent=True) or {}
    revision_job_id = str(data.get('revision_job_id') or '').strip()
    source = data.get('source', 'ALIBABA_1688')

    if not revision_job_id:
        return jsonify({'success': False, 'error': 'revision_job_id required'}), 400

    result = product_service.rollback_adjusted_price_revision(revision_job_id, source=source)
    execution_time = int((time.time() - start_time) * 1000)
    _log_task(
        'POST',
        '/api/products/batch/auto-adjust-price/rollback',
        {'revision_job_id': revision_job_id, 'source': source},
        result,
        bool(result.get('success')),
        '' if result.get('success') else result.get('error', '回滚失败'),
        execution_time
    )
    status_code = 200 if result.get('success') else 400
    return jsonify(result), status_code


@api_bp.route('/api/products/batch/sync-price', methods=['POST'])
def batch_sync_price_to_erp():
    """批量调价并同步ERP接口"""
    start_time = time.time()
    data = request.get_json() or {}
    offer_ids = data.get('offer_ids', [])
    source = data.get('source', 'ALIBABA_1688')
    adjust_ratio = data.get('adjust_ratio', 1.0)

    if not offer_ids:
        return jsonify({'success': False, 'error': 'offer_ids required'}), 400

    try:
        adjust_ratio = float(adjust_ratio)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'adjust_ratio格式错误'}), 400

    products = product_service.get_products_for_sync(offer_ids, include_detail=True, source=source)
    if not products:
        return jsonify({'success': False, 'error': '未找到商品数据'}), 400

    print(f"[Sync Price to ERP] Request: offer_ids count={len(offer_ids)}, products_count={len(products)}, adjust_ratio={adjust_ratio}")

    from app.services.erp_sync_service import erp_sync_service

    access_token = get_valid_token()
    if not access_token:
        return jsonify({'success': False, 'code': 'UNAUTHORIZED', 'error': '登录已过期，请重新登录'}), 401

    tenant_id = session.get('tenant_id', '')

    result = erp_sync_service.batch_update_price(
        products,
        adjust_ratio=adjust_ratio,
        access_token=access_token,
        tenant_id=tenant_id
    )

    print(f"[Sync Price to ERP] Result: {json.dumps(result, ensure_ascii=False, cls=DecimalEncoder)}")

    execution_time = int((time.time() - start_time) * 1000)

    if result.get('success'):
        local_update_result = product_service.batch_update_adjusted_price(offer_ids, adjust_ratio, source=source)
        if not local_update_result.get('success'):
            error_message = f"ERP price sync succeeded, but local market price update failed: {local_update_result.get('error', 'unknown error')}"
            logger.error(
                "[Sync Price to ERP] Local market price update failed after ERP success. source=%s offer_count=%s error=%s",
                source,
                len(offer_ids),
                local_update_result.get('error')
            )
            _log_task(
                'POST',
                '/api/products/batch/sync-price',
                {'offer_ids': offer_ids, 'source': source},
                {'erp_result': result, 'local_update_result': local_update_result},
                False,
                error_message,
                execution_time
            )
            return jsonify({
                'success': False,
                'error': error_message,
                'erp_synced': True
            }), 500
        product_service.batch_update_sync_status(offer_ids, 'synced', source=source)
        _log_task('POST', '/api/products/batch/sync-price', {'offer_ids': offer_ids, 'source': source}, result, True, '', execution_time)
        return jsonify({
            'success': True,
            'async_task': False,
            'message': result.get('message', '调价成功'),
            'total': result.get('total', 0),
            'success_count': result.get('success_count', 0)
        })
    else:
        _log_task('POST', '/api/products/batch/sync-price', {'offer_ids': offer_ids, 'source': source}, result, False, result.get('error', '同步失败'), execution_time)
        return jsonify({
            'success': False,
            'error': result.get('error', '同步价格失败'),
            'total': result.get('total', 0)
        }), 400


@api_bp.route('/api/products/batch/fix-adjust-price', methods=['POST'])
def fix_batch_adjust_price():
    """按本地价格映射批量同步已同步商品价格到 ERP"""
    start_time = time.time()
    data = request.get_json() or {}
    source = data.get('source', 'ALIBABA_1688')
    offer_ids = _normalize_offer_ids(data.get('offer_ids', []))

    if not offer_ids:
        return jsonify({'success': False, 'error': 'offer_ids required'}), 400

    access_token = get_valid_token()
    if not access_token:
        return jsonify({'success': False, 'code': 'UNAUTHORIZED', 'error': '登录已过期，请重新登录'}), 401

    tenant_id = session.get('tenant_id', '')
    products = product_service.get_products_for_sync(offer_ids, include_detail=True, source=source)
    product_map = {}
    for product in products or []:
        if not isinstance(product, dict):
            continue
        offer_id = str(product.get('offer_id', '')).strip()
        if offer_id:
            product_map[offer_id] = product

    synced_products = []
    failed_items = []

    for offer_id in offer_ids:
        product = product_map.get(offer_id)
        if not product:
            failed_items.append({'offer_id': offer_id, 'reason': '未找到对应商品'})
            continue
        if str(product.get('sync_status') or '').strip() != 'synced':
            failed_items.append({'offer_id': offer_id, 'reason': '商品不是已同步状态'})
            continue
        synced_products.append(product)

    from app.services.erp_sync_service import erp_sync_service

    success_items = []
    result = {}
    if synced_products:
        result = erp_sync_service.batch_update_price_by_local_mapping(
            synced_products,
            access_token=access_token,
            tenant_id=tenant_id,
            max_retries=3,
            timeout=60
        )
        if result.get('success'):
            task_nos = result.get('task_nos') or []
            task_desc = f"任务号: {', '.join(task_nos)}" if task_nos else 'ERP已受理'
            success_items = [
                {
                    'offer_id': str(product.get('offer_id', '')).strip(),
                    'reason': '',
                    'message': task_desc
                }
                for product in synced_products
            ]
        else:
            failed_reason = result.get('error') or 'ERP调用失败'
            failed_items.extend([
                {
                    'offer_id': str(product.get('offer_id', '')).strip(),
                    'reason': failed_reason
                }
                for product in synced_products
                if str(product.get('offer_id', '')).strip()
            ])

    response_data = {
        'success': True,
        'batch_success': bool(result.get('success')) if synced_products else False,
        'message': result.get('message') or ('ERP批量调价已提交' if success_items else '本批次处理完成'),
        'requested_count': len(offer_ids),
        'matched_count': len(synced_products),
        'success_count': len(success_items),
        'fail_count': len(failed_items),
        'success_items': success_items,
        'failed_items': failed_items,
        'task_nos': result.get('task_nos') or [],
        'spu_count': result.get('spu_count', 0),
        'sku_count': result.get('sku_count', 0)
    }

    execution_time = int((time.time() - start_time) * 1000)
    _log_task(
        'POST',
        '/api/products/batch/fix-adjust-price',
        {'offer_ids': offer_ids, 'source': source},
        response_data,
        len(failed_items) == 0,
        '' if len(failed_items) == 0 else (result.get('error') or '部分记录处理失败'),
        execution_time
    )
    return jsonify(response_data)


@api_bp.route('/api/products/batch/synced-adjusted-price/count', methods=['GET'])
def count_synced_adjusted_price_targets():
    source = request.args.get('source', 'ALIBABA_1688')
    keyword = request.args.get('keyword', '')
    total = synced_adjusted_price_service.count_targets(source=source, keyword=keyword)
    return jsonify({'success': True, 'total': total})


@api_bp.route('/api/products/batch/synced-adjusted-price', methods=['POST'])
def batch_adjust_synced_adjusted_price():
    start_time = time.time()
    data = request.get_json(silent=True) or {}
    source = data.get('source', 'ALIBABA_1688')
    keyword = str(data.get('keyword') or '').strip()
    batch_size = int(data.get('batch_size') or 200)
    task_id = str(data.get('task_id') or uuid4().hex).strip()
    operator = session.get('username', 'unknown')
    ip_address = request.remote_addr or ''
    user_agent = request.headers.get('User-Agent', '')[:500]
    app_obj = current_app._get_current_object()

    access_token = get_valid_token()
    if not access_token:
        return jsonify({'success': False, 'code': 'UNAUTHORIZED', 'error': '登录已过期，请重新登录'}), 401
    tenant_id = session.get('tenant_id', '')

    _set_synced_adjusted_price_progress(task_id, 0, 0, 0, 0, '准备批量调已同步商品运费...')

    def run_batch_task():
        task_started = time.time()

        def progress_callback(current, total, success_count, fail_count, message=''):
            _set_synced_adjusted_price_progress(task_id, current, total, success_count, fail_count, message)

        with app_obj.app_context():
            try:
                result = synced_adjusted_price_service.batch_adjust_synced_adjusted_products(
                    access_token=access_token,
                    tenant_id=tenant_id,
                    source=source,
                    keyword=keyword,
                    batch_size=batch_size,
                    timeout=60,
                    max_retries=3,
                    operator=operator,
                    progress_callback=progress_callback,
                )
                result['task_id'] = task_id
                _set_synced_adjusted_price_progress(
                    task_id,
                    result.get('processed_count', 0),
                    result.get('total', 0),
                    result.get('success_count', 0),
                    result.get('fail_count', 0),
                    result.get('message') or ('处理完成' if result.get('success') else result.get('error', '处理失败')),
                    done=True,
                    result=result,
                    error='' if result.get('success') or result.get('partial_success') or result.get('empty') else result.get('error', '处理失败')
                )
                execution_time = int((time.time() - task_started) * 1000)
                task_log_service.log(
                    username=operator,
                    request_method='POST',
                    request_path='/api/products/batch/synced-adjusted-price',
                    request_params={
                        'source': source,
                        'keyword': keyword,
                        'batch_size': batch_size,
                        'task_id': task_id,
                        'operator': operator,
                    },
                    response_data={
                        'job_id': result.get('job_id'),
                        'backup_job_id': result.get('backup_job_id'),
                        'total': result.get('total'),
                        'processed_count': result.get('processed_count'),
                        'success_count': result.get('success_count'),
                        'fail_count': result.get('fail_count'),
                        'batch_count': result.get('batch_count'),
                        'failed_batch_count': result.get('failed_batch_count'),
                        'retry_count': result.get('retry_count'),
                        'success_log_id': result.get('success_log_id'),
                        'failure_log_id': result.get('failure_log_id'),
                        'success_details': result.get('success_details', [])[:10],
                        'failed_details': result.get('failed_details', [])[:10],
                    },
                    success=bool(result.get('success')) or bool(result.get('partial_success')) or bool(result.get('empty')),
                    error_message='' if (result.get('success') or result.get('partial_success') or result.get('empty')) else result.get('error', '处理失败'),
                    ip_address=ip_address,
                    user_agent=user_agent,
                    execution_time=execution_time
                )
            except Exception as exc:
                logger.exception('[Synced Adjusted Price] background task failed')
                result = {
                    'success': False,
                    'task_id': task_id,
                    'error': str(exc),
                    'total': 0,
                    'processed_count': 0,
                    'success_count': 0,
                    'fail_count': 0,
                    'success_details': [],
                    'failed_details': [],
                }
                _set_synced_adjusted_price_progress(task_id, 0, 0, 0, 0, str(exc), done=True, result=result, error=str(exc))

    thread = threading.Thread(target=run_batch_task, daemon=True)
    thread.start()

    return jsonify({
        'success': True,
        'async_task': True,
        'task_id': task_id,
        'message': '批量调已同步商品运费任务已开始',
        'accepted_time_ms': int((time.time() - start_time) * 1000)
    }), 202


@api_bp.route('/api/products/batch/synced-adjusted-price/progress/<task_id>', methods=['GET'])
def get_synced_adjusted_price_progress(task_id):
    task_id = str(task_id or '').strip()
    if not task_id:
        return jsonify({'success': False, 'error': 'task_id required'}), 400
    with _SYNCED_ADJUSTED_PRICE_PROGRESS_LOCK:
        progress_data = dict(_SYNCED_ADJUSTED_PRICE_PROGRESS.get(task_id) or {})
    if not progress_data:
        return jsonify({'success': False, 'error': 'task not found'}), 404
    return jsonify(progress_data)


@api_bp.route('/api/products/batch/synced-adjusted-price/success-log/<log_id>', methods=['GET'])
def download_synced_adjusted_price_success_log(log_id):
    content = synced_adjusted_price_service.export_success_log_csv(log_id)
    if content is None:
        return jsonify({'success': False, 'error': 'success log not found'}), 404
    return Response(
        content,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=synced_adjusted_price_successes_{log_id}.csv'}
    )


@api_bp.route('/api/products/batch/synced-adjusted-price/failure-log/<log_id>', methods=['GET'])
def download_synced_adjusted_price_failure_log(log_id):
    content = synced_adjusted_price_service.export_failure_log_csv(log_id)
    if content is None:
        return jsonify({'success': False, 'error': 'failure log not found'}), 404
    return Response(
        content,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=synced_adjusted_price_failures_{log_id}.csv'}
    )


@api_bp.route('/api/products/batch/sync-price/progress', methods=['GET'])
def sync_price_progress():
    """查询价格同步任务进度"""
    start_time = time.time()
    task_no = (request.args.get('taskNo') or '').strip()
    source = request.args.get('source', 'ALIBABA_1688')

    if not task_no:
        return jsonify({'success': False, 'error': 'taskNo required'}), 400

    access_token = get_valid_token()
    if not access_token:
        return jsonify({'success': False, 'code': 'UNAUTHORIZED', 'error': '登录已过期，请重新登录'}), 401

    tenant_id = session.get('tenant_id', '')

    from app.services.erp_sync_service import erp_sync_service
    progress_result = erp_sync_service.query_batch_create_progress(task_no, access_token=access_token, tenant_id=tenant_id)

    if not progress_result.get('success'):
        execution_time = int((time.time() - start_time) * 1000)
        _log_task('GET', '/api/products/batch/sync-price/progress', {'taskNo': task_no, 'source': source}, progress_result, False, progress_result.get('error', '查询失败'), execution_time)
        return jsonify(progress_result), 400

    task_context = _get_price_sync_task(task_no)
    task_status = progress_result.get('task_status')

    if task_context and not task_context.get('finalized'):
        offer_ids = task_context.get('offer_ids') or []
        adjust_ratio = task_context.get('adjust_ratio', 1.0)
        failed_details = progress_result.get('failed_details') or []
        success_details = progress_result.get('success_details') or []
        success_offer_ids = _extract_success_offer_ids_by_details(success_details, offer_ids)

        if success_offer_ids:
            product_service.batch_update_adjusted_price(success_offer_ids, adjust_ratio, source=source)
            product_service.batch_update_sync_status(success_offer_ids, 'synced', source=source)

        if task_status in (2, 3, 4):
            failed_offer_ids = set(_extract_failed_offer_ids_by_details(failed_details, offer_ids))
            finalized_success_offer_ids = [oid for oid in offer_ids if oid not in failed_offer_ids]

            if task_status in (2, 3) and finalized_success_offer_ids:
                product_service.batch_update_adjusted_price(finalized_success_offer_ids, adjust_ratio, source=source)
                product_service.batch_update_sync_status(finalized_success_offer_ids, 'synced', source=source)

            if failed_offer_ids:
                first_reason = ''
                for item in failed_details:
                    if isinstance(item, dict) and item.get('reason'):
                        first_reason = str(item.get('reason'))
                        break
                product_service.batch_update_sync_status(list(failed_offer_ids), 'failed', first_reason or progress_result.get('task_status_desc', '同步失败'), source=source)

            if task_status == 4 and offer_ids:
                fail_reason = ''
                for item in failed_details:
                    if isinstance(item, dict) and item.get('reason'):
                        fail_reason = str(item.get('reason'))
                        break
                product_service.batch_update_sync_status(offer_ids, 'failed', fail_reason or progress_result.get('task_status_desc', '同步失败'), source=source)

            _mark_price_sync_task_finalized(task_no)

    execution_time = int((time.time() - start_time) * 1000)
    _log_task('GET', '/api/products/batch/sync-price/progress', {'taskNo': task_no, 'source': source}, progress_result, True, '', execution_time)
    return jsonify(progress_result)


@api_bp.route('/api/products/batch/erp-update-stock', methods=['PUT'])
def batch_erp_update_stock():
    """ERP批量更新库存接口 - 调用ERP接口更新SKU库存"""
    start_time = time.time()
    data = request.get_json() or {}
    offer_ids = data.get('offer_ids', [])
    stock_value = data.get('stock', 200)  # 默认库存200
    source = data.get('source', 'ALIBABA_1688')
    
    if not offer_ids:
        return jsonify({'success': False, 'error': 'offer_ids required'}), 400
    
    # 获取商品数据
    products = product_service.get_products_for_sync(offer_ids, include_detail=True, source=source)
    
    if not products:
        return jsonify({'success': False, 'error': '未找到商品数据'}), 400
    
    print(f"[ERP Batch Update Stock] Request: offer_ids={offer_ids}, stock={stock_value}, products_count={len(products)}")
    
    # 调用ERP批量更新库存服务
    from app.services.erp_sync_service import erp_sync_service
    
    # 获取有效token（自动刷新过期token）
    access_token = get_valid_token()
    if not access_token:
        return jsonify({'success': False, 'code': 'UNAUTHORIZED', 'error': '登录已过期，请重新登录'}), 401
    
    tenant_id = session.get('tenant_id', '')
    
    result = erp_sync_service.batch_update_stock(products, stock_value=stock_value, access_token=access_token, tenant_id=tenant_id)
    
    print(f"[ERP Batch Update Stock] Result: {json.dumps(result, ensure_ascii=False, cls=DecimalEncoder)}")
    
    execution_time = int((time.time() - start_time) * 1000)
    
    if result.get('success'):
        # 更新本地数据库中的库存
        for offer_id in offer_ids:
            product_service.update_stock(offer_id, stock_value, source=source)
        
        _log_task('PUT', '/api/products/batch/erp-update-stock', data, result, True, '', execution_time)
        return jsonify({
            'success': True,
            'message': result.get('message', '库存更新成功'),
            'total': result.get('total', 0),
            'success_count': result.get('success_count', 0)
        })
    else:
        _log_task('PUT', '/api/products/batch/erp-update-stock', data, result, False, result.get('error', '库存更新失败'), execution_time)
        return jsonify({
            'success': False,
            'error': result.get('error', '库存更新失败'),
            'total': result.get('total', 0)
        }), 400

@api_bp.route('/api/products/export', methods=['POST'])
def export_products():
    start_time = time.time()
    data = request.get_json() or {}
    offer_ids = data.get('offer_ids')
    format_type = data.get('format', 'json')
    source = data.get('source', 'ALIBABA_1688')
    
    content, mime_type, filename = product_service.export_products(offer_ids, format_type, source=source)
    
    execution_time = int((time.time() - start_time) * 1000)
    _log_task('POST', '/api/products/export', {'offer_ids': offer_ids, 'format': format_type, 'source': source}, {'success': True, 'filename': filename}, True, '', execution_time)
    
    response = Response(
        content,
        mimetype=mime_type,
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )
    return response

@api_bp.route('/api/products/import/template', methods=['GET'])
def download_import_template():
    """下载商品导入模板。"""
    start_time = time.time()
    template_path = os.path.abspath(os.path.join(current_app.root_path, '..', 'mb.xlsx'))
    request_path = '/api/products/import/template'

    if not os.path.isfile(template_path):
        execution_time = int((time.time() - start_time) * 1000)
        result = {'success': False, 'error': '导入模板文件不存在'}
        _log_task('GET', request_path, {}, result, False, result['error'], execution_time)
        return jsonify(result), 404

    try:
        response = send_file(
            template_path,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='products_import_template.xlsx',
            max_age=0,
        )
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        execution_time = int((time.time() - start_time) * 1000)
        _log_task('GET', request_path, {}, {'success': True, 'filename': 'products_import_template.xlsx'}, True, '', execution_time)
        return response
    except Exception as e:
        logger.exception("[Import Template] Download failed: %s", str(e))
        execution_time = int((time.time() - start_time) * 1000)
        result = {'success': False, 'error': f'模板下载失败: {str(e)}'}
        _log_task('GET', request_path, {}, result, False, result['error'], execution_time)
        return jsonify(result), 500

@api_bp.route('/api/products/import', methods=['POST'])
def import_products():
    """导入商品 - 使用优化后的批量导入服务"""
    start_time = time.time()
    temp_path = ''
    upload_file = request.files.get('file')
    if not upload_file:
        return jsonify({'success': False, 'error': '请上传Excel文件'}), 400
    filename = (upload_file.filename or '').lower()
    if not filename.endswith('.xlsx'):
        return jsonify({'success': False, 'error': '仅支持.xlsx文件'}), 400
    import_task_id = (request.form.get('import_task_id') or '').strip()
    if not import_task_id:
        import_task_id = f"task_{int(time.time() * 1000)}_{threading.get_ident()}"
    _set_import_progress(import_task_id, 0, '正在读取Excel文件...')
    try:
        def progress_callback(current, total, message=''):
            percent = 0
            try:
                if total:
                    percent = int((float(current) / float(total)) * 100)
                else:
                    percent = int(current)
            except Exception:
                percent = int(current) if isinstance(current, (int, float)) else 0
            _set_import_progress(import_task_id, percent, message or '正在处理...')

        # 使用优化后的导入服务
        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as temp_file:
            upload_file.stream.seek(0)
            temp_file.write(upload_file.stream.read())
            temp_path = temp_file.name

        result = product_import_job_service.run_import_from_excel(
            temp_path,
            import_task_id=import_task_id,
            progress_callback=progress_callback
        )
        _set_import_progress(import_task_id, 100, '导入完成！')
        print(f"[Products Import] Result: {json.dumps(result, ensure_ascii=False, cls=DecimalEncoder)}")
        execution_time = int((time.time() - start_time) * 1000)
        error_msg = ''
        if not result.get('success'):
            if result.get('fail_count', 0) > 0:
                failed_brief = '; '.join([f"row={d.get('row')}:{d.get('reason','')}" for d in (result.get('failed_details') or [])[:5]])
                error_msg = f"fail_count={result.get('fail_count')}, details: {failed_brief}"
            elif result.get('excluded_count', 0) > 0:
                error_msg = f"excluded_count={result.get('excluded_count')} (not a real failure)"
        _log_task('POST', '/api/products/import', {'filename': filename}, result, result.get('success', False), error_msg, execution_time)
        return jsonify({**result, 'import_task_id': import_task_id})
    except Exception as e:
        import traceback
        tb_str = traceback.format_exc()
        logger.error("[Products Import] Exception: %s\n%s", str(e), tb_str)
        _set_import_progress(import_task_id, 100, f'导入失败: {str(e)}')
        execution_time = int((time.time() - start_time) * 1000)
        error_detail = str(e)
        error_type = type(e).__name__
        _log_task('POST', '/api/products/import', {'filename': filename}, {'success': False}, False, f'[{error_type}] {error_detail}', execution_time)
        return jsonify({
            'success': False,
            'error': f'导入失败: {error_detail}',
            'error_type': error_type,
            'detail': tb_str,
        })

    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

@api_bp.route('/api/products/import/progress', methods=['GET'])
def get_import_progress():
    task_id = (request.args.get('task_id') or '').strip()
    if not task_id:
        return jsonify({'success': False, 'error': 'task_id required'}), 400
    with _IMPORT_PROGRESS_LOCK:
        progress_data = _IMPORT_PROGRESS.get(task_id)
    if not progress_data:
        return jsonify({'success': False, 'error': 'task not found'}), 404
    return jsonify({'success': True, 'task_id': task_id, **progress_data})


@api_bp.route('/api/products/import/exclusion-reports/<report_id>', methods=['GET'])
def get_import_exclusion_report(report_id):
    summary = import_exclusion_report_service.get_report_summary(report_id)
    if not summary:
        return jsonify({'success': False, 'error': 'report not found'}), 404
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('page_size', 200))
    items_result = import_exclusion_report_service.list_report_items(report_id, page=page, page_size=page_size)
    return jsonify({
        'success': True,
        'summary': summary,
        **items_result
    })


@api_bp.route('/api/products/import/exclusion-reports/<report_id>/row-numbers', methods=['GET'])
def get_import_exclusion_report_row_numbers(report_id):
    summary = import_exclusion_report_service.get_report_summary(report_id)
    if not summary:
        return jsonify({'success': False, 'error': 'report not found'}), 404
    return jsonify({
        'success': True,
        'row_numbers': import_exclusion_report_service.get_row_numbers(report_id)
    })


@api_bp.route('/api/products/import/exclusion-reports/<report_id>/export', methods=['GET'])
def export_import_exclusion_report(report_id):
    summary = import_exclusion_report_service.get_report_summary(report_id)
    if not summary:
        return jsonify({'success': False, 'error': 'report not found'}), 404
    content = import_exclusion_report_service.export_csv(report_id)
    filename = f'import_exclusion_report_{report_id}.csv'
    return Response(
        content,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


@api_bp.route('/api/products/import/price-revision/rollback', methods=['POST'])
def rollback_import_price_revision():
    start_time = time.time()
    data = request.get_json(silent=True) or {}
    revision_job_id = str(data.get('revision_job_id') or '').strip()
    if not revision_job_id:
        return jsonify({'success': False, 'error': 'revision_job_id required'}), 400

    result = product_import_service.rollback_price_revision(revision_job_id)
    execution_time = int((time.time() - start_time) * 1000)
    _log_task(
        'POST',
        '/api/products/import/price-revision/rollback',
        {'revision_job_id': revision_job_id},
        result,
        bool(result.get('success')),
        '' if result.get('success') else result.get('error', '回滚失败'),
        execution_time
    )
    status_code = 200 if result.get('success') else 400
    return jsonify(result), status_code

@api_bp.route('/api/products/<offer_id>/detail', methods=['GET'])
def get_product_detail(offer_id):
    source = request.args.get('source', 'ALIBABA_1688')
    product = product_service.get_product(offer_id, source=source)
    if not product:
        return jsonify({'success': False, 'error': 'Product not found'}), 404
    
    if not _has_import_detail_data(product):
        result = _fetch_detail_by_source(offer_id, source=source)
        if result.get('success') and result.get('detail'):
            product_service.update_product_detail(offer_id, result['detail'], source=source)
            product = product_service.get_product(offer_id, source=source)
    
    return jsonify({'success': True, 'product': product})

@api_bp.route('/api/products/<offer_id>/refresh', methods=['POST'])
def refresh_product_detail(offer_id):
    data = request.get_json(silent=True) or {}
    source = data.get('source', request.args.get('source', 'ALIBABA_1688'))
    result = _fetch_detail_by_source(offer_id, source=source)
    if result.get('success') and result.get('detail'):
        product_service.update_product_detail(offer_id, result['detail'], source=source)
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': result.get('error', 'Failed to fetch detail')})

@api_bp.route('/api/products/sync', methods=['POST'])
def sync_to_erp():
    start_time = time.time()
    data = request.get_json() or {}
    offer_ids = data.get('offer_ids', [])
    source = data.get('source', 'ALIBABA_1688')
    print(f"[Sync to ERP] Request: offer_ids={offer_ids}, source={source}")
    if str(source).lower() == 'df':
        return jsonify({'success': False, 'error': '代发商品暂不支持同步到ERP'}), 400
    
    if not offer_ids:
        return jsonify({'success': False, 'error': 'offer_ids required'}), 400
    
    products = product_service.get_products_for_sync(offer_ids, include_detail=True, source=source)
    print(f"[Sync to ERP] Products count: {len(products)}")

    top_level_block_result = _build_top_level_category_block_result(products)
    if top_level_block_result:
        execution_time = int((time.time() - start_time) * 1000)
        _log_task(
            'POST',
            '/api/products/sync',
            {'offer_ids': offer_ids, 'source': source},
            top_level_block_result,
            False,
            TOP_LEVEL_CATEGORY_NOT_ALLOWED_MESSAGE,
            execution_time
        )
        return jsonify(top_level_block_result), 400
    
    # 直接调用 ERP 同步接口，不再检查 description 字段
    from app.services.erp_sync_service import erp_sync_service
    
    # 获取有效token（自动刷新过期token）
    access_token = get_valid_token()
    if not access_token:
        return jsonify({'success': False, 'code': 'UNAUTHORIZED', 'error': '登录已过期，请重新登录'}), 401
    
    tenant_id = session.get('tenant_id', '')
    print(f"[Sync to ERP] Calling erp_sync_service.sync_products...")
    result = erp_sync_service.sync_products(products, access_token=access_token, tenant_id=tenant_id)
    result_str = json.dumps(result, ensure_ascii=False, cls=DecimalEncoder)
    print(f"[Sync to ERP] Response: {result_str}")
    print(f"[Sync to ERP] Success: {result.get('success')}, Error: {result.get('error')}")
    
    execution_time = int((time.time() - start_time) * 1000)
    failed_details = result.get('failed_details') or []
    result['failure_summary'] = _build_sync_failure_summary(failed_details, result.get('error', ''))
    
    if result.get('success') and result.get('async_task'):
        failed_offer_ids = _update_failed_sync_status_by_details(
            failed_details,
            offer_ids,
            fallback_reason=result.get('error') or '任务提交失败',
            source=source
        )
        task_offer_ids = result.get('task_offer_ids') or offer_ids
        immediate_failed_offer_ids = [oid for oid in failed_offer_ids if str(oid) in set(str(x) for x in (task_offer_ids or []))]
        _register_erp_sync_task(
            result.get('task_no'),
            task_offer_ids,
            source,
            immediate_failed_offer_ids=immediate_failed_offer_ids
        )
        _log_task('POST', '/api/products/sync', {'offer_ids': offer_ids, 'source': source}, result, True, '', execution_time)
    elif result.get('success'):
        failed_offer_ids = _extract_failed_offer_ids_by_details(failed_details, offer_ids)
        success_offer_ids = [oid for oid in offer_ids if str(oid) not in set(str(x) for x in failed_offer_ids)]
        if success_offer_ids:
            product_service.batch_update_sync_status(success_offer_ids, 'synced', source=source)
        if failed_details:
            _update_failed_sync_status_by_details(
                failed_details,
                offer_ids,
                fallback_reason=result.get('error') or '同步失败',
                source=source
            )
        _log_task('POST', '/api/products/sync', {'offer_ids': offer_ids, 'source': source}, 
                  result, True, '', execution_time)
    elif result.get('partial_success'):
        failed_offer_ids = _extract_failed_offer_ids_by_details(failed_details, offer_ids)
        success_offer_ids = [str(oid) for oid in offer_ids if str(oid) not in set(str(x) for x in failed_offer_ids)]
        if success_offer_ids:
            product_service.batch_update_sync_status(success_offer_ids, 'synced', source=source)
        if failed_offer_ids:
            _update_failed_sync_status_by_details(
                failed_details,
                offer_ids,
                fallback_reason=result.get('error') or '同步失败',
                source=source
            )
        result['success'] = True
        _log_task('POST', '/api/products/sync', {'offer_ids': offer_ids, 'source': source}, 
                  result, True, '', execution_time)
    else:
        product_service.batch_update_sync_status(offer_ids, 'failed', result.get('error'), source=source)
        _log_task('POST', '/api/products/sync', {'offer_ids': offer_ids, 'source': source}, 
                  result, False, result.get('error', '同步失败'), execution_time)
    
    return jsonify(result)

@api_bp.route('/api/products/sync/progress', methods=['GET'])
def sync_to_erp_progress():
    start_time = time.time()
    task_no = (request.args.get('taskNo') or '').strip()
    source = request.args.get('source', 'ALIBABA_1688')
    if not task_no:
        return jsonify({'success': False, 'error': 'taskNo required'}), 400
    access_token = get_valid_token()
    if not access_token:
        return jsonify({'success': False, 'code': 'UNAUTHORIZED', 'error': '登录已过期，请重新登录'}), 401
    tenant_id = session.get('tenant_id', '')
    from app.services.erp_sync_service import erp_sync_service
    progress_result = erp_sync_service.query_batch_create_progress(task_no, access_token=access_token, tenant_id=tenant_id)
    if not progress_result.get('success'):
        execution_time = int((time.time() - start_time) * 1000)
        _log_task('GET', '/api/products/sync/progress', {'taskNo': task_no, 'source': source}, progress_result, False, progress_result.get('error', '查询失败'), execution_time)
        return jsonify(progress_result), 400

    task_context = _get_erp_sync_task(task_no)
    task_status = progress_result.get('task_status')
    success_details = progress_result.get('success_details') or []
    failed_details = progress_result.get('failed_details') or []
    progress_result['failure_summary'] = _build_sync_failure_summary(
        failed_details,
        progress_result.get('task_status_desc', '')
    )

    if task_status in (2, 3, 4):
        if task_context:
            try:
                apply_task_progress_to_local(
                    task_context=task_context,
                    progress_result=progress_result,
                    source=source,
                    persist_finalized=True,
                )
            except Exception as exc:
                logger.exception('apply_task_progress_to_local failed task_no=%s: %s', task_no, exc)
        else:
            print(f"[Sync Progress] Task context not found for task_no={task_no}, using success_details to update status")
            if success_details and task_status in (2, 3):
                success_spu_ids = [str(item.get('spuId', '')) for item in success_details if item.get('spuId')]
                if success_spu_ids:
                    product_service.batch_update_sync_status(success_spu_ids, 'synced', source=source)
                    print(f"[Sync Progress] Updated {len(success_spu_ids)} products to synced status from success_details")

            if failed_details and task_status in (3, 4):
                failed_spu_reason_map = {
                    str(item.get('spuId')): str(item.get('reason') or progress_result.get('task_status_desc', '同步失败'))
                    for item in failed_details
                    if item.get('spuId')
                }
                if failed_spu_reason_map:
                    product_service.batch_update_sync_status_with_reasons(
                        failed_spu_reason_map,
                        'failed',
                        source=source
                    )
                    print(f"[Sync Progress] Updated {len(failed_spu_reason_map)} products to failed status from failed_details")

    execution_time = int((time.time() - start_time) * 1000)
    _log_task('GET', '/api/products/sync/progress', {'taskNo': task_no, 'source': source}, progress_result, True, '', execution_time)
    return jsonify(progress_result)


@api_bp.route('/api/products/sync/reconcile', methods=['POST'])
def reconcile_sync_status():
    """用户点击"刷新同步状态"时触发：根据 taskNo 或 offer_ids 主动拉取 ERP 任务进度，
    将仍处于 pending 的记录推进到 synced/failed。"""
    start_time = time.time()
    data = request.get_json(silent=True) or {}
    raw_task_no = str(data.get('taskNo') or data.get('task_no') or '').strip()
    raw_offer_ids = data.get('offer_ids') or []
    if isinstance(raw_offer_ids, str):
        raw_offer_ids = [raw_offer_ids]
    offer_ids = [str(x).strip() for x in raw_offer_ids if str(x).strip()]
    source = str(data.get('source') or 'ALIBABA_1688').strip() or 'ALIBABA_1688'

    if not raw_task_no and not offer_ids:
        return jsonify({'success': False, 'error': '缺少 taskNo 或 offer_ids'}), 400

    access_token = get_valid_token()
    if not access_token:
        return jsonify({'success': False, 'code': 'UNAUTHORIZED', 'error': '登录已过期，请重新登录'}), 401
    tenant_id = session.get('tenant_id', '')

    tasks_to_reconcile = []
    if raw_task_no:
        task = erp_sync_task_store.get_task(raw_task_no)
        if task:
            tasks_to_reconcile.append(task)
    if offer_ids:
        candidate_tasks = erp_sync_task_store.find_tasks_by_offer_ids(
            offer_ids, source=source, include_finalized=False
        )
        seen = set(t.get('task_no') for t in tasks_to_reconcile)
        for task in candidate_tasks:
            tno = task.get('task_no')
            if tno and tno not in seen:
                tasks_to_reconcile.append(task)
                seen.add(tno)

    if not tasks_to_reconcile:
        response = {
            'success': True,
            'reconciled_tasks': [],
            'message': '没有待对账的任务（可能已全部落定或超过保留期）',
        }
        _log_task('POST', '/api/products/sync/reconcile', data, response, True, '', int((time.time() - start_time) * 1000))
        return jsonify(response)

    from app.services.erp_sync_service import erp_sync_service
    reconciled = []
    for task in tasks_to_reconcile:
        task_no = task.get('task_no')
        progress_result = erp_sync_service.query_batch_create_progress(
            task_no, access_token=access_token, tenant_id=tenant_id
        )
        if not progress_result.get('success'):
            reconciled.append({
                'task_no': task_no,
                'success': False,
                'error': progress_result.get('error') or '查询任务进度失败',
            })
            erp_sync_task_store.update_reconcile_meta(
                task_no,
                reconcile_error=str(progress_result.get('error') or '查询任务进度失败')[:500],
            )
            continue
        try:
            apply_result = apply_task_progress_to_local(
                task_context=task,
                progress_result=progress_result,
                source=source,
                persist_finalized=True,
            )
        except Exception as exc:
            logger.exception('reconcile apply failed task_no=%s: %s', task_no, exc)
            reconciled.append({'task_no': task_no, 'success': False, 'error': str(exc)})
            continue
        reconciled.append({
            'task_no': task_no,
            'success': True,
            'finalized': apply_result.get('finalized'),
            'task_status': apply_result.get('task_status'),
            'task_status_desc': apply_result.get('task_status_desc'),
            'success_offer_ids': apply_result.get('success_offer_ids') or [],
            'failed_offer_ids': apply_result.get('failed_offer_ids') or [],
        })

    any_finalized = any(item.get('finalized') for item in reconciled)
    response = {
        'success': True,
        'any_finalized': any_finalized,
        'reconciled_tasks': reconciled,
    }
    execution_time = int((time.time() - start_time) * 1000)
    _log_task('POST', '/api/products/sync/reconcile', data, response, True, '', execution_time)
    return jsonify(response)


@api_bp.route('/api/products/sync/preview', methods=['POST'])
def preview_sync_data():
    data = request.get_json() or {}
    offer_ids = data.get('offer_ids', [])
    source = data.get('source', 'ALIBABA_1688')
    print(f"[Sync Preview] Request: offer_ids={offer_ids}, source={source}")
    if str(source).lower() == 'df':
        return jsonify({'success': False, 'error': '代发商品暂不支持同步预览'}), 400
    
    if not offer_ids:
        return jsonify({'success': False, 'error': 'offer_ids required'}), 400
    
    products = product_service.get_products_for_sync(offer_ids, include_detail=True, source=source)
    print(f"[Sync Preview] Products count: {len(products)}")

    top_level_block_result = _build_top_level_category_block_result(products)
    if top_level_block_result:
        return jsonify(top_level_block_result), 400
    
    sync_data = []
    for p in products:
        sync_data.append({
            'offer_id': p['offer_id'],
            'title': p['title'],
            'price': p['price'],
            'cost_price': p['cost_price'],
            'sell_price': p['sell_price'],
            'image_url': p['image_url'],
            'images': p.get('images', []),
            'supplier_name': p['supplier_name'],
            'sku_info': p.get('sku_info', []),
            'sku_count': p.get('sku_count', 0),
            'attributes': p.get('attributes', []),
            'description': p.get('description', ''),
            'detail_url': p['detail_url'],
            'sync_status': p.get('sync_status', 'pending')
        })

    from app.services.erp_sync_service import erp_sync_service
    payload_preview_data = erp_sync_service.build_payload_preview(products, max_items=2)
    
    response_data = {
        'success': True,
        'products': sync_data,
        'total': len(sync_data),
        'payload_total': payload_preview_data.get('payload_total', 0),
        'payload_preview': payload_preview_data.get('payload_preview', []),
        'payload_preview_errors': payload_preview_data.get('errors', [])
    }
    print(f"[Sync Preview] Response: {json.dumps(response_data, ensure_ascii=False, cls=DecimalEncoder)[:500]}...")
    return jsonify(response_data)

@api_bp.route('/api/products/batch/fetch-detail', methods=['POST'])
def batch_fetch_detail():
    data = request.get_json() or {}
    offer_ids = data.get('offer_ids', [])
    source = data.get('source', 'ALIBABA_1688')
    
    if not offer_ids:
        return jsonify({'success': False, 'error': 'offer_ids required'}), 400
    
    thread = threading.Thread(
        target=fetch_details_async,
        args=(offer_ids, source)
    )
    thread.daemon = True
    thread.start()
    
    return jsonify({
        'success': True,
        'message': f'正在获取 {len(offer_ids)} 个商品详情'
    })

@api_bp.route('/api/products/batch/refresh', methods=['POST'])
def batch_refresh():
    data = request.get_json() or {}
    offer_ids = data.get('offer_ids', [])
    source = data.get('source', 'ALIBABA_1688')
    
    if not offer_ids:
        return jsonify({'success': False, 'error': 'offer_ids required'}), 400
    
    thread = threading.Thread(
        target=refresh_products_async,
        args=(offer_ids, source)
    )
    thread.daemon = True
    thread.start()
    
    return jsonify({
        'success': True,
        'message': f'正在刷新 {len(offer_ids)} 个商品'
    })

def refresh_products_async(offer_ids, source='ALIBABA_1688'):
    for offer_id in offer_ids:
        try:
            result = _fetch_detail_by_source(offer_id, source=source)
            if result.get('success') and result.get('detail'):
                product_service.update_product_detail(offer_id, result['detail'], source=source)
        except Exception as e:
            print(f"Failed to refresh product {offer_id}: {e}")

def fetch_details_async(offer_ids, source='ALIBABA_1688'):
    for offer_id in offer_ids:
        try:
            result = _fetch_detail_by_source(offer_id, source=source)
            if result.get('success') and result.get('detail'):
                product_service.update_product_detail(offer_id, result['detail'], source=source)
        except Exception as e:
            print(f"Failed to fetch detail for {offer_id}: {e}")

def fetch_details_and_sync(products, source='ALIBABA_1688'):
    for p in products:
        try:
            result = _fetch_detail_by_source(p['offer_id'], source=source)
            if result.get('success') and result.get('detail'):
                product_service.update_product_detail(p['offer_id'], result['detail'], source=source)
        except Exception as e:
            print(f"Failed to fetch detail for {p['offer_id']}: {e}")

@api_bp.route('/api/search', methods=['GET', 'POST'])
def search():
    if request.method == 'POST':
        data = request.get_json() or {}
        keyword = data.get('keyword', '').strip() or None  # 空字符串转为None，支持无关键词搜索
        quantity = data.get('quantity', 5000)
        price_min = data.get('price_min')
        price_max = data.get('price_max')
    else:
        keyword = request.args.get('keyword', '').strip() or None
        quantity = int(request.args.get('quantity', 5000))
        price_min = request.args.get('price_min')
        price_max = request.args.get('price_max')
    
    # keyword现在是可选的，可以为None
    
    params = {}
    if price_min:
        params['price_min'] = float(price_min)
    if price_max:
        params['price_max'] = float(price_max)
    
    from app.services.selection_service import selection_service
    task_id = selection_service.create_task(keyword, quantity, params if params else None)
    result = selection_service.execute_search(task_id)
    
    return jsonify({
        'success': result['success'],
        'task_id': task_id,
        'total': result.get('total', 0),
        'selection_url': selection_service.get_selection_url(task_id),
        'debug_products': result.get('products', [])[:2] if result.get('products') else []
    })


_image_cache = {}
_image_cache_lock = threading.Lock()


def _get_cached_image(url):
    with _image_cache_lock:
        return _image_cache.get(url)


def _set_cached_image(url, content):
    with _image_cache_lock:
        _image_cache[url] = content
        if len(_image_cache) > 500:
            keys_to_remove = list(_image_cache.keys())[:100]
            for key in keys_to_remove:
                del _image_cache[key]


@api_bp.route('/api/proxy/image')
def proxy_image():
    """图片代理接口 - 解决1688图片防盗链问题，带缓存"""
    image_url = request.args.get('url')
    if not image_url:
        return jsonify({'success': False, 'error': 'url parameter required'}), 400

    cached = _get_cached_image(image_url)
    if cached:
        return Response(
            cached['content'],
            mimetype=cached['content_type'],
            headers={
                'Cache-Control': 'public, max-age=86400',
                'Access-Control-Allow-Origin': '*',
                'X-Image-Cache': 'HIT'
            }
        )

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://detail.1688.com/',
            'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8'
        }

        resp = requests.get(image_url, headers=headers, timeout=10, stream=True)

        if resp.status_code == 200:
            content = resp.content
            content_type = resp.headers.get('Content-Type', 'image/jpeg')

            _set_cached_image(image_url, {'content': content, 'content_type': content_type})

            return Response(
                content,
                mimetype=content_type,
                headers={
                    'Cache-Control': 'public, max-age=86400',
                    'Access-Control-Allow-Origin': '*',
                    'X-Image-Cache': 'MISS'
                }
            )
        else:
            return jsonify({'success': False, 'error': f'Failed to fetch image: {resp.status_code}'}), 502
    except Exception as e:
        print(f"Image proxy error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@api_bp.route('/api/erp/test', methods=['GET', 'POST'])
def test_erp_connection():
    from app.services.erp_sync_service import erp_sync_service
    result = erp_sync_service.test_connection()
    return jsonify(result)

# 同步全部商品到ERP
@api_bp.route('/api/products/sync-all', methods=['POST'])
def sync_all_products_to_erp():
    start_time = time.time()
    data = request.get_json() or {}
    source = data.get('source', 'ALIBABA_1688')
    
    # 获取有效token（自动刷新过期token）
    access_token = get_valid_token()
    if not access_token:
        return jsonify({'success': False, 'code': 'UNAUTHORIZED', 'error': '登录已过期，请重新登录'}), 401
    
    tenant_id = session.get('tenant_id', '')
    
    # 获取所有待同步的商品
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT offer_id FROM import_product 
        WHERE source_type = %s AND sync_status = %s
    ''', (source, 'pending'))
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        return jsonify({
            'success': True,
            'message': '没有待同步的商品',
            'data': {
                'syncedCount': 0,
                'failedCount': 0,
                'failedIds': []
            }
        })
    
    offer_ids = [row['offer_id'] for row in rows]
    print(f"[Sync All Products] 同步全部商品到ERP, source={source}, count={len(offer_ids)}")
    
    # 获取商品详情
    products = product_service.get_products_for_sync(offer_ids, include_detail=True, source=source)
    
    # 调用ERP同步服务
    from app.services.erp_sync_service import erp_sync_service
    result = erp_sync_service.sync_products(products, access_token=access_token, tenant_id=tenant_id)
    
    execution_time = int((time.time() - start_time) * 1000)
    failed_details = result.get('failed_details') or []
    result['failure_summary'] = _build_sync_failure_summary(failed_details, result.get('error', ''))
    
    if result.get('success') and result.get('async_task'):
        failed_offer_ids = _update_failed_sync_status_by_details(
            failed_details,
            offer_ids,
            fallback_reason=result.get('error') or '任务提交失败',
            source=source
        )
        task_offer_ids = result.get('task_offer_ids') or offer_ids
        task_offer_id_set = set(str(x) for x in (task_offer_ids or []))
        immediate_failed_offer_ids = [oid for oid in failed_offer_ids if str(oid) in task_offer_id_set]
        _register_erp_sync_task(
            result.get('task_no'),
            task_offer_ids,
            source,
            immediate_failed_offer_ids=immediate_failed_offer_ids
        )
        _log_task('POST', '/api/products/sync-all', {'source': source, 'count': len(offer_ids)}, result, True, '', execution_time)
        return jsonify({
            'success': True,
            'async_task': True,
            'task_no': result.get('task_no'),
            'message': result.get('message') or '已提交批量同步任务',
            'total': result.get('total'),
            'success_count': result.get('success_count'),
            'fail_count': result.get('fail_count'),
            'pending_count': result.get('pending_count'),
            'failed_details': failed_details,
            'failure_summary': result.get('failure_summary')
        })
    elif result.get('success'):
        failed_offer_ids = _extract_failed_offer_ids_by_details(failed_details, offer_ids)
        success_offer_ids = [oid for oid in offer_ids if str(oid) not in set(str(x) for x in failed_offer_ids)]
        if success_offer_ids:
            product_service.batch_update_sync_status(success_offer_ids, 'synced', source=source)
        if failed_details:
            _update_failed_sync_status_by_details(
                failed_details,
                offer_ids,
                fallback_reason=result.get('error') or '同步失败',
                source=source
            )
        _log_task('POST', '/api/products/sync-all', {'source': source, 'count': len(offer_ids)}, result, True, '', execution_time)
        return jsonify({
            'success': True,
            'message': f'成功同步 {len(success_offer_ids)} 件商品到ERP',
            'data': {
                'syncedCount': len(success_offer_ids),
                'failedCount': len(failed_offer_ids),
                'failedIds': failed_offer_ids,
                'syncTime': datetime.now().isoformat()
            },
            'failure_summary': result.get('failure_summary')
        })
    elif result.get('partial_success'):
        failed_offer_ids = _extract_failed_offer_ids_by_details(failed_details, offer_ids)
        failed_offer_id_set = set(str(x) for x in failed_offer_ids)
        success_offer_ids = [oid for oid in offer_ids if str(oid) not in failed_offer_id_set]
        
        if success_offer_ids:
            product_service.batch_update_sync_status(success_offer_ids, 'synced', source=source)
        if failed_offer_ids:
            _update_failed_sync_status_by_details(
                failed_details,
                offer_ids,
                fallback_reason=result.get('error', '同步失败'),
                source=source
            )
        
        _log_task('POST', '/api/products/sync-all', {'source': source, 'count': len(offer_ids)}, result, True, '', execution_time)
        return jsonify({
            'success': True,
            'message': f'成功同步 {len(success_offer_ids)} 件，失败 {len(failed_offer_ids)} 件',
            'data': {
                'syncedCount': len(success_offer_ids),
                'failedCount': len(failed_offer_ids),
                'failedIds': failed_offer_ids,
                'syncTime': datetime.now().isoformat()
            },
            'failure_summary': result.get('failure_summary')
        })
    else:
        # 全部失败
        product_service.batch_update_sync_status(offer_ids, 'failed', result.get('error'), source=source)
        _log_task('POST', '/api/products/sync-all', {'source': source, 'count': len(offer_ids)}, result, False, result.get('error', '同步失败'), execution_time)
        return jsonify({
            'success': False,
            'error': result.get('error', '同步失败'),
            'failure_summary': result.get('failure_summary'),
            'data': {
                'syncedCount': 0,
                'failedCount': len(offer_ids),
                'failedIds': offer_ids
            }
        })

# 验证码代理API - 解决跨域问题
@api_bp.route('/api/captcha/get', methods=['POST', 'OPTIONS'])
def captcha_get():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    try:
        erp_api_url = os.environ.get('ERP_API_URL', 'https://dev.1bgo.com').rstrip('/')
        target_url = f'{erp_api_url}/admin-api/system/captcha/get'
        print(f"[Captcha Get] Requesting: {target_url}")
        
        response = requests.post(
            target_url,
            json=request.get_json() or {'captchaType': 'blockPuzzle'},
            headers={'Content-Type': 'application/json'},
            timeout=10
        )
        
        print(f"[Captcha Get] Response status: {response.status_code}")
        print(f"[Captcha Get] Response text: {response.text[:500]}")
        
        # 如果返回的是JSON
        if response.headers.get('content-type', '').startswith('application/json'):
            return jsonify(response.json()), response.status_code
        else:
            # 返回原始响应
            return jsonify({
                'success': False, 
                'error': f'Non-JSON response: {response.text[:200]}',
                'status_code': response.status_code
            }), 500
    except Exception as e:
        print(f"Captcha get error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@api_bp.route('/api/captcha/check', methods=['POST', 'OPTIONS'])
def captcha_check():
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    try:
        erp_api_url = os.environ.get('ERP_API_URL', 'https://dev.1bgo.com').rstrip('/')
        target_url = f'{erp_api_url}/admin-api/system/captcha/check'
        print(f"[Captcha Check] Requesting: {target_url}")
        
        response = requests.post(
            target_url,
            json=request.get_json(),
            headers={'Content-Type': 'application/json'},
            timeout=10
        )
        
        print(f"[Captcha Check] Response status: {response.status_code}")
        print(f"[Captcha Check] Response text: {response.text[:500]}")
        
        if response.headers.get('content-type', '').startswith('application/json'):
            return jsonify(response.json()), response.status_code
        else:
            return jsonify({
                'success': False, 
                'error': f'Non-JSON response: {response.text[:200]}',
                'status_code': response.status_code
            }), 500
    except Exception as e:
        print(f"Captcha check error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/api/products/sku/batch-update-channel-task', methods=['PUT', 'OPTIONS'])
@require_auth
def batch_update_sku_channel_task():
    """批量更新SKU渠道任务 - 转发到ERP系统"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    try:
        erp_api_url = os.environ.get('ERP_API_URL', 'https://dev.1bgo.com').rstrip('/')
        target_url = f'{erp_api_url}/admin-api/product/sku/batch-update-channel-task'
        
        # 获取请求数据
        request_data = request.get_json()
        if not request_data or not isinstance(request_data, list):
            return jsonify({'success': False, 'error': '请求数据格式错误，应为数组'}), 400
        
        print(f"[Batch Update SKU Channel] Request to: {target_url}")
        print(f"[Batch Update SKU Channel] Request data count: {len(request_data)}")
        print(f"[Batch Update SKU Channel] Request data: {json.dumps(request_data, ensure_ascii=False, indent=2)}")
        
        # 获取access_token
        access_token = get_valid_token()
        if not access_token:
            return jsonify({'success': False, 'code': 'UNAUTHORIZED', 'error': '登录已过期，请重新登录'}), 401
        
        # 构建请求头
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {access_token}'
        }
        
        # 转发请求到ERP系统
        response = requests.put(
            target_url,
            json=request_data,
            headers=headers,
            timeout=30
        )
        
        print(f"[Batch Update SKU Channel] Response status: {response.status_code}")
        print(f"[Batch Update SKU Channel] Response text: {response.text[:500]}")
        
        # 如果返回的是JSON
        if response.headers.get('content-type', '').startswith('application/json'):
            return jsonify(response.json()), response.status_code
        else:
            return jsonify({
                'success': False, 
                'error': f'ERP返回非JSON响应: {response.text[:200]}',
                'status_code': response.status_code
            }), 500
            
    except requests.exceptions.Timeout:
        print(f"[Batch Update SKU Channel] Request timeout")
        return jsonify({'success': False, 'error': '请求ERP系统超时'}), 504
    except requests.exceptions.ConnectionError as e:
        print(f"[Batch Update SKU Channel] Connection error: {e}")
        return jsonify({'success': False, 'error': '无法连接到ERP系统'}), 503
    except Exception as e:
        print(f"[Batch Update SKU Channel] Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== 商品分类相关API ====================

@api_bp.route('/api/categories')
def get_categories():
    """获取ERP商品分类列表（树形结构）- 使用缓存优化"""
    from app.services.erp_category_service import erp_category_service

    force_refresh = request.args.get('force_refresh') == '1'

    cached = erp_category_service.get_cached_categories()
    if cached is not None and not force_refresh:
        return jsonify({
            'success': True,
            'data': cached['data'],
            'total': cached['total'],
            'cached': True
        })

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            SELECT id, parentId, name, picUrl, sort, status, description, createTime, visible
            FROM erp_category
            ORDER BY sort ASC, id ASC
        ''')
        rows = cursor.fetchall()
    finally:
        conn.close()

    category_map = {}
    root_categories = []

    for row in rows:
        cat = {
            'id': row['id'],
            'parentId': row['parentId'],
            'name': row['name'],
            'picUrl': row['picUrl'] or '',
            'sort': row['sort'] or 0,
            'status': 'active' if row['status'] == 0 else 'inactive',
            'description': row['description'] or '',
            'createTime': str(row['createTime']) if row['createTime'] else '',
            'visible': bool(row['visible']),
            'children': []
        }
        category_map[cat['id']] = cat

    for cat in category_map.values():
        parent_id = cat['parentId']
        if parent_id is None or parent_id == 0:
            root_categories.append(cat)
        elif parent_id in category_map:
            category_map[parent_id]['children'].append(cat)

    root_categories.sort(key=lambda x: (x['sort'], x['id']))
    for cat in category_map.values():
        cat['children'].sort(key=lambda x: (x['sort'], x['id']))

    cache_data = {'data': root_categories, 'total': len(rows)}
    erp_category_service.set_cached_categories(cache_data)

    return jsonify({
        'success': True,
        'data': root_categories,
        'total': len(rows),
        'cached': False
    })


@api_bp.route('/api/categories/sync', methods=['POST'])
def sync_categories():
    """同步ERP商品分类"""
    start_time = time.time()
    from app.services.erp_category_service import erp_category_service
    from app.routes.auth import get_valid_token
    
    # 获取有效token（自动刷新过期token）
    access_token = get_valid_token()
    if not access_token:
        return jsonify({'success': False, 'code': 'UNAUTHORIZED', 'error': '登录已过期，请重新登录'}), 401
    
    tenant_id = session.get('tenant_id', '')
    
    result = erp_category_service.sync_categories(access_token=access_token, tenant_id=tenant_id)

    execution_time = int((time.time() - start_time) * 1000)

    if result.get('success'):
        erp_category_service.invalidate_cache()
        stats = erp_category_service.get_stats()
        _log_task('POST', '/api/categories/sync', {}, result, True, '', execution_time)
        return jsonify({
            'success': True,
            'message': f"成功同步 {result.get('total', 0)} 个分类",
            'total': result.get('total', 0),
            'affected': result.get('affected', 0),
            'stats': stats
        })
    else:
        _log_task('POST', '/api/categories/sync', {}, result, False, result.get('error', '同步失败'), execution_time)
        return jsonify({
            'success': False,
            'error': result.get('error', '同步失败')
        }), 400


@api_bp.route('/api/categories/stats')
def get_category_stats():
    """获取分类统计信息"""
    from app.services.erp_category_service import erp_category_service
    
    stats = erp_category_service.get_stats()
    return jsonify({
        'success': True,
        'stats': stats
    })


@api_bp.route('/api/products/batch/update-delivery-template', methods=['POST'])
def batch_update_delivery_template():
    start_time = time.time()
    data = request.get_json() or {}
    offer_ids = data.get('offer_ids', [])
    source = data.get('source', 'ALIBABA_1688')

    if not offer_ids:
        return jsonify({'success': False, 'error': '请选择要操作的商品'}), 400

    products = product_service.get_products_for_sync(offer_ids, include_detail=True, source=source)

    synced_products = [p for p in products if str(p.get('sync_status', '')).lower() == 'synced']
    if not synced_products:
        return jsonify({'success': False, 'error': '所选商品中没有已同步状态的商品，只有已同步商品才能修改物流模板'}), 400

    access_token = get_valid_token()
    if not access_token:
        return jsonify({'success': False, 'code': 'UNAUTHORIZED', 'error': '登录已过期，请重新登录'}), 401

    tenant_id = session.get('tenant_id', '')
    from app.services.erp_sync_service import erp_sync_service
    result = erp_sync_service.batch_update_delivery_template(synced_products, access_token=access_token, tenant_id=tenant_id)

    execution_time = int((time.time() - start_time) * 1000)
    _log_task(
        'POST',
        '/api/products/batch/update-delivery-template',
        {'offer_ids': offer_ids, 'source': source, 'synced_count': len(synced_products)},
        result,
        result.get('success', False),
        result.get('error', ''),
        execution_time
    )

    return jsonify(result)


@api_bp.route('/api/products/batch/check-shipping-info', methods=['POST'])
def batch_check_shipping_info():
    data = request.get_json() or {}
    offer_ids = data.get('offer_ids', [])
    source = data.get('source', 'ALIBABA_1688')

    if not offer_ids:
        return jsonify({'success': False, 'error': '请选择商品'}), 400

    results = product_service.batch_check_shipping_info(offer_ids, source=source)
    has_count = sum(1 for r in results if r.get('has_shipping_info'))
    return jsonify({
        'success': True,
        'total': len(results),
        'has_shipping_count': has_count,
        'no_shipping_count': len(results) - has_count,
        'items': results
    })


@api_bp.route('/api/products/synced-offer-ids', methods=['GET'])
def get_synced_offer_ids():
    source = request.args.get('source', 'ALIBABA_1688')
    include_stats = request.args.get('include_stats', 'false').lower() == 'true'

    if include_stats:
        stats = product_service.check_shipping_info_stats(source=source)
        return jsonify({
            'success': True,
            'total': stats['total'],
            'has_shipping_count': stats['has_shipping_count'],
            'no_shipping_count': stats['no_shipping_count'],
            'offer_ids': []
        })

    offer_ids = product_service.get_synced_offer_ids(source=source)
    return jsonify({
        'success': True,
        'total': len(offer_ids),
        'offer_ids': offer_ids
    })


@api_bp.route('/api/products/shipping-info-stats', methods=['GET'])
def get_shipping_info_stats():
    source = request.args.get('source', 'ALIBABA_1688')
    stats = product_service.check_shipping_info_stats(source=source)
    return jsonify({
        'success': True,
        **stats
    })
