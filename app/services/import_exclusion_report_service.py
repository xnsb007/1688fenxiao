import csv
import io
import logging
from typing import Any, Dict, List
from uuid import uuid4

from app.config import BASE_URL, SOURCE_TYPE
from app.models import get_db


logger = logging.getLogger(__name__)


class ImportExclusionReportService:
    def create_report(self, rows: List[Dict[str, Any]], source: str = SOURCE_TYPE, import_task_id: str = '') -> Dict[str, Any]:
        normalized_rows = [dict(item) for item in (rows or []) if isinstance(item, dict)]
        if not normalized_rows:
            return {'report_id': '', 'count': 0, 'report_url': ''}

        report_id = uuid4().hex
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''
                    INSERT INTO import_exclusion_report
                    (report_id, source_type, import_task_id, excluded_count)
                    VALUES (%s, %s, %s, %s)
                ''',
                (report_id, str(source or SOURCE_TYPE), str(import_task_id or ''), len(normalized_rows))
            )
            cursor.executemany(
                '''
                    INSERT INTO import_exclusion_report_item
                    (report_id, row_index, raw_category_name, exclude_reason, masked_title, suggestion, offer_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                ''',
                [
                    (
                        report_id,
                        int(item.get('row_index') or 0),
                        str(item.get('raw_category_name') or '')[:255],
                        str(item.get('exclude_reason') or '')[:255],
                        self.mask_title(str(item.get('title') or '')),
                        str(item.get('suggestion') or '')[:255],
                        str(item.get('offer_id') or '')[:64],
                    )
                    for item in normalized_rows
                ]
            )
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception('Create import exclusion report failed report_id=%s', report_id)
            raise
        finally:
            conn.close()

        return {
            'report_id': report_id,
            'count': len(normalized_rows),
            'report_url': self.build_report_url(report_id),
        }

    def get_report_summary(self, report_id: str) -> Dict[str, Any]:
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''
                    SELECT report_id, source_type, import_task_id, excluded_count, created_at
                    FROM import_exclusion_report
                    WHERE report_id = %s
                ''',
                (report_id,)
            )
            row = cursor.fetchone()
            if not row:
                return {}
            summary = dict(row)
            summary['report_url'] = self.build_report_url(report_id)
            return summary
        finally:
            conn.close()

    def list_report_items(self, report_id: str, page: int = 1, page_size: int = 200) -> Dict[str, Any]:
        safe_page = max(1, int(page or 1))
        safe_page_size = max(1, min(500, int(page_size or 200)))
        offset = (safe_page - 1) * safe_page_size

        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''
                    SELECT COUNT(*) AS total_count
                    FROM import_exclusion_report_item
                    WHERE report_id = %s
                ''',
                (report_id,)
            )
            total_count = int((cursor.fetchone() or {}).get('total_count') or 0)
            cursor.execute(
                '''
                    SELECT row_index, raw_category_name, exclude_reason, masked_title, suggestion, offer_id
                    FROM import_exclusion_report_item
                    WHERE report_id = %s
                    ORDER BY row_index ASC, id ASC
                    LIMIT %s OFFSET %s
                ''',
                (report_id, safe_page_size, offset)
            )
            rows = [dict(row) for row in cursor.fetchall()]
            return {
                'items': rows,
                'page': safe_page,
                'page_size': safe_page_size,
                'total_count': total_count,
            }
        finally:
            conn.close()

    def get_row_numbers(self, report_id: str) -> List[int]:
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''
                    SELECT row_index
                    FROM import_exclusion_report_item
                    WHERE report_id = %s
                    ORDER BY row_index ASC, id ASC
                ''',
                (report_id,)
            )
            return [int(row.get('row_index') or 0) for row in cursor.fetchall()]
        finally:
            conn.close()

    def export_csv(self, report_id: str) -> str:
        summary = self.get_report_summary(report_id)
        if not summary:
            return ''

        rows = self.list_report_items(report_id, page=1, page_size=500000).get('items') or []
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(['Excel 行号', '商品标题（脱敏）', '原始类目值', '排除原因', '建议操作', 'Offer ID'])
        for item in rows:
            writer.writerow([
                item.get('row_index') or '',
                item.get('masked_title') or '',
                item.get('raw_category_name') or '',
                item.get('exclude_reason') or '',
                item.get('suggestion') or '',
                item.get('offer_id') or '',
            ])
        return buffer.getvalue()

    def build_report_url(self, report_id: str) -> str:
        return f"{BASE_URL.rstrip('/')}/products/import-exclusion-reports/{report_id}"

    def mask_title(self, title: str) -> str:
        text = str(title or '').strip()
        if len(text) <= 8:
            return text[:2] + '***' if text else ''
        return f"{text[:6]}***{text[-4:]}"


import_exclusion_report_service = ImportExclusionReportService()
