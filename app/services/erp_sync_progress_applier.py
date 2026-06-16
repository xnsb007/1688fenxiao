# -*- coding: utf-8 -*-
"""
ErpSyncProgressApplier - 将 ERP `batch-create/progress` 的结果落库到本地

为前端轮询接口、手动对账接口、后台 reconciler 提供统一的状态同步函数，
避免三处重复实现 task_status 2/3/4 的分支逻辑。
"""

import logging
from typing import Any, Dict, List, Optional

from app.services.erp_sync_task_store import erp_sync_task_store
from app.services.product_service import product_service

logger = logging.getLogger(__name__)


def extract_failed_offer_ids_by_details(failed_details, offer_ids) -> List[str]:
    offer_ids_str = [str(x) for x in (offer_ids or [])]
    offer_id_set = set(offer_ids_str)
    failed_offer_ids = set()
    for item in (failed_details or []):
        if not isinstance(item, dict):
            continue
        index = item.get('index')
        if isinstance(index, int) and 0 <= index < len(offer_ids_str):
            failed_offer_ids.add(offer_ids_str[index])
            continue
        spu_id = item.get('spuId') or item.get('offer_id') or item.get('spu_id')
        spu_id_str = str(spu_id) if spu_id is not None else ''
        if spu_id_str and spu_id_str in offer_id_set:
            failed_offer_ids.add(spu_id_str)
    return list(failed_offer_ids)


def extract_offer_reason_map_by_details(failed_details, offer_ids, fallback_reason='') -> Dict[str, str]:
    offer_ids_str = [str(x) for x in (offer_ids or [])]
    offer_id_set = set(offer_ids_str)
    reason_map: Dict[str, str] = {}
    for item in (failed_details or []):
        if not isinstance(item, dict):
            continue
        reason = str(item.get('reason') or item.get('message') or fallback_reason or '').strip()
        if not reason:
            continue
        index = item.get('index')
        if isinstance(index, int) and 0 <= index < len(offer_ids_str):
            reason_map[offer_ids_str[index]] = reason
            continue
        spu_id = item.get('spuId') or item.get('offer_id') or item.get('spu_id')
        spu_id_str = str(spu_id or '').strip()
        if spu_id_str and spu_id_str in offer_id_set:
            reason_map[spu_id_str] = reason

    if fallback_reason:
        for offer_id in extract_failed_offer_ids_by_details(failed_details, offer_ids):
            reason_map.setdefault(str(offer_id), str(fallback_reason))
    return reason_map


def update_failed_sync_status_by_details(failed_details, offer_ids, fallback_reason='', source='ALIBABA_1688') -> List[str]:
    reason_map = extract_offer_reason_map_by_details(failed_details, offer_ids, fallback_reason=fallback_reason)
    if reason_map:
        product_service.batch_update_sync_status_with_reasons(reason_map, 'failed', source=source)
        return list(reason_map.keys())

    failed_offer_ids = extract_failed_offer_ids_by_details(failed_details, offer_ids)
    if failed_offer_ids:
        product_service.batch_update_sync_status(failed_offer_ids, 'failed', fallback_reason, source=source)
    return failed_offer_ids


def apply_task_progress_to_local(
    task_context: Dict[str, Any],
    progress_result: Dict[str, Any],
    source: str = 'ALIBABA_1688',
    persist_finalized: bool = True,
) -> Dict[str, Any]:
    """当 progress_result 返回 task_status ∈ {2,3,4} 时，把结果写回 import_product 并标记任务 finalized。

    - 处理中（0/1）时仅回写 reconcile 元数据，返回 finalized=False
    - persist_finalized=False 时调用方自行决定是否 finalize（前端轮询场景需要）
    """
    task_no = str((task_context or {}).get('task_no') or '').strip()
    offer_ids = list((task_context or {}).get('offer_ids') or [])
    source = str(source or (task_context or {}).get('source') or 'ALIBABA_1688').strip() or 'ALIBABA_1688'
    already_finalized = bool((task_context or {}).get('finalized'))

    task_status = progress_result.get('task_status')
    task_status_desc = str(progress_result.get('task_status_desc') or '').strip()
    failed_details = progress_result.get('failed_details') or []
    immediate_failed_offer_ids = set(str(x) for x in (task_context or {}).get('immediate_failed_offer_ids') or [])

    result = {
        'finalized': already_finalized,
        'task_status': task_status,
        'task_status_desc': task_status_desc,
        'success_offer_ids': [],
        'failed_offer_ids': [],
        'skipped': False,
    }

    if task_status not in (2, 3, 4):
        if task_no:
            try:
                erp_sync_task_store.update_reconcile_meta(
                    task_no,
                    last_task_status=task_status if isinstance(task_status, int) else None,
                    last_task_status_desc=task_status_desc,
                )
            except Exception as exc:
                logger.exception('update_reconcile_meta failed for task_no=%s: %s', task_no, exc)
        result['skipped'] = True
        return result

    if already_finalized:
        return result

    failed_offer_ids = set(extract_failed_offer_ids_by_details(failed_details, offer_ids))
    failed_offer_ids.update(immediate_failed_offer_ids)

    success_offer_ids: List[str] = []
    applied_failed_ids: List[str] = []

    try:
        if task_status == 2:
            success_offer_ids = [oid for oid in offer_ids if oid not in failed_offer_ids]
            if success_offer_ids:
                product_service.batch_update_sync_status(success_offer_ids, 'synced', source=source)
        elif task_status == 3:
            failed_set = set(str(x) for x in failed_offer_ids)
            success_offer_ids = [oid for oid in offer_ids if str(oid) not in failed_set]
            if success_offer_ids:
                product_service.batch_update_sync_status(success_offer_ids, 'synced', source=source)
            if failed_offer_ids:
                applied_failed_ids = update_failed_sync_status_by_details(
                    failed_details,
                    offer_ids,
                    fallback_reason=task_status_desc or '同步失败',
                    source=source,
                )
        elif task_status == 4:
            if offer_ids:
                if failed_details:
                    applied_failed_ids = update_failed_sync_status_by_details(
                        failed_details,
                        offer_ids,
                        fallback_reason=task_status_desc or '同步失败',
                        source=source,
                    )
                    missing_failed = [
                        oid for oid in offer_ids if str(oid) not in set(str(x) for x in applied_failed_ids)
                    ]
                    if missing_failed:
                        product_service.batch_update_sync_status(
                            missing_failed,
                            'failed',
                            task_status_desc or '同步失败',
                            source=source,
                        )
                        applied_failed_ids = list(set(applied_failed_ids) | set(missing_failed))
                else:
                    product_service.batch_update_sync_status(
                        offer_ids,
                        'failed',
                        task_status_desc or '同步失败',
                        source=source,
                    )
                    applied_failed_ids = list(offer_ids)
    except Exception as exc:
        logger.exception(
            'apply_task_progress_to_local failed task_no=%s status=%s: %s',
            task_no, task_status, exc,
        )
        if task_no:
            try:
                erp_sync_task_store.update_reconcile_meta(
                    task_no,
                    last_task_status=task_status if isinstance(task_status, int) else None,
                    last_task_status_desc=task_status_desc,
                    reconcile_error=str(exc)[:500],
                )
            except Exception:
                pass
        raise

    if persist_finalized and task_no:
        try:
            erp_sync_task_store.mark_finalized(
                task_no,
                last_task_status=task_status if isinstance(task_status, int) else None,
                last_task_status_desc=task_status_desc,
            )
        except Exception as exc:
            logger.exception('mark_finalized failed for task_no=%s: %s', task_no, exc)

    result['finalized'] = True
    result['success_offer_ids'] = success_offer_ids
    result['failed_offer_ids'] = list(failed_offer_ids | set(applied_failed_ids))
    return result
