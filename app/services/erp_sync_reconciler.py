# -*- coding: utf-8 -*-
"""
ErpSyncReconciler - ERP 批量同步任务后台对账

职责：
1. 定期扫描 `erp_sync_tasks` 里未完成（finalized=0）的任务
2. 调用 ERP `batch-create/progress` 接口拉取最新状态
3. 如果 access_token 过期，使用保存的 refresh_token 刷新后重试
4. 拿到 taskStatus 2/3/4 时调用 apply_task_progress_to_local 回写本地状态
5. 超过最大年龄（默认 2 小时）仍未落定的任务自动标记 finalized 并写入过期原因

设计要点：
- 仅读写数据库，不依赖 Flask session / request 上下文
- 单例 + 守护线程，create_app 时启动
"""

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from app.services.erp_sync_task_store import erp_sync_task_store
from app.services.erp_sync_progress_applier import apply_task_progress_to_local

logger = logging.getLogger(__name__)

DEFAULT_SCAN_INTERVAL_SECONDS = 60
DEFAULT_MIN_RECONCILE_INTERVAL_SECONDS = 30
DEFAULT_MAX_TASK_AGE_SECONDS = 2 * 60 * 60
DEFAULT_MAX_TASKS_PER_SCAN = 30


class ErpSyncReconciler:

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._started = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # 单个任务对账                                                          #
    # ------------------------------------------------------------------ #

    def reconcile_task(
        self,
        task: Dict[str, Any],
        allow_token_refresh: bool = True,
    ) -> Dict[str, Any]:
        """对账单个任务，返回 {finalized, task_status, skipped, error}"""
        task_no = str((task or {}).get('task_no') or '').strip()
        if not task_no:
            return {'finalized': False, 'skipped': True, 'error': 'missing task_no'}

        access_token = str(task.get('access_token') or '').strip()
        refresh_token = str(task.get('refresh_token') or '').strip()
        tenant_id = str(task.get('tenant_id') or '').strip()
        source = str(task.get('source') or 'ALIBABA_1688').strip() or 'ALIBABA_1688'

        from app.services.erp_sync_service import erp_sync_service

        if not access_token and refresh_token and allow_token_refresh:
            access_token = self._try_refresh_token(task_no, refresh_token, access_token, tenant_id) or ''

        progress_result = erp_sync_service.query_batch_create_progress(
            task_no, access_token=access_token, tenant_id=tenant_id
        )
        if not progress_result.get('success'):
            error_msg = str(progress_result.get('error') or 'query progress failed')[:500]
            # 401/403 → 尝试刷新 token 再查一次
            if allow_token_refresh and self._looks_like_auth_error(error_msg) and refresh_token:
                refreshed = self._try_refresh_token(task_no, refresh_token, access_token, tenant_id)
                if refreshed:
                    return self.reconcile_task({**task, 'access_token': refreshed}, allow_token_refresh=False)
            erp_sync_task_store.update_reconcile_meta(
                task_no,
                last_task_status_desc='',
                reconcile_error=error_msg,
            )
            return {'finalized': False, 'skipped': True, 'error': error_msg}

        apply_result = apply_task_progress_to_local(
            task_context=task,
            progress_result=progress_result,
            source=source,
            persist_finalized=True,
        )
        return {
            'finalized': bool(apply_result.get('finalized')),
            'skipped': bool(apply_result.get('skipped')),
            'task_status': apply_result.get('task_status'),
            'task_status_desc': apply_result.get('task_status_desc'),
            'success_offer_ids': apply_result.get('success_offer_ids') or [],
            'failed_offer_ids': apply_result.get('failed_offer_ids') or [],
            'error': '',
        }

    def _looks_like_auth_error(self, error_msg: str) -> bool:
        lowered = str(error_msg or '').lower()
        if not lowered:
            return False
        if '401' in lowered or '403' in lowered:
            return True
        for keyword in ('unauthorized', 'token', 'login', 'expired'):
            if keyword in lowered:
                return True
        for keyword in ('登录', '过期', '失效', '未登录', '权限'):
            if keyword in error_msg:
                return True
        return False

    def _try_refresh_token(
        self,
        task_no: str,
        refresh_token: str,
        access_token: str,
        tenant_id: str,
    ) -> Optional[str]:
        try:
            from app.routes.auth import refresh_access_token_standalone
        except Exception as exc:
            logger.warning('refresh_access_token_standalone import failed: %s', exc)
            return None
        try:
            success, token_data, err = refresh_access_token_standalone(
                refresh_token=refresh_token,
                access_token=access_token,
                tenant_id=tenant_id,
            )
        except Exception as exc:
            logger.exception('standalone refresh failed task_no=%s: %s', task_no, exc)
            return None
        if not success:
            erp_sync_task_store.update_reconcile_meta(
                task_no,
                reconcile_error=f'token refresh failed: {err}'[:500],
            )
            return None
        new_access = str(token_data.get('accessToken') or '').strip()
        new_refresh = str(token_data.get('refreshToken') or refresh_token).strip()
        expire_time = int(token_data.get('expiresTime') or 0)
        if new_access:
            try:
                erp_sync_task_store.update_token(task_no, new_access, new_refresh, expire_time)
            except Exception as exc:
                logger.exception('update_token failed task_no=%s: %s', task_no, exc)
        return new_access or None

    # ------------------------------------------------------------------ #
    # 批量扫描                                                              #
    # ------------------------------------------------------------------ #

    def reconcile_pending_tasks(
        self,
        max_age_seconds: int = DEFAULT_MAX_TASK_AGE_SECONDS,
        min_reconcile_interval: int = DEFAULT_MIN_RECONCILE_INTERVAL_SECONDS,
        limit: int = DEFAULT_MAX_TASKS_PER_SCAN,
    ) -> Dict[str, Any]:
        tasks = erp_sync_task_store.list_unfinalized_tasks(
            max_age_seconds=max_age_seconds,
            min_age_seconds=min_reconcile_interval,
            limit=limit,
        )
        processed = 0
        finalized = 0
        errors = 0
        for task in tasks:
            processed += 1
            try:
                outcome = self.reconcile_task(task)
                if outcome.get('finalized'):
                    finalized += 1
                elif outcome.get('error'):
                    errors += 1
            except Exception as exc:
                errors += 1
                logger.exception('reconcile_task raised for task_no=%s: %s', task.get('task_no'), exc)

        stale_count = 0
        try:
            stale_count = erp_sync_task_store.auto_expire_stale_tasks(max_age_seconds=max_age_seconds)
        except Exception as exc:
            logger.exception('auto_expire_stale_tasks failed: %s', exc)

        return {
            'processed': processed,
            'finalized': finalized,
            'errors': errors,
            'expired': stale_count,
        }

    # ------------------------------------------------------------------ #
    # 守护线程                                                              #
    # ------------------------------------------------------------------ #

    def start_background_thread(
        self,
        scan_interval_seconds: int = DEFAULT_SCAN_INTERVAL_SECONDS,
    ) -> None:
        with self._lock:
            if self._started:
                return
            self._stop_event.clear()
            thread = threading.Thread(
                target=self._run_loop,
                kwargs={'scan_interval_seconds': scan_interval_seconds},
                name='erp-sync-reconciler',
                daemon=True,
            )
            thread.start()
            self._thread = thread
            self._started = True
            logger.info('ERP sync reconciler thread started (interval=%ss)', scan_interval_seconds)

    def stop(self, timeout: Optional[float] = 5.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        with self._lock:
            self._started = False
            self._thread = None

    def _run_loop(self, scan_interval_seconds: int) -> None:
        # 启动时稍微等待，避免与应用初始化争抢连接池
        if self._stop_event.wait(5):
            return
        while not self._stop_event.is_set():
            try:
                summary = self.reconcile_pending_tasks()
                if summary.get('processed'):
                    logger.info(
                        'ERP sync reconciler scan: processed=%s finalized=%s errors=%s expired=%s',
                        summary.get('processed'),
                        summary.get('finalized'),
                        summary.get('errors'),
                        summary.get('expired'),
                    )
            except Exception as exc:
                logger.exception('ERP sync reconciler scan failed: %s', exc)
            if self._stop_event.wait(scan_interval_seconds):
                return


erp_sync_reconciler = ErpSyncReconciler()
