# -*- coding: utf-8 -*-
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.config import SOURCE_TYPE
from app.models import get_db
from app.services.ali1688_service import ali1688_service
from app.services.task_log_service import task_log_service
from app.utils.time_utils import get_app_timezone, get_current_app_datetime

logger = logging.getLogger(__name__)

FOLLOW_NOT_FOLLOWED = 'not_followed'
FOLLOW_FOLLOWING = 'following'
FOLLOW_FOLLOWED = 'followed'
FOLLOW_FAILED = 'failed'

FOLLOW_LOCK_NAME = 'ali1688_product_follow_job'
DEFAULT_LIMIT = 500
DEFAULT_MAX_RETRIES = 3
DEFAULT_QPS = 1.0


class Ali1688ProductFollowService:
    def _build_date_window(self, date_text: Optional[str] = None) -> Tuple[datetime, datetime, str]:
        tz = get_app_timezone()
        if date_text:
            day = datetime.strptime(str(date_text).strip(), '%Y-%m-%d').date()
            start = datetime(day.year, day.month, day.day, tzinfo=tz)
        else:
            now = get_current_app_datetime()
            start = datetime(now.year, now.month, now.day, tzinfo=tz)
        end = start + timedelta(days=1)
        return start.replace(tzinfo=None), end.replace(tzinfo=None), start.strftime('%Y-%m-%d')

    def acquire_lock(self) -> bool:
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute('SELECT GET_LOCK(%s, 0) AS locked', (FOLLOW_LOCK_NAME,))
            row = cursor.fetchone() or {}
            return int(row.get('locked') or 0) == 1
        finally:
            conn.close()

    def release_lock(self) -> None:
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute('SELECT RELEASE_LOCK(%s)', (FOLLOW_LOCK_NAME,))
        finally:
            conn.close()

    def list_candidates(
        self,
        date_text: Optional[str] = None,
        retry_failed_only: bool = False,
        limit: int = DEFAULT_LIMIT,
        source: str = SOURCE_TYPE,
    ) -> Dict[str, Any]:
        start, end, normalized_date = self._build_date_window(date_text)
        statuses = [FOLLOW_FAILED] if retry_failed_only else [FOLLOW_NOT_FOLLOWED, FOLLOW_FAILED]
        placeholders = ','.join(['%s' for _ in statuses])
        limit_value = max(1, int(limit or DEFAULT_LIMIT))

        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f'''
                    SELECT id, offer_id, source_type, sync_status, sync_at,
                           COALESCE(follow_status, %s) AS follow_status,
                           follow_retry_count, last_follow_attempt_at
                    FROM import_product
                    WHERE source_type = %s
                      AND sync_status = %s
                      AND sync_at >= %s
                      AND sync_at < %s
                      AND COALESCE(follow_status, %s) IN ({placeholders})
                    ORDER BY sync_at ASC, id ASC
                    LIMIT %s
                ''',
                [
                    FOLLOW_NOT_FOLLOWED,
                    source,
                    'synced',
                    start,
                    end,
                    FOLLOW_NOT_FOLLOWED,
                    *statuses,
                    limit_value,
                ],
            )
            candidates = [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

        return {
            'success': True,
            'date': normalized_date,
            'start': start.strftime('%Y-%m-%d %H:%M:%S'),
            'end': end.strftime('%Y-%m-%d %H:%M:%S'),
            'total': len(candidates),
            'candidates': candidates,
        }

    def run_daily_follow(
        self,
        date_text: Optional[str] = None,
        retry_failed_only: bool = False,
        dry_run: bool = False,
        limit: int = DEFAULT_LIMIT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        qps: float = DEFAULT_QPS,
        source: str = SOURCE_TYPE,
    ) -> Dict[str, Any]:
        started = time.time()
        locked = False
        summary = {
            'success': True,
            'dry_run': bool(dry_run),
            'date': '',
            'total': 0,
            'followed': 0,
            'failed': 0,
            'skipped': 0,
            'lock_skipped': False,
            'details': [],
        }

        if not dry_run:
            locked = self.acquire_lock()
            if not locked:
                summary['lock_skipped'] = True
                summary['message'] = 'another follow job is running'
                return summary

        try:
            candidate_result = self.list_candidates(
                date_text=date_text,
                retry_failed_only=retry_failed_only,
                limit=limit,
                source=source,
            )
            candidates = candidate_result.get('candidates') or []
            summary['date'] = candidate_result.get('date') or ''
            summary['total'] = len(candidates)

            if dry_run:
                summary['candidates'] = candidates
                return summary

            interval = 1.0 / max(float(qps or DEFAULT_QPS), 0.1)
            for index, product in enumerate(candidates):
                if index > 0:
                    time.sleep(interval)
                detail = self.follow_one_product(product, max_retries=max_retries)
                summary['details'].append(detail)
                if detail.get('success'):
                    summary['followed'] += 1
                elif detail.get('skipped'):
                    summary['skipped'] += 1
                else:
                    summary['failed'] += 1

            summary['success'] = summary['failed'] == 0
            return summary
        finally:
            summary['execution_time_ms'] = int((time.time() - started) * 1000)
            if locked:
                self.release_lock()
            if not dry_run:
                self._log_task_summary(summary)

    def follow_one_product(self, product: Dict[str, Any], max_retries: int = DEFAULT_MAX_RETRIES) -> Dict[str, Any]:
        product_id = str(product.get('offer_id') or '').strip()
        row_id = product.get('id')
        if not product_id or not row_id:
            return {'success': False, 'offer_id': product_id, 'error': 'missing product id or row id'}

        current_retry_count = int(product.get('follow_retry_count') or 0)
        first_attempt_no = current_retry_count + 1
        if not self._mark_following(row_id):
            return {'success': False, 'skipped': True, 'offer_id': product_id, 'error': 'row is not claimable'}

        final_result = None
        attempt_no = first_attempt_no
        max_attempts = max(1, int(max_retries or DEFAULT_MAX_RETRIES))
        for attempt_index in range(1, max_attempts + 1):
            attempt_no = current_retry_count + attempt_index
            result = ali1688_service.follow_product(product_id)
            status = FOLLOW_FOLLOWED if result.get('success') else FOLLOW_FAILED
            self.record_follow_log(product_id, product_id, status, attempt_no, result)
            final_result = result
            if result.get('success'):
                self._mark_followed(row_id, product_id)
                return {
                    'success': True,
                    'offer_id': product_id,
                    'already_followed': bool(result.get('already_followed')),
                    'attempt_no': attempt_no,
                }
            if attempt_index < max_attempts and self._is_retryable(result):
                time.sleep(min(8, 2 ** (attempt_index - 1)))
                continue
            break

        error_message = str((final_result or {}).get('error_message') or 'follow product failed')[:2000]
        error_code = str((final_result or {}).get('error_code') or 'FOLLOW_FAILED')[:128]
        self._mark_failed(row_id, attempt_no, error_message)
        return {
            'success': False,
            'offer_id': product_id,
            'attempt_no': attempt_no,
            'error_code': error_code,
            'error_message': error_message,
        }

    def _mark_following(self, row_id: int) -> bool:
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''
                    UPDATE import_product
                    SET follow_status = %s,
                        last_follow_attempt_at = CURRENT_TIMESTAMP,
                        follow_error = NULL
                    WHERE id = %s
                      AND COALESCE(follow_status, %s) IN (%s, %s)
                ''',
                (FOLLOW_FOLLOWING, row_id, FOLLOW_NOT_FOLLOWED, FOLLOW_NOT_FOLLOWED, FOLLOW_FAILED),
            )
            affected = cursor.rowcount
            conn.commit()
            return affected > 0
        finally:
            conn.close()

    def _mark_followed(self, row_id: int, product_id: str) -> None:
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''
                    UPDATE import_product
                    SET follow_status = %s,
                        follow_at = CURRENT_TIMESTAMP,
                        follow_error = NULL
                    WHERE id = %s AND offer_id = %s
                ''',
                (FOLLOW_FOLLOWED, row_id, product_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _mark_failed(self, row_id: int, attempt_no: int, error_message: str) -> None:
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''
                    UPDATE import_product
                    SET follow_status = %s,
                        follow_error = %s,
                        follow_retry_count = %s
                    WHERE id = %s
                ''',
                (FOLLOW_FAILED, error_message[:2000], int(attempt_no), row_id),
            )
            conn.commit()
        finally:
            conn.close()

    def record_follow_log(
        self,
        offer_id: str,
        product_id: str,
        status: str,
        attempt_no: int,
        result: Dict[str, Any],
        source: str = SOURCE_TYPE,
    ) -> int:
        request_payload = {'productId': str(product_id or '')}
        response_payload = result.get('raw')
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''
                    INSERT INTO ali1688_product_follow_log
                    (offer_id, product_id, source_type, request_payload, response_payload,
                     status, error_code, error_message, attempt_no)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''',
                (
                    str(offer_id or ''),
                    str(product_id or ''),
                    source,
                    json.dumps(request_payload, ensure_ascii=False, sort_keys=True),
                    json.dumps(response_payload, ensure_ascii=False, default=str),
                    status,
                    str(result.get('error_code') or '')[:128],
                    str(result.get('error_message') or '')[:2000],
                    int(attempt_no or 1),
                ),
            )
            log_id = cursor.lastrowid
            conn.commit()
            return log_id
        finally:
            conn.close()

    def list_follow_logs(self, page: int = 1, page_size: int = 20, offer_id: str = '', status: str = '', source: str = SOURCE_TYPE) -> Dict[str, Any]:
        page = max(1, int(page or 1))
        page_size = max(1, min(100, int(page_size or 20)))
        offset = (page - 1) * page_size
        where = ['source_type = %s']
        params: List[Any] = [source]
        if offer_id:
            where.append('offer_id = %s')
            params.append(str(offer_id).strip())
        if status:
            where.append('status = %s')
            params.append(str(status).strip())
        where_sql = ' AND '.join(where)

        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(f'SELECT COUNT(*) AS total FROM ali1688_product_follow_log WHERE {where_sql}', params)
            total = int((cursor.fetchone() or {}).get('total') or 0)
            cursor.execute(
                f'''
                    SELECT id, offer_id, product_id, status, error_code, error_message,
                           attempt_no, created_at
                    FROM ali1688_product_follow_log
                    WHERE {where_sql}
                    ORDER BY id DESC
                    LIMIT %s OFFSET %s
                ''',
                params + [page_size, offset],
            )
            logs = [dict(row) for row in cursor.fetchall()]
            for row in logs:
                row['created_at'] = str(row.get('created_at') or '')
        finally:
            conn.close()

        return {'success': True, 'logs': logs, 'total': total, 'page': page, 'page_size': page_size}

    def get_follow_stats(self, source: str = SOURCE_TYPE) -> Dict[str, Any]:
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN COALESCE(follow_status, 'not_followed') = 'not_followed' THEN 1 ELSE 0 END) AS not_followed,
                        SUM(CASE WHEN follow_status = 'following' THEN 1 ELSE 0 END) AS following,
                        SUM(CASE WHEN follow_status = 'followed' THEN 1 ELSE 0 END) AS followed,
                        SUM(CASE WHEN follow_status = 'failed' THEN 1 ELSE 0 END) AS failed
                    FROM import_product
                    WHERE source_type = %s AND sync_status = 'synced'
                ''',
                (source,),
            )
            row = cursor.fetchone() or {}
        finally:
            conn.close()
        return {
            'success': True,
            'total': int(row.get('total') or 0),
            'not_followed': int(row.get('not_followed') or 0),
            'following': int(row.get('following') or 0),
            'followed': int(row.get('followed') or 0),
            'failed': int(row.get('failed') or 0),
        }

    def _is_retryable(self, result: Dict[str, Any]) -> bool:
        text = f"{result.get('error_code') or ''} {result.get('error_message') or ''}".lower()
        if not text:
            return False
        return any(keyword in text for keyword in ('timeout', 'timed out', '429', 'rate', 'limit', '5xx', '500', '502', '503', '504', 'server', 'system', 'request_error'))

    def _log_task_summary(self, summary: Dict[str, Any]) -> None:
        try:
            task_log_service.log(
                username='system',
                request_method='CRON',
                request_path='run_1688_follow_job',
                request_params={'date': summary.get('date'), 'dry_run': summary.get('dry_run')},
                response_data=summary,
                success=bool(summary.get('success')),
                error_message='' if summary.get('success') else f"{summary.get('failed', 0)} follow failures",
                ip_address='127.0.0.1',
                user_agent='run_1688_follow_job',
                execution_time=int(summary.get('execution_time_ms') or 0),
            )
        except Exception as exc:
            logger.warning('record follow task summary failed: %s', exc)


ali1688_product_follow_service = Ali1688ProductFollowService()
