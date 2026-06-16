# -*- coding: utf-8 -*-
import csv
import io
import json
import logging
import os
import re
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4

import requests

from app.models import get_db


logger = logging.getLogger(__name__)
PLACEHOLDER = '%s'
SPU_PRICE_SYNC_STATUS = 'synced'
SPU_PRICE_PAYLOAD_KEYS = ('id', 'price', 'marketPrice')


class SPUPriceService:
    """SPU price maintenance helpers for import_product."""

    def __init__(self):
        self.failure_log_dir = os.path.abspath(
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'logs')
        )

    def count_all_products(self):
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT COUNT(*) AS total_count FROM import_product WHERE COALESCE(sync_status, '') <> %s",
                ('failed',)
            )
            row = cursor.fetchone() or {}
            return int(row.get('total_count') or 0)
        finally:
            conn.close()

    def count_synced_products(self):
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT COUNT(*) AS total_count FROM import_product WHERE sync_status = %s",
                (SPU_PRICE_SYNC_STATUS,)
            )
            row = cursor.fetchone() or {}
            return int(row.get('total_count') or 0)
        finally:
            conn.close()

    def _failure_log_path(self, job_id):
        safe_job_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(job_id or ''))
        if not safe_job_id:
            raise ValueError('invalid failure log id')
        return os.path.join(self.failure_log_dir, f'spu_price_failures_{safe_job_id}.jsonl')

    def _success_log_path(self, job_id):
        safe_job_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(job_id or ''))
        if not safe_job_id:
            raise ValueError('invalid success log id')
        return os.path.join(self.failure_log_dir, f'spu_price_successes_{safe_job_id}.jsonl')

    def _create_failure_log(self):
        os.makedirs(self.failure_log_dir, exist_ok=True)
        job_id = uuid4().hex
        path = self._failure_log_path(job_id)
        return job_id, path

    def _create_operation_logs(self):
        os.makedirs(self.failure_log_dir, exist_ok=True)
        job_id = uuid4().hex
        return job_id, self._failure_log_path(job_id), self._success_log_path(job_id)

    def _append_failure(self, handle, details, item, sample_limit=50):
        if not item:
            return
        handle.write(json.dumps(item, ensure_ascii=False, default=str) + '\n')
        if len(details) < sample_limit:
            details.append(item)

    def _append_success(self, handle, details, item, sample_limit=50):
        if not item:
            return
        handle.write(json.dumps(item, ensure_ascii=False, default=str) + '\n')
        if len(details) < sample_limit:
            details.append(item)

    def _to_decimal(self, value, field_name, allow_zero=True):
        if value is None or (isinstance(value, str) and value.strip() == ''):
            raise ValueError(f'{field_name}缺失')
        try:
            amount = Decimal(str(value).strip())
        except (InvalidOperation, ValueError, TypeError):
            raise ValueError(f'{field_name}格式非法: {value}')
        if amount < 0:
            raise ValueError(f'{field_name}不能为负数')
        if amount == 0 and not allow_zero:
            raise ValueError(f'{field_name}必须大于0')
        return amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    def _decimal_to_float(self, value):
        if value is None:
            return None
        return float(Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

    def _first_present(self, data, names):
        for name in names:
            if isinstance(data, dict) and name in data:
                return data.get(name)
        return None

    def _parse_sku_info(self, value):
        if value is None or (isinstance(value, str) and value.strip() == ''):
            raise ValueError('sku_info为空')
        if isinstance(value, list):
            sku_list = value
        elif isinstance(value, str):
            try:
                sku_list = json.loads(value)
            except Exception as exc:
                raise ValueError(f'sku_info格式非法: {exc}')
        else:
            raise ValueError('sku_info格式非法: 必须是JSON数组')
        if not isinstance(sku_list, list):
            raise ValueError('sku_info格式非法: 必须是JSON数组')
        if not sku_list:
            raise ValueError('sku_info为空数组')
        return sku_list

    def _extract_min_sku_prices(self, sku_info_value, allow_missing_adjusted_price=False):
        sku_list = self._parse_sku_info(sku_info_value)
        min_item = None
        invalid_reasons = []

        for index, sku in enumerate(sku_list):
            if not isinstance(sku, dict):
                invalid_reasons.append(f'第{index + 1}个SKU不是对象')
                continue
            try:
                consign_price = self._to_decimal(
                    self._first_present(sku, ['consignPrice', 'consign_price']),
                    'consignPrice',
                    allow_zero=False
                )
            except ValueError as exc:
                invalid_reasons.append(f'第{index + 1}个SKU {exc}')
                continue

            if min_item is None or consign_price < min_item['consign_price']:
                min_item = {
                    'sku_index': index,
                    'sku_id': str(
                        self._first_present(sku, ['skuId', 'sku_id', 'specId', 'spec_id']) or ''
                    ).strip(),
                    'consign_price': consign_price,
                    'market_price_raw': self._first_present(
                        sku,
                        ['marketPrice', 'market_price']
                    ),
                    'adjusted_price_raw': self._first_present(
                        sku,
                        ['adjusted_price', 'adjustedPrice']
                    ),
                }

        if min_item is None:
            reason = '未找到包含有效consignPrice的SKU'
            if invalid_reasons:
                reason = f'{reason}: {"; ".join(invalid_reasons[:3])}'
            raise ValueError(reason)

        market_price = self._to_decimal(min_item['market_price_raw'], 'marketPrice', allow_zero=True)
        adjusted_price_raw = min_item['adjusted_price_raw']
        adjusted_price_missing = adjusted_price_raw is None or (
            isinstance(adjusted_price_raw, str) and adjusted_price_raw.strip().lower() in ('', 'null', 'undefined')
        )
        if adjusted_price_missing and allow_missing_adjusted_price:
            adjusted_price = None
        else:
            adjusted_price = self._to_decimal(adjusted_price_raw, 'adjusted_price', allow_zero=True)

        return {
            'sku_index': min_item['sku_index'],
            'sku_id': min_item['sku_id'],
            'sell_price': min_item['consign_price'],
            'cost_price': market_price,
            'adjusted_price': adjusted_price,
        }

    def _apply_spu_price_update_group(
        self,
        conn,
        cursor,
        success_handle,
        failure_handle,
        success_details,
        failed_details,
        sql,
        params_items,
    ):
        if not params_items:
            return 0, 0

        success_delta = 0
        fail_delta = 0
        params_list = [params for params, _ in params_items]
        items_list = [item for _, item in params_items]

        try:
            cursor.executemany(sql, params_list)
            conn.commit()
            success_delta += len(items_list)
            for item in items_list:
                self._append_success(success_handle, success_details, item)
        except Exception as batch_exc:
            conn.rollback()
            logger.exception('[SPU Min SKU Price] batch update failed, fallback to row mode')
            for params, item in params_items:
                try:
                    cursor.execute(sql, params)
                    conn.commit()
                    success_delta += 1
                    self._append_success(success_handle, success_details, item)
                except Exception as row_exc:
                    conn.rollback()
                    fail_delta += 1
                    self._append_failure(failure_handle, failed_details, {
                        'id': item.get('id'),
                        'offer_id': item.get('offer_id'),
                        'title': item.get('title') or '',
                        'reason': f'数据库更新失败: {row_exc}',
                        'batch_error': str(batch_exc),
                        'sync_status': item.get('sync_status') or '',
                    })

        return success_delta, fail_delta

    def update_spu_prices_from_min_sku(self, batch_size=1000, progress_callback=None):
        started_at = time.time()
        total = self.count_all_products()
        job_id, failure_path, success_path = self._create_operation_logs()
        failed_details = []
        success_details = []
        skipped_details = []
        success_count = 0
        fail_count = 0
        skipped_count = 0
        processed_count = 0
        last_id = 0
        batch_size = max(1, int(batch_size or 1000))

        if callable(progress_callback):
            progress_callback(0, total, success_count, fail_count, '正在扫描SPU价格...')

        conn = get_db()
        cursor = conn.cursor()
        failure_handle = open(failure_path, 'w', encoding='utf-8')
        success_handle = open(success_path, 'w', encoding='utf-8')

        try:
            while True:
                cursor.execute(
                    '''
                        SELECT id, offer_id, title, sell_price, cost_price, adjusted_price, sku_info, sync_status
                        FROM import_product
                        WHERE id > %s
                        ORDER BY id ASC
                        LIMIT %s
                    ''',
                    (last_id, batch_size)
                )
                rows = cursor.fetchall()
                if not rows:
                    break

                update_params = []
                success_items = []
                for row in rows:
                    row_id = int(row.get('id') or 0)
                    last_id = row_id
                    sync_status = str(row.get('sync_status') or '').strip()
                    offer_id = str(row.get('offer_id') or '').strip()
                    if sync_status == 'failed':
                        skipped_count += 1
                        skipped_item = {
                            'id': row_id,
                            'offer_id': offer_id,
                            'title': row.get('title') or '',
                            'sync_status': sync_status,
                            'reason': 'sync_status=failed，已跳过价格回写',
                        }
                        if len(skipped_details) < 50:
                            skipped_details.append(skipped_item)
                        continue
                    processed_count += 1
                    try:
                        price_info = self._extract_min_sku_prices(
                            row.get('sku_info'),
                            allow_missing_adjusted_price=(sync_status == 'pending')
                        )
                        adjusted_price_value = price_info.get('adjusted_price')
                        final_adjusted_price = adjusted_price_value
                        if sync_status == 'pending' and adjusted_price_value is None:
                            final_adjusted_price = row.get('adjusted_price')
                            update_params.append((
                                price_info['sell_price'],
                                price_info['cost_price'],
                                row_id
                            ))
                        else:
                            update_params.append((
                                price_info['sell_price'],
                                price_info['cost_price'],
                                adjusted_price_value,
                                row_id
                            ))
                        success_items.append({
                            'id': row_id,
                            'offer_id': offer_id,
                            'title': row.get('title') or '',
                            'sku_id': price_info.get('sku_id') or '',
                            'sku_index': price_info.get('sku_index'),
                            'sync_status': sync_status,
                            'old_sell_price': self._decimal_to_float(row.get('sell_price')) if row.get('sell_price') is not None else None,
                            'old_cost_price': self._decimal_to_float(row.get('cost_price')) if row.get('cost_price') is not None else None,
                            'old_adjusted_price': self._decimal_to_float(row.get('adjusted_price')) if row.get('adjusted_price') is not None else None,
                            'sell_price': self._decimal_to_float(price_info['sell_price']),
                            'cost_price': self._decimal_to_float(price_info['cost_price']),
                            'adjusted_price': self._decimal_to_float(final_adjusted_price) if final_adjusted_price is not None else None,
                        })
                    except Exception as exc:
                        fail_count += 1
                        self._append_failure(failure_handle, failed_details, {
                            'id': row_id,
                            'offer_id': offer_id,
                            'title': row.get('title') or '',
                            'sync_status': sync_status,
                            'reason': str(exc),
                        })

                if update_params:
                    update_sql_with_adjusted = '''
                        UPDATE import_product
                        SET sell_price = %s,
                            cost_price = %s,
                            adjusted_price = %s
                        WHERE id = %s
                    '''
                    update_sql_without_adjusted = '''
                        UPDATE import_product
                        SET sell_price = %s,
                            cost_price = %s
                        WHERE id = %s
                    '''
                    with_adjusted = []
                    without_adjusted = []
                    for params_item, item in zip(update_params, success_items):
                        if len(params_item) == 4:
                            with_adjusted.append((params_item, item))
                        else:
                            without_adjusted.append((params_item, item))
                    success_delta = 0
                    fail_delta = 0
                    if with_adjusted:
                        delta_success, delta_fail = self._apply_spu_price_update_group(
                            conn,
                            cursor,
                            success_handle,
                            failure_handle,
                            success_details,
                            failed_details,
                            update_sql_with_adjusted,
                            with_adjusted,
                        )
                        success_delta += delta_success
                        fail_delta += delta_fail
                    if without_adjusted:
                        delta_success, delta_fail = self._apply_spu_price_update_group(
                            conn,
                            cursor,
                            success_handle,
                            failure_handle,
                            success_details,
                            failed_details,
                            update_sql_without_adjusted,
                            without_adjusted,
                        )
                        success_delta += delta_success
                        fail_delta += delta_fail
                    success_count += success_delta
                    fail_count += fail_delta

                if callable(progress_callback):
                    progress_callback(processed_count, total, success_count, fail_count, '正在更新本地SPU价格...')

            failure_handle.flush()
            success_handle.flush()

            return {
                'success': True,
                'partial_success': success_count > 0 and fail_count > 0,
                'job_id': job_id,
                'message': 'SPU价格更新完成' if fail_count == 0 else 'SPU价格更新完成，存在失败记录',
                'total': total,
                'processed_count': processed_count,
                'success_count': success_count,
                'fail_count': fail_count,
                'skipped_count': skipped_count,
                'success_details': success_details,
                'failed_details': failed_details,
                'skipped_details': skipped_details,
                'success_log_id': job_id if success_count else '',
                'failure_log_id': job_id if fail_count else '',
                'success_log_truncated': success_count > len(success_details),
                'failure_log_truncated': fail_count > len(failed_details),
                'execution_time_ms': int((time.time() - started_at) * 1000),
            }
        except Exception as exc:
            conn.rollback()
            logger.exception('[SPU Min SKU Price] update stopped; committed batches are preserved')
            remaining_count = max(0, total - processed_count)
            if remaining_count:
                self._append_failure(failure_handle, failed_details, {
                    'id': '',
                    'offer_id': '',
                    'title': '',
                    'reason': f'任务中断，剩余 {remaining_count} 条未处理: {exc}',
                })
                failure_handle.flush()
            success_handle.flush()
            return {
                'success': False,
                'partial_success': success_count > 0,
                'job_id': job_id,
                'total': total,
                'processed_count': processed_count,
                'success_count': success_count,
                'fail_count': fail_count + remaining_count,
                'skipped_count': skipped_count,
                'success_details': success_details,
                'failed_details': failed_details,
                'skipped_details': skipped_details,
                'success_log_id': job_id if success_count else '',
                'failure_log_id': job_id if fail_count or remaining_count else '',
                'error': str(exc),
                'execution_time_ms': int((time.time() - started_at) * 1000),
            }
        finally:
            try:
                failure_handle.close()
            finally:
                try:
                    success_handle.close()
                finally:
                    conn.close()

    def _build_headers(self, access_token='', tenant_id=''):
        headers = {'Content-Type': 'application/json'}
        if access_token:
            headers['Authorization'] = f'Bearer {access_token}'
            headers['accessToken'] = access_token
        if tenant_id is not None and str(tenant_id).strip() != '':
            tenant_value = str(tenant_id).strip()
            headers['tenant-id'] = tenant_value
            headers['tenantId'] = tenant_value
        return headers

    def _get_erp_api_url(self):
        return os.environ.get('ERP_API_URL', 'https://dev.1bgo.com').rstrip('/')

    def _parse_erp_response_body(self, response):
        response_text = response.text or ''
        try:
            return response.json() if response_text else {}
        except Exception:
            return {'raw': response_text[:1000]}

    def _is_erp_success(self, status_code, body):
        if status_code not in (200, 201):
            return False
        if isinstance(body, dict) and body.get('success') is True:
            return True
        code = str((body or {}).get('code') if isinstance(body, dict) else '').strip()
        return code in ('0', '200')

    def _erp_error_message(self, status_code, body, fallback='ERP接口调用失败'):
        if isinstance(body, dict):
            for key in ('msg', 'message', 'error', 'errorMsg'):
                value = body.get(key)
                if value:
                    return str(value)
            raw = body.get('raw')
            if raw:
                return f'HTTP {status_code}: {str(raw)[:200]}'
        return f'HTTP {status_code}: {fallback}'

    def _put_spu_price_batch(self, spu_price_list, access_token='', tenant_id='', timeout=30, max_retries=3):
        endpoint = f'{self._get_erp_api_url()}/admin-api/product/sku/batch-update-price-task'
        sanitized_spu_price_list = [
            {key: item.get(key) for key in SPU_PRICE_PAYLOAD_KEYS}
            for item in (spu_price_list or [])
        ]
        # SPU price sync is intentionally restricted to SPU fields.
        # The ERP endpoint supports SKU updates too, so keep skuPriceList empty here.
        request_body = {
            'spuPriceList': sanitized_spu_price_list,
            'skuPriceList': []
        }
        headers = self._build_headers(access_token=access_token, tenant_id=tenant_id)
        attempts = max(1, int(max_retries or 3))
        last_error = 'ERP接口调用失败'
        last_body = {}
        last_status = 0

        for attempt in range(1, attempts + 1):
            try:
                response = requests.put(endpoint, json=request_body, headers=headers, timeout=timeout)
                last_status = response.status_code
                body = self._parse_erp_response_body(response)
                last_body = body
                if self._is_erp_success(response.status_code, body):
                    return {
                        'success': True,
                        'endpoint': endpoint,
                        'attempts': attempt,
                        'request_preview': {
                            'spuPriceList': sanitized_spu_price_list[:3],
                            'skuPriceList': []
                        },
                        'response_preview': body,
                    }
                last_error = self._erp_error_message(response.status_code, body)
                if 400 <= response.status_code < 500:
                    break
            except requests.exceptions.Timeout:
                last_error = '请求ERP系统超时'
            except requests.exceptions.ConnectionError as exc:
                last_error = f'无法连接到ERP系统: {exc}'
            except Exception as exc:
                last_error = str(exc)

            if attempt < attempts:
                time.sleep(min(0.2 * attempt, 1.0))

        return {
            'success': False,
            'endpoint': endpoint,
            'attempts': attempt,
            'error': last_error,
            'http_status': last_status,
            'request_preview': {
                'spuPriceList': sanitized_spu_price_list[:3],
                'skuPriceList': []
            },
            'response_preview': last_body,
        }

    def _price_to_cents(self, value):
        if value is None:
            return 0
        amount = float(Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        return int(round(amount * 100))

    def _build_spu_price_item(self, row):
        sync_status = str(row.get('sync_status') or '').strip()
        if sync_status != SPU_PRICE_SYNC_STATUS:
            raise ValueError('仅允许同步已同步状态的SPU价格')
        offer_id = str(row.get('offer_id') or '').strip()
        if not offer_id:
            raise ValueError('offer_id为空')
        adjusted_price = self._to_decimal(row.get('adjusted_price'), 'adjusted_price', allow_zero=True)
        cost_price = self._to_decimal(row.get('cost_price'), 'cost_price', allow_zero=True)
        return {
            'id': offer_id,
            'price': self._price_to_cents(adjusted_price),
            'marketPrice': self._price_to_cents(cost_price),
        }

    def _mark_price_synced(self, cursor, row_ids, status_policy='keep_synced'):
        if not row_ids:
            return 0
        target_status = 'synced_price' if status_policy == 'synced_price' else 'synced'
        placeholders = ','.join([PLACEHOLDER for _ in row_ids])
        sync_at = datetime.now().isoformat()
        cursor.execute(
            f'''
                UPDATE import_product
                SET sync_status = %s,
                    sync_at = %s,
                    sync_error = NULL
                WHERE sync_status = %s
                  AND id IN ({placeholders})
            ''',
            [target_status, sync_at, SPU_PRICE_SYNC_STATUS] + list(row_ids)
        )
        return cursor.rowcount

    def sync_synced_spu_prices_to_erp(
        self,
        access_token='',
        tenant_id='',
        batch_size=1000,
        timeout=30,
        max_retries=3,
        status_policy='keep_synced',
        progress_callback=None,
    ):
        started_at = time.time()
        total = self.count_synced_products()
        job_id, failure_path = self._create_failure_log()
        failed_details = []
        success_count = 0
        fail_count = 0
        processed_count = 0
        last_id = 0
        batch_size = max(1, int(batch_size or 1000))
        request_snapshots = []
        response_snapshots = []

        if callable(progress_callback):
            progress_callback(0, total, success_count, fail_count, '正在组装SPU价格请求...')

        if total == 0:
            return {
                'success': True,
                'empty': True,
                'message': '没有需要同步的商品',
                'job_id': job_id,
                'total': 0,
                'processed_count': 0,
                'success_count': 0,
                'fail_count': 0,
                'failed_details': [],
                'failure_log_id': '',
                'execution_time_ms': int((time.time() - started_at) * 1000),
            }

        conn = get_db()
        cursor = conn.cursor()
        failure_handle = open(failure_path, 'w', encoding='utf-8')

        try:
            while True:
                cursor.execute(
                    '''
                        SELECT id, offer_id, adjusted_price, cost_price, sync_status
                        FROM import_product
                        WHERE sync_status = %s AND id > %s
                        ORDER BY id ASC
                        LIMIT %s
                    ''',
                    (SPU_PRICE_SYNC_STATUS, last_id, batch_size)
                )
                rows = cursor.fetchall()
                if not rows:
                    break

                row_ids = []
                spu_price_list = []
                valid_rows = []
                for row in rows:
                    row_id = int(row.get('id') or 0)
                    last_id = row_id
                    processed_count += 1
                    offer_id = str(row.get('offer_id') or '').strip()
                    try:
                        spu_price_list.append(self._build_spu_price_item(row))
                        row_ids.append(row_id)
                        valid_rows.append(row)
                    except Exception as exc:
                        fail_count += 1
                        self._append_failure(failure_handle, failed_details, {
                            'id': row_id,
                            'offer_id': offer_id,
                            'reason': str(exc),
                        })

                if not spu_price_list:
                    continue

                erp_result = self._put_spu_price_batch(
                    spu_price_list,
                    access_token=access_token,
                    tenant_id=tenant_id,
                    timeout=timeout,
                    max_retries=max_retries,
                )
                request_snapshots.append(erp_result.get('request_preview') or {})
                response_snapshots.append(erp_result.get('response_preview') or {})

                if erp_result.get('success'):
                    self._mark_price_synced(cursor, row_ids, status_policy=status_policy)
                    conn.commit()
                    success_count += len(row_ids)
                    if callable(progress_callback):
                        progress_callback(processed_count, total, success_count, fail_count, '正在同步SPU价格到ERP...')
                    continue

                conn.rollback()
                reason = erp_result.get('error') or 'ERP接口调用失败'
                for row, item in zip(valid_rows, spu_price_list):
                    fail_count += 1
                    self._append_failure(failure_handle, failed_details, {
                        'id': row.get('id'),
                        'offer_id': item.get('id'),
                        'reason': reason,
                        'http_status': erp_result.get('http_status'),
                        'attempts': erp_result.get('attempts'),
                        'erp_response': erp_result.get('response_preview'),
                    })

                if callable(progress_callback):
                    progress_callback(processed_count, total, success_count, fail_count, '正在同步SPU价格到ERP...')

            failure_handle.flush()
            has_failed = fail_count > 0
            return {
                'success': not has_failed,
                'partial_success': success_count > 0 and has_failed,
                'job_id': job_id,
                'message': 'SPU价格同步完成' if not has_failed else 'SPU价格同步存在失败记录',
                'total': total,
                'processed_count': processed_count,
                'success_count': success_count,
                'fail_count': fail_count,
                'failed_details': failed_details,
                'failure_log_id': job_id if fail_count else '',
                'failure_log_truncated': fail_count > len(failed_details),
                'erp_request_snapshots': request_snapshots[:3],
                'erp_response_snapshots': response_snapshots[:3],
                'execution_time_ms': int((time.time() - started_at) * 1000),
            }
        except Exception as exc:
            conn.rollback()
            logger.exception('[SPU ERP Price Sync] failed')
            return {
                'success': False,
                'job_id': job_id,
                'total': total,
                'processed_count': processed_count,
                'success_count': success_count,
                'fail_count': max(fail_count, total - success_count),
                'failed_details': failed_details,
                'failure_log_id': job_id if fail_count else '',
                'error': str(exc),
                'execution_time_ms': int((time.time() - started_at) * 1000),
            }
        finally:
            try:
                failure_handle.close()
            finally:
                conn.close()

    def export_failure_log_csv(self, job_id):
        path = self._failure_log_path(job_id)
        if not os.path.exists(path):
            return None

        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=['id', 'offer_id', 'title', 'reason', 'http_status', 'attempts', 'erp_response']
        )
        writer.writeheader()
        with open(path, 'r', encoding='utf-8') as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    item = {'reason': line}
                writer.writerow({
                    'id': item.get('id', ''),
                    'offer_id': item.get('offer_id', ''),
                    'title': item.get('title', ''),
                    'reason': item.get('reason', ''),
                    'http_status': item.get('http_status', ''),
                    'attempts': item.get('attempts', ''),
                    'erp_response': json.dumps(item.get('erp_response', ''), ensure_ascii=False, default=str),
                })
        return '\ufeff' + output.getvalue()

    def export_success_log_csv(self, job_id):
        path = self._success_log_path(job_id)
        if not os.path.exists(path):
            return None

        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                'id', 'offer_id', 'title', 'sku_id', 'sku_index',
                'old_sell_price', 'old_cost_price', 'old_adjusted_price',
                'sell_price', 'cost_price', 'adjusted_price'
            ]
        )
        writer.writeheader()
        with open(path, 'r', encoding='utf-8') as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    item = {}
                writer.writerow({
                    'id': item.get('id', ''),
                    'offer_id': item.get('offer_id', ''),
                    'title': item.get('title', ''),
                    'sku_id': item.get('sku_id', ''),
                    'sku_index': item.get('sku_index', ''),
                    'old_sell_price': item.get('old_sell_price', ''),
                    'old_cost_price': item.get('old_cost_price', ''),
                    'old_adjusted_price': item.get('old_adjusted_price', ''),
                    'sell_price': item.get('sell_price', ''),
                    'cost_price': item.get('cost_price', ''),
                    'adjusted_price': item.get('adjusted_price', ''),
                })
        return '\ufeff' + output.getvalue()


spu_price_service = SPUPriceService()
