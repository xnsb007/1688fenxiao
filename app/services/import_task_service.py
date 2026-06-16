# -*- coding: utf-8 -*-
"""
Persistent import task state management.
"""

import json
from datetime import datetime
from typing import Any, Dict, Optional

from app.models import get_db


class ImportTaskService:
    def create_task(
        self,
        task_id: str,
        filename: str,
        file_path: str,
        created_by: str = '',
        created_ip: str = '',
        batch_size: int = 0
    ) -> None:
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''
                    INSERT INTO import_job
                    (task_id, filename, file_path, status, progress, stage, batch_size, created_by, created_ip)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        filename = VALUES(filename),
                        file_path = VALUES(file_path),
                        status = VALUES(status),
                        progress = VALUES(progress),
                        stage = VALUES(stage),
                        batch_size = VALUES(batch_size),
                        created_by = VALUES(created_by),
                        created_ip = VALUES(created_ip),
                        result_json = NULL,
                        metrics_json = NULL,
                        cancel_requested = 0,
                        error_message = NULL,
                        finished_at = NULL
                ''',
                (task_id, filename, file_path, 'pending', 0, 'queued', int(batch_size or 0), created_by, created_ip)
            )
            conn.commit()
        finally:
            conn.close()

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute('SELECT * FROM import_job WHERE task_id = %s', (task_id,))
            row = cursor.fetchone()
        finally:
            conn.close()
        if not row:
            return None
        task = dict(row)
        for field in ('result_json', 'metrics_json'):
            raw_value = task.get(field)
            if raw_value:
                try:
                    task[field[:-5]] = json.loads(raw_value)
                except Exception:
                    task[field[:-5]] = {}
            else:
                task[field[:-5]] = {}
        return task

    def update_task(self, task_id: str, **fields: Any) -> None:
        if not task_id or not fields:
            return

        set_clauses = []
        values = []

        if 'result' in fields:
            fields['result_json'] = json.dumps(fields.pop('result') or {}, ensure_ascii=False, default=str)
        if 'metrics' in fields:
            fields['metrics_json'] = json.dumps(fields.pop('metrics') or {}, ensure_ascii=False, default=str)

        for key, value in fields.items():
            set_clauses.append(f'{key} = %s')
            values.append(value)

        if not set_clauses:
            return

        values.append(task_id)
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f'UPDATE import_job SET {", ".join(set_clauses)} WHERE task_id = %s',
                values
            )
            conn.commit()
        finally:
            conn.close()

    def mark_running(self, task_id: str, stage: str = 'running') -> None:
        self.update_task(
            task_id,
            status='running',
            stage=stage,
            started_at=datetime.now(),
            finished_at=None,
            error_message=None
        )

    def mark_finished(self, task_id: str, status: str, result: Optional[Dict[str, Any]] = None, metrics: Optional[Dict[str, Any]] = None, error_message: str = '') -> None:
        update_fields: Dict[str, Any] = {
            'status': status,
            'finished_at': datetime.now(),
            'error_message': error_message or None
        }
        if result is not None:
            update_fields['result'] = result
        if metrics is not None:
            update_fields['metrics'] = metrics
        self.update_task(task_id, **update_fields)

    def request_cancel(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if not task:
            return False
        self.update_task(task_id, cancel_requested=1, stage='cancel_requested')
        return True

    def clear_cancel(self, task_id: str) -> None:
        self.update_task(task_id, cancel_requested=0)

    def is_cancel_requested(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        return bool(task and task.get('cancel_requested'))

    def increment_resume_count(self, task_id: str) -> None:
        task = self.get_task(task_id) or {}
        self.update_task(task_id, resume_count=int(task.get('resume_count') or 0) + 1)


import_task_service = ImportTaskService()
