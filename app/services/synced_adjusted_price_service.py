# -*- coding: utf-8 -*-
import csv
import io
import json
import logging
import os
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4

from app.config import SOURCE_TYPE
from app.models import get_db


logger = logging.getLogger(__name__)
PLACEHOLDER = '%s'


class SyncedAdjustedPriceService:
    """Batch-adjust synced products that already have local adjusted prices."""

    def __init__(self):
        self.log_dir = os.path.abspath(
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'logs')
        )

    def _operation_log_paths(self, job_id):
        os.makedirs(self.log_dir, exist_ok=True)
        return (
            os.path.join(self.log_dir, f'synced_adjusted_price_successes_{job_id}.jsonl'),
            os.path.join(self.log_dir, f'synced_adjusted_price_failures_{job_id}.jsonl'),
        )

    def _success_log_path(self, job_id):
        return os.path.join(self.log_dir, f'synced_adjusted_price_successes_{job_id}.jsonl')

    def _failure_log_path(self, job_id):
        return os.path.join(self.log_dir, f'synced_adjusted_price_failures_{job_id}.jsonl')

    def _append_jsonl(self, handle, details, item, sample_limit=50):
        if not item:
            return
        handle.write(json.dumps(item, ensure_ascii=False, default=str) + '\n')
        if len(details) < sample_limit:
            details.append(item)

    def _json_dumps(self, value):
        return json.dumps(value, ensure_ascii=False, default=str)

    def _to_decimal(self, value, default='0.00'):
        try:
            if value is None or (isinstance(value, str) and value.strip() == ''):
                raise ValueError('empty')
            amount = Decimal(str(value).strip())
        except (InvalidOperation, ValueError, TypeError):
            amount = Decimal(str(default))
        if amount < 0:
            amount = Decimal('0.00')
        return amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    def _to_float(self, value):
        return float(self._to_decimal(value))

    def _price_to_cents(self, value):
        amount = self._to_decimal(value)
        return int(round(float(amount) * 100))

    def _market_price(self, base_price):
        base_amount = self._to_decimal(base_price)
        return float((base_amount * Decimal('1.7')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

    def _adjust_price(self, base_price, ratio):
        base_amount = self._to_decimal(base_price)
        ratio_amount = Decimal(str(ratio))
        return float((base_amount * ratio_amount).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

    def _ratio_by_cost_price(self, cost_price):
        cost_amount = self._to_decimal(cost_price)
        if cost_amount > Decimal('100'):
            return Decimal('1.20'), Decimal('20')
        if cost_amount >= Decimal('30'):
            return Decimal('1.25'), Decimal('25')
        return Decimal('1.40'), Decimal('40')

    def _parse_sku_list(self, sku_info_value, offer_id):
        if sku_info_value in (None, ''):
            return []
        if isinstance(sku_info_value, list):
            return sku_info_value
        if isinstance(sku_info_value, str):
            try:
                parsed = json.loads(sku_info_value)
            except Exception as exc:
                raise ValueError(f'offer_id={offer_id} sku_info JSON parse failed: {exc}')
            if isinstance(parsed, list):
                return parsed
        raise ValueError(f'offer_id={offer_id} sku_info must be a JSON array')

    def _sku_identifier(self, sku, fallback=''):
        if not isinstance(sku, dict):
            return str(fallback or '').strip()
        return str(
            sku.get('sku_id')
            or sku.get('skuId')
            or sku.get('spec_id')
            or sku.get('specId')
            or fallback
            or ''
        ).strip()

    def _spu_base_price(self, product):
        sell_price = self._to_decimal(product.get('sell_price'))
        if sell_price > 0:
            return sell_price
        return self._to_decimal(product.get('adjusted_price'))

    def _sku_base_price(self, sku):
        if not isinstance(sku, dict):
            return Decimal('0.00')
        for field_name in ('consign_price', 'consignPrice', 'price', 'adjusted_price', 'adjustedPrice'):
            value = sku.get(field_name)
            if value is None or (isinstance(value, str) and value.strip() == ''):
                continue
            amount = self._to_decimal(value)
            if amount >= 0:
                return amount
        return Decimal('0.00')

    def _build_adjusted_product(self, product):
        offer_id = str(product.get('offer_id') or '').strip()
        if not offer_id:
            raise ValueError('offer_id is empty')

        spu_base_price = self._spu_base_price(product)
        spu_ratio, spu_ratio_percent = self._ratio_by_cost_price(spu_base_price)
        spu_adjusted_price = self._adjust_price(spu_base_price, spu_ratio)
        spu_market_price = self._market_price(spu_base_price)

        sku_list = self._parse_sku_list(product.get('sku_info'), offer_id)
        updated_sku_list = []
        sku_price_list = []
        price_logs = [{
            'offer_id': offer_id,
            'sku_id': None,
            'entity_type': 'SPU',
            'field_name': 'adjusted_price',
            'old_value': self._to_float(product.get('adjusted_price')),
            'new_value': spu_adjusted_price,
            'ratio_percent': float(spu_ratio_percent),
            'base_price': float(spu_base_price),
        }]

        for sku_index, sku in enumerate(sku_list):
            if not isinstance(sku, dict):
                updated_sku_list.append(sku)
                continue

            sku_copy = dict(sku)
            sku_id = self._sku_identifier(sku_copy, fallback=f'index:{sku_index}')
            sku_base_price = self._sku_base_price(sku_copy)
            sku_ratio, sku_ratio_percent = self._ratio_by_cost_price(sku_base_price)
            sku_adjusted_price = self._adjust_price(sku_base_price, sku_ratio)
            sku_market_price = self._market_price(sku_base_price)
            sku_copy['adjusted_price'] = sku_adjusted_price
            sku_copy['marketPrice'] = sku_market_price
            updated_sku_list.append(sku_copy)
            if sku_id and not sku_id.startswith('index:'):
                sku_price_list.append({
                    'channelSkuId': sku_id,
                    'price': self._price_to_cents(sku_adjusted_price),
                    'marketPrice': self._price_to_cents(sku_market_price),
                })
            price_logs.append({
                'offer_id': offer_id,
                'sku_id': None if sku_id.startswith('index:') else sku_id,
                'entity_type': 'SKU',
                'field_name': 'adjusted_price',
                'old_value': self._to_float(sku_copy.get('adjustedPrice', sku.get('adjusted_price'))),
                'new_value': sku_adjusted_price,
                'ratio_percent': float(sku_ratio_percent),
                'base_price': float(sku_base_price),
            })

        updated_sku_info = self._json_dumps(updated_sku_list) if sku_list else (product.get('sku_info') or '')
        return {
            'id': product.get('id'),
            'offer_id': offer_id,
            'title': product.get('title') or '',
            'sell_price': float(spu_base_price),
            'adjusted_price': spu_adjusted_price,
            'cost_price': spu_market_price,
            'sku_info': updated_sku_list,
            'updated_sku_info': updated_sku_info,
            'spu_price_item': {
                'id': offer_id,
                'price': self._price_to_cents(spu_adjusted_price),
                'marketPrice': self._price_to_cents(spu_market_price),
            },
            'sku_price_list': sku_price_list,
            'price_logs': price_logs,
            'sku_count': len(sku_list),
        }

    def _target_where(self, source=SOURCE_TYPE, keyword=''):
        where_clauses = [
            'source_type = %s',
            "sync_status = 'synced'",
            'adjusted_price IS NOT NULL',
            'adjusted_price > 0',
        ]
        params = [source]
        keyword = str(keyword or '').strip()
        if keyword:
            where_clauses.append('(title LIKE %s OR supplier_name LIKE %s OR shop_name LIKE %s)')
            keyword_pattern = f'%{keyword}%'
            params.extend([keyword_pattern, keyword_pattern, keyword_pattern])
        return ' AND '.join(where_clauses), params

    def count_targets(self, source=SOURCE_TYPE, keyword=''):
        conn = get_db()
        cursor = conn.cursor()
        try:
            where_sql, params = self._target_where(source=source, keyword=keyword)
            cursor.execute(f'SELECT COUNT(*) AS total_count FROM import_product WHERE {where_sql}', params)
            row = cursor.fetchone() or {}
            return int(row.get('total_count') or 0)
        finally:
            conn.close()

    def _insert_backup_rows(self, cursor, job_id, operator, source, rows):
        if not rows:
            return
        values = []
        for row in rows:
            values.append((
                job_id,
                str(row.get('offer_id') or '').strip(),
                source,
                self._json_dumps(dict(row)),
                operator or '',
            ))
        cursor.executemany(
            f'''
                INSERT IGNORE INTO price_adjustment_backup
                (backup_job_id, offer_id, source_type, backup_data, operator)
                VALUES ({PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER})
            ''',
            values
        )

    def _insert_price_logs(self, cursor, job_id, operator, source, adjusted_products):
        values = []
        now = datetime.now()
        for product in adjusted_products:
            for log_item in product.get('price_logs') or []:
                values.append((
                    job_id,
                    None,
                    log_item.get('offer_id') or product.get('offer_id'),
                    log_item.get('sku_id'),
                    log_item.get('entity_type'),
                    log_item.get('field_name'),
                    log_item.get('old_value'),
                    log_item.get('new_value'),
                    source,
                    now,
                    None,
                    operator or '',
                    'batch_synced_adjusted_price',
                ))
        if not values:
            return
        cursor.executemany(
            f'''
                INSERT INTO price_revision_log
                (revision_job_id, rollback_job_id, offer_id, sku_id, entity_type, field_name,
                 old_value, new_value, source_type, operation_time, rolled_back_at, operator, operation_type)
                VALUES ({PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER},
                        {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER})
            ''',
            values
        )

    def _update_import_products(self, cursor, adjusted_products, source):
        values = []
        for product in adjusted_products:
            values.append((
                product.get('adjusted_price'),
                product.get('cost_price'),
                product.get('updated_sku_info') or '',
                product.get('id'),
                source,
            ))
        if not values:
            return 0
        cursor.executemany(
            '''
                UPDATE import_product
                SET adjusted_price = %s,
                    cost_price = %s,
                    sku_info = %s
                WHERE id = %s
                  AND source_type = %s
                  AND sync_status = 'synced'
            ''',
            values
        )
        return cursor.rowcount

    def _submit_erp_batch(self, adjusted_products, access_token='', tenant_id='', max_retries=3, timeout=60):
        import requests
        from app.services.erp_sync_service import erp_sync_service

        config = erp_sync_service._get_config()
        if not config.get('erp_api_url'):
            return {'success': False, 'error': 'ERP API未配置'}

        spu_price_list = []
        sku_price_list = []
        for product in adjusted_products:
            if product.get('spu_price_item'):
                spu_price_list.append(product.get('spu_price_item'))
            sku_price_list.extend(product.get('sku_price_list') or [])

        if not spu_price_list and not sku_price_list:
            return {'success': False, 'error': '没有有效的调价数据'}

        request_body = {
            'spuPriceList': spu_price_list,
            'skuPriceList': sku_price_list,
        }
        headers = {'Content-Type': 'application/json'}
        if access_token:
            headers['accessToken'] = access_token
            headers['Authorization'] = f'Bearer {access_token}'
        if tenant_id is not None and str(tenant_id).strip() != '':
            tenant_value = str(tenant_id).strip()
            headers['tenant-id'] = tenant_value
            headers['tenantId'] = tenant_value
        if config.get('erp_api_key') and 'Authorization' not in headers:
            headers['Authorization'] = f'Bearer {config["erp_api_key"]}'

        endpoint = f"{config['erp_api_url']}/admin-api/product/sku/batch-update-price-task"
        attempts = max(1, int(max_retries or 3))
        last_error = 'ERP批量调价失败'
        last_body = {}
        last_status = 0

        for attempt in range(1, attempts + 1):
            try:
                response = erp_sync_service._request_with_refresh_retry(
                    'PUT',
                    endpoint,
                    json=request_body,
                    headers=headers,
                    timeout=timeout,
                )
                last_status = response.status_code
                response_text = response.text or ''
                try:
                    body = response.json() if response_text else {}
                except Exception:
                    body = {'raw': response_text[:1000]}
                last_body = body

                if response.status_code in (200, 201) and str(body.get('code')) in ('0', '200'):
                    raw_data = body.get('data')
                    task_nos = []
                    if isinstance(raw_data, list):
                        for item in raw_data:
                            if isinstance(item, dict) and item.get('taskNo'):
                                task_nos.append(str(item.get('taskNo')))
                    elif isinstance(raw_data, dict) and raw_data.get('taskNo'):
                        task_nos.append(str(raw_data.get('taskNo')))
                    return {
                        'success': True,
                        'message': body.get('msg') or body.get('message') or 'ERP批量调价已提交',
                        'attempts': attempt,
                        'task_nos': task_nos,
                        'spu_count': len(spu_price_list),
                        'sku_count': len(sku_price_list),
                        'request_preview': {
                            'spuPriceList': spu_price_list[:3],
                            'skuPriceList': sku_price_list[:5],
                        },
                        'response_preview': body,
                    }

                last_error = erp_sync_service._extract_error_message(body) or (
                    f'HTTP {response.status_code}: {response_text[:200]}'
                    if response.status_code not in (200, 201)
                    else 'ERP返回失败'
                )
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
            'error': last_error,
            'attempts': attempt,
            'http_status': last_status,
            'spu_count': len(spu_price_list),
            'sku_count': len(sku_price_list),
            'request_preview': {
                'spuPriceList': spu_price_list[:3],
                'skuPriceList': sku_price_list[:5],
            },
            'response_preview': last_body,
            'response_body': last_body,
        }

    def batch_adjust_synced_adjusted_products(
        self,
        access_token='',
        tenant_id='',
        source=SOURCE_TYPE,
        keyword='',
        batch_size=200,
        timeout=60,
        max_retries=3,
        operator='unknown',
        progress_callback=None,
    ):
        started_at = time.time()
        job_id = uuid4().hex
        success_path, failure_path = self._operation_log_paths(job_id)
        success_details = []
        failed_details = []
        total = self.count_targets(source=source, keyword=keyword)
        processed_count = 0
        success_count = 0
        fail_count = 0
        batch_count = 0
        failed_batch_count = 0
        retry_count = 0
        last_id = 0
        batch_size = max(1, min(500, int(batch_size or 200)))

        if callable(progress_callback):
            progress_callback(0, total, 0, 0, '正在准备批量调价任务...')

        if total == 0:
            return {
                'success': True,
                'empty': True,
                'job_id': job_id,
                'message': '没有需要处理的已同步已调价商品',
                'total': 0,
                'processed_count': 0,
                'success_count': 0,
                'fail_count': 0,
                'batch_count': 0,
                'failed_batch_count': 0,
                'success_details': [],
                'failed_details': [],
                'success_log_id': '',
                'failure_log_id': '',
                'backup_job_id': job_id,
                'execution_time_ms': int((time.time() - started_at) * 1000),
            }

        success_handle = open(success_path, 'w', encoding='utf-8')
        failure_handle = open(failure_path, 'w', encoding='utf-8')

        try:
            while True:
                conn = get_db()
                cursor = conn.cursor()
                batch_rows = []
                adjusted_products = []
                invalid_items = []
                try:
                    if hasattr(conn, 'begin'):
                        conn.begin()
                    where_sql, params = self._target_where(source=source, keyword=keyword)
                    cursor.execute(
                        f'''
                            SELECT *
                            FROM import_product
                            WHERE {where_sql}
                              AND id > %s
                            ORDER BY id ASC
                            LIMIT %s
                            FOR UPDATE
                        ''',
                        params + [last_id, batch_size]
                    )
                    batch_rows = [dict(row) for row in cursor.fetchall()]
                    if not batch_rows:
                        conn.rollback()
                        break

                    batch_count += 1
                    last_id = max(int(row.get('id') or 0) for row in batch_rows)
                    processed_count += len(batch_rows)

                    for row in batch_rows:
                        offer_id = str(row.get('offer_id') or '').strip()
                        try:
                            adjusted_products.append(self._build_adjusted_product(row))
                        except Exception as exc:
                            invalid_items.append({
                                'id': row.get('id'),
                                'offer_id': offer_id,
                                'title': row.get('title') or '',
                                'reason': str(exc),
                                'batch_no': batch_count,
                            })

                    for item in invalid_items:
                        fail_count += 1
                        self._append_jsonl(failure_handle, failed_details, item)

                    if not adjusted_products:
                        conn.rollback()
                        if callable(progress_callback):
                            progress_callback(
                                processed_count,
                                total,
                                success_count,
                                fail_count,
                                f'第 {batch_count} 批无有效商品，已跳过'
                            )
                        continue

                    valid_offer_ids = {product.get('offer_id') for product in adjusted_products}
                    valid_rows = [row for row in batch_rows if str(row.get('offer_id') or '').strip() in valid_offer_ids]
                    self._insert_backup_rows(cursor, job_id, operator, source, valid_rows)
                    self._insert_price_logs(cursor, job_id, operator, source, adjusted_products)
                    self._update_import_products(cursor, adjusted_products, source)

                    erp_result = self._submit_erp_batch(
                        adjusted_products,
                        access_token=access_token,
                        tenant_id=tenant_id,
                        max_retries=max_retries,
                        timeout=timeout,
                    )
                    retry_count += max(0, int(erp_result.get('attempts') or 1) - 1)
                    if not erp_result.get('success'):
                        conn.rollback()
                        failed_batch_count += 1
                        reason = erp_result.get('error') or 'ERP批量调价失败'
                        for product in adjusted_products:
                            fail_count += 1
                            self._append_jsonl(failure_handle, failed_details, {
                                'id': product.get('id'),
                                'offer_id': product.get('offer_id'),
                                'title': product.get('title') or '',
                                'reason': reason,
                                'batch_no': batch_count,
                                'spu_count': 1,
                                'sku_count': product.get('sku_count') or 0,
                                'erp_response': erp_result.get('response_body') or erp_result.get('response_preview'),
                            })
                    else:
                        conn.commit()
                        task_nos = erp_result.get('task_nos') or []
                        for product in adjusted_products:
                            success_count += 1
                            self._append_jsonl(success_handle, success_details, {
                                'id': product.get('id'),
                                'offer_id': product.get('offer_id'),
                                'title': product.get('title') or '',
                                'batch_no': batch_count,
                                'spu_adjusted_price': product.get('adjusted_price'),
                                'spu_market_price': product.get('cost_price'),
                                'sku_count': product.get('sku_count') or 0,
                                'task_nos': task_nos,
                            })

                    if callable(progress_callback):
                        progress_callback(
                            processed_count,
                            total,
                            success_count,
                            fail_count,
                            f'已处理 {processed_count}/{total}，成功 {success_count}，失败 {fail_count}'
                        )
                except Exception as exc:
                    conn.rollback()
                    failed_batch_count += 1
                    logger.exception('[Synced Adjusted Price] batch failed')
                    batch_fail_items = adjusted_products or [
                        {'id': row.get('id'), 'offer_id': row.get('offer_id'), 'title': row.get('title') or '', 'sku_count': 0}
                        for row in batch_rows
                    ]
                    for item in batch_fail_items:
                        fail_count += 1
                        self._append_jsonl(failure_handle, failed_details, {
                            'id': item.get('id'),
                            'offer_id': item.get('offer_id'),
                            'title': item.get('title') or '',
                            'reason': str(exc),
                            'batch_no': batch_count,
                        })
                    if callable(progress_callback):
                        progress_callback(
                            processed_count,
                            total,
                            success_count,
                            fail_count,
                            f'第 {batch_count} 批处理失败，已继续后续批次'
                        )
                finally:
                    conn.close()

            success_handle.flush()
            failure_handle.flush()
            has_failed = fail_count > 0
            return {
                'success': not has_failed,
                'partial_success': success_count > 0 and has_failed,
                'job_id': job_id,
                'backup_job_id': job_id,
                'message': '批量调已同步商品运费完成' if not has_failed else '批量调已同步商品运费完成，存在失败记录',
                'total': total,
                'processed_count': processed_count,
                'success_count': success_count,
                'fail_count': fail_count,
                'batch_count': batch_count,
                'failed_batch_count': failed_batch_count,
                'retry_count': retry_count,
                'success_details': success_details,
                'failed_details': failed_details,
                'success_log_id': job_id if success_count else '',
                'failure_log_id': job_id if fail_count else '',
                'success_log_truncated': success_count > len(success_details),
                'failure_log_truncated': fail_count > len(failed_details),
                'execution_time_ms': int((time.time() - started_at) * 1000),
            }
        except Exception as exc:
            logger.exception('[Synced Adjusted Price] task failed')
            return {
                'success': False,
                'job_id': job_id,
                'backup_job_id': job_id,
                'total': total,
                'processed_count': processed_count,
                'success_count': success_count,
                'fail_count': max(fail_count, total - success_count),
                'batch_count': batch_count,
                'failed_batch_count': failed_batch_count,
                'retry_count': retry_count,
                'success_details': success_details,
                'failed_details': failed_details,
                'success_log_id': job_id if success_count else '',
                'failure_log_id': job_id if fail_count or total > success_count else '',
                'error': str(exc),
                'execution_time_ms': int((time.time() - started_at) * 1000),
            }
        finally:
            try:
                success_handle.close()
            finally:
                failure_handle.close()

    def export_success_log_csv(self, job_id):
        path = self._success_log_path(job_id)
        if not os.path.exists(path):
            return None

        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                'id', 'offer_id', 'title', 'batch_no',
                'spu_adjusted_price', 'spu_market_price', 'sku_count', 'task_nos'
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
                    'batch_no': item.get('batch_no', ''),
                    'spu_adjusted_price': item.get('spu_adjusted_price', ''),
                    'spu_market_price': item.get('spu_market_price', ''),
                    'sku_count': item.get('sku_count', ''),
                    'task_nos': ','.join(item.get('task_nos') or []),
                })
        return '\ufeff' + output.getvalue()

    def export_failure_log_csv(self, job_id):
        path = self._failure_log_path(job_id)
        if not os.path.exists(path):
            return None

        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=['id', 'offer_id', 'title', 'batch_no', 'reason', 'spu_count', 'sku_count', 'erp_response']
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
                    'batch_no': item.get('batch_no', ''),
                    'reason': item.get('reason', ''),
                    'spu_count': item.get('spu_count', ''),
                    'sku_count': item.get('sku_count', ''),
                    'erp_response': json.dumps(item.get('erp_response', ''), ensure_ascii=False, default=str),
                })
        return '\ufeff' + output.getvalue()


synced_adjusted_price_service = SyncedAdjustedPriceService()
