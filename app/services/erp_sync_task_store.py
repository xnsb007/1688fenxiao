# -*- coding: utf-8 -*-
"""
ErpSyncTaskStore - ERP 批量同步任务上下文持久化

替换原先进程内存字典 `_ERP_SYNC_TASKS`，将 task_no、offer_ids、token 快照等
持久化到 `erp_sync_tasks` 表。供前端轮询、手动对账、后台 reconciler 共享。
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

from app.models import get_db

logger = logging.getLogger(__name__)


def _to_str_list(values) -> List[str]:
    if not values:
        return []
    result = []
    for item in values:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _parse_json_list(raw) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return _to_str_list(raw)
    text = str(raw).strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except Exception:
        return []
    if isinstance(data, list):
        return _to_str_list(data)
    return []


def _row_to_dict(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not row:
        return {}
    return {
        'id': row.get('id'),
        'task_no': str(row.get('task_no') or '').strip(),
        'source': str(row.get('source') or 'ALIBABA_1688').strip() or 'ALIBABA_1688',
        'username': str(row.get('username') or '').strip(),
        'tenant_id': str(row.get('tenant_id') or '').strip(),
        'access_token': str(row.get('access_token') or '').strip(),
        'refresh_token': str(row.get('refresh_token') or '').strip(),
        'expire_time': int(row.get('expire_time') or 0),
        'offer_ids': _parse_json_list(row.get('offer_ids_json')),
        'immediate_failed_offer_ids': _parse_json_list(row.get('immediate_failed_offer_ids_json')),
        'finalized': bool(row.get('finalized')),
        'finalized_at': row.get('finalized_at'),
        'last_task_status': row.get('last_task_status'),
        'last_task_status_desc': str(row.get('last_task_status_desc') or ''),
        'reconcile_error': str(row.get('reconcile_error') or ''),
        'reconcile_attempt_count': int(row.get('reconcile_attempt_count') or 0),
        'last_reconcile_at': row.get('last_reconcile_at'),
        'created_at': row.get('created_at'),
        'updated_at': row.get('updated_at'),
    }


class ErpSyncTaskStore:
    """基于 erp_sync_tasks 表的任务上下文仓储。"""

    def register_task(
        self,
        task_no: str,
        offer_ids: List[str],
        source: str = 'ALIBABA_1688',
        immediate_failed_offer_ids: Optional[List[str]] = None,
        access_token: str = '',
        refresh_token: str = '',
        tenant_id: str = '',
        expire_time: int = 0,
        username: str = '',
    ) -> bool:
        task_no = str(task_no or '').strip()
        if not task_no:
            return False
        payload = (
            task_no,
            (source or 'ALIBABA_1688').strip() or 'ALIBABA_1688',
            (username or '').strip(),
            (tenant_id or '').strip(),
            (access_token or '').strip(),
            (refresh_token or '').strip(),
            int(expire_time or 0),
            json.dumps(_to_str_list(offer_ids), ensure_ascii=False),
            json.dumps(_to_str_list(immediate_failed_offer_ids), ensure_ascii=False),
        )
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''
                    INSERT INTO erp_sync_tasks
                        (task_no, source, username, tenant_id, access_token, refresh_token,
                         expire_time, offer_ids_json, immediate_failed_offer_ids_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        source = VALUES(source),
                        username = VALUES(username),
                        tenant_id = VALUES(tenant_id),
                        access_token = VALUES(access_token),
                        refresh_token = VALUES(refresh_token),
                        expire_time = VALUES(expire_time),
                        offer_ids_json = VALUES(offer_ids_json),
                        immediate_failed_offer_ids_json = VALUES(immediate_failed_offer_ids_json),
                        finalized = 0,
                        finalized_at = NULL,
                        reconcile_error = '',
                        updated_at = CURRENT_TIMESTAMP
                ''',
                payload,
            )
            conn.commit()
            return True
        except Exception as exc:
            logger.exception('register_task failed for task_no=%s: %s', task_no, exc)
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            conn.close()

    def get_task(self, task_no: str) -> Dict[str, Any]:
        task_no = str(task_no or '').strip()
        if not task_no:
            return {}
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                'SELECT * FROM erp_sync_tasks WHERE task_no = %s LIMIT 1',
                (task_no,),
            )
            row = cursor.fetchone()
            return _row_to_dict(row) if row else {}
        except Exception as exc:
            logger.exception('get_task failed for task_no=%s: %s', task_no, exc)
            return {}
        finally:
            conn.close()

    def mark_finalized(
        self,
        task_no: str,
        last_task_status: Optional[int] = None,
        last_task_status_desc: str = '',
        reconcile_error: str = '',
    ) -> bool:
        task_no = str(task_no or '').strip()
        if not task_no:
            return False
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''
                    UPDATE erp_sync_tasks
                    SET finalized = 1,
                        finalized_at = CURRENT_TIMESTAMP,
                        last_task_status = COALESCE(%s, last_task_status),
                        last_task_status_desc = %s,
                        reconcile_error = %s,
                        last_reconcile_at = CURRENT_TIMESTAMP
                    WHERE task_no = %s
                ''',
                (
                    last_task_status,
                    str(last_task_status_desc or '')[:255],
                    str(reconcile_error or '')[:500],
                    task_no,
                ),
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception as exc:
            logger.exception('mark_finalized failed for task_no=%s: %s', task_no, exc)
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            conn.close()

    def update_reconcile_meta(
        self,
        task_no: str,
        last_task_status: Optional[int] = None,
        last_task_status_desc: str = '',
        reconcile_error: str = '',
        increment_attempt: bool = True,
    ) -> bool:
        task_no = str(task_no or '').strip()
        if not task_no:
            return False
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''
                    UPDATE erp_sync_tasks
                    SET last_task_status = COALESCE(%s, last_task_status),
                        last_task_status_desc = %s,
                        reconcile_error = %s,
                        reconcile_attempt_count = reconcile_attempt_count + %s,
                        last_reconcile_at = CURRENT_TIMESTAMP
                    WHERE task_no = %s
                ''',
                (
                    last_task_status,
                    str(last_task_status_desc or '')[:255],
                    str(reconcile_error or '')[:500],
                    1 if increment_attempt else 0,
                    task_no,
                ),
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception as exc:
            logger.exception('update_reconcile_meta failed for task_no=%s: %s', task_no, exc)
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            conn.close()

    def update_token(
        self,
        task_no: str,
        access_token: str,
        refresh_token: str = '',
        expire_time: int = 0,
    ) -> bool:
        task_no = str(task_no or '').strip()
        if not task_no:
            return False
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''
                    UPDATE erp_sync_tasks
                    SET access_token = %s,
                        refresh_token = CASE WHEN %s <> '' THEN %s ELSE refresh_token END,
                        expire_time = CASE WHEN %s > 0 THEN %s ELSE expire_time END
                    WHERE task_no = %s
                ''',
                (
                    (access_token or '').strip(),
                    (refresh_token or '').strip(),
                    (refresh_token or '').strip(),
                    int(expire_time or 0),
                    int(expire_time or 0),
                    task_no,
                ),
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception as exc:
            logger.exception('update_token failed for task_no=%s: %s', task_no, exc)
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            conn.close()

    def list_unfinalized_tasks(
        self,
        max_age_seconds: int = 7200,
        min_age_seconds: int = 0,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''
                    SELECT *
                    FROM erp_sync_tasks
                    WHERE finalized = 0
                      AND created_at >= (NOW() - INTERVAL %s SECOND)
                      AND (last_reconcile_at IS NULL OR last_reconcile_at <= (NOW() - INTERVAL %s SECOND))
                    ORDER BY created_at ASC
                    LIMIT %s
                ''',
                (
                    int(max_age_seconds or 0),
                    int(min_age_seconds or 0),
                    int(limit or 50),
                ),
            )
            rows = cursor.fetchall() or []
            return [_row_to_dict(row) for row in rows]
        except Exception as exc:
            logger.exception('list_unfinalized_tasks failed: %s', exc)
            return []
        finally:
            conn.close()

    def auto_expire_stale_tasks(self, max_age_seconds: int = 7200) -> int:
        """把超过 max_age 仍未 finalize 的任务标记为已过期，避免 reconciler 无限重试。"""
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''
                    UPDATE erp_sync_tasks
                    SET finalized = 1,
                        finalized_at = CURRENT_TIMESTAMP,
                        reconcile_error = CASE
                            WHEN reconcile_error = '' THEN 'reconcile timeout: task exceeded max age'
                            ELSE reconcile_error
                        END
                    WHERE finalized = 0
                      AND created_at < (NOW() - INTERVAL %s SECOND)
                ''',
                (int(max_age_seconds or 0),),
            )
            conn.commit()
            return cursor.rowcount or 0
        except Exception as exc:
            logger.exception('auto_expire_stale_tasks failed: %s', exc)
            try:
                conn.rollback()
            except Exception:
                pass
            return 0
        finally:
            conn.close()

    def find_tasks_by_offer_ids(
        self,
        offer_ids: List[str],
        source: str = 'ALIBABA_1688',
        include_finalized: bool = False,
        lookback_seconds: int = 86400,
    ) -> List[Dict[str, Any]]:
        offer_id_set = set(str(x).strip() for x in (offer_ids or []) if str(x).strip())
        if not offer_id_set:
            return []
        conn = get_db()
        cursor = conn.cursor()
        try:
            params = [int(lookback_seconds or 0)]
            where_parts = ['created_at >= (NOW() - INTERVAL %s SECOND)']
            if source:
                where_parts.append('source = %s')
                params.append(source)
            if not include_finalized:
                where_parts.append('finalized = 0')
            cursor.execute(
                f'''
                    SELECT *
                    FROM erp_sync_tasks
                    WHERE {' AND '.join(where_parts)}
                    ORDER BY created_at DESC
                    LIMIT 200
                ''',
                params,
            )
            rows = cursor.fetchall() or []
        except Exception as exc:
            logger.exception('find_tasks_by_offer_ids failed: %s', exc)
            return []
        finally:
            conn.close()

        matched = []
        for row in rows:
            task = _row_to_dict(row)
            task_offer_ids = set(task.get('offer_ids') or [])
            if task_offer_ids & offer_id_set:
                matched.append(task)
        return matched


erp_sync_task_store = ErpSyncTaskStore()
