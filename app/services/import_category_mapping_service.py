import logging
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set


logger = logging.getLogger(__name__)


class ImportCategoryMappingService:
    TOP_LEVEL_EXCLUDE_REASON = '一级父类目禁止导入'
    TOP_LEVEL_EXCLUDE_SUGGESTION = '请选择 ERP 二级或更深类目后重试'
    MANUAL_REVIEW_REASON = '类目无法匹配且标题模糊匹配无结果'
    MANUAL_REVIEW_SUGGESTION = '请补充准确的 ERP 末级类目，或转人工审核'
    MIN_FUZZY_SCORE = 80

    def load_category_cache(self, cursor) -> Dict[str, Any]:
        cursor.execute(
            '''
                SELECT id, name, parentId
                FROM erp_category
                ORDER BY id ASC
            '''
        )
        rows = [dict(row) for row in cursor.fetchall()]

        child_ids: Set[int] = set()
        top_level_ids: Set[int] = set()
        top_level_names: Set[str] = set()
        for row in rows:
            name = str(row.get('name') or '').strip()
            parent_id = row.get('parentId')
            if parent_id in (None, 0, '0', ''):
                if name:
                    top_level_names.add(name)
                try:
                    top_level_ids.add(int(row.get('id')))
                except Exception:
                    pass
                continue
            try:
                child_ids.add(int(parent_id))
            except Exception:
                continue

        leaf_rows: List[Dict[str, Any]] = []
        exact_leaf_map: Dict[str, Dict[str, Any]] = {}
        keyword_index: Dict[str, List[int]] = {}
        for row in rows:
            row_id = row.get('id')
            if row_id is None:
                continue
            try:
                row_id_int = int(row_id)
            except Exception:
                continue
            if row_id_int in child_ids:
                continue
            # 排除"无子节点的一级类目"，避免导入后无法同步到 ERP。
            if row_id_int in top_level_ids:
                continue

            name = str(row.get('name') or '').strip()
            if not name:
                continue

            normalized_row = {
                'id': row_id_int,
                'name': name,
                'parentId': row.get('parentId'),
            }
            leaf_rows.append(normalized_row)
            exact_leaf_map.setdefault(name, normalized_row)
            for char in set(name):
                keyword_index.setdefault(char, []).append(row_id_int)

        return {
            'top_level_names': top_level_names,
            'exact_leaf_map': exact_leaf_map,
            'leaf_rows': leaf_rows,
            'leaf_by_id': {int(item['id']): item for item in leaf_rows},
            'keyword_index': keyword_index,
        }

    def evaluate_row(
        self,
        category_cache: Dict[str, Any],
        category_name: str,
        title: str,
    ) -> Dict[str, Any]:
        raw_category_name = str(category_name or '').strip()
        normalized_title = str(title or '').strip()

        if raw_category_name and raw_category_name in (category_cache.get('top_level_names') or set()):
            return {
                'excluded': True,
                'reason': self.TOP_LEVEL_EXCLUDE_REASON,
                'suggestion': self.TOP_LEVEL_EXCLUDE_SUGGESTION,
            }

        exact_match = self._match_exact_leaf(category_cache, raw_category_name)
        if exact_match:
            return {
                'excluded': False,
                'match_type': 'exact',
                'category': exact_match,
                'score': 100,
            }

        fuzzy_match = self._match_by_title(category_cache, normalized_title)
        if fuzzy_match:
            return {
                'excluded': False,
                'match_type': 'fuzzy',
                'category': fuzzy_match['category'],
                'score': fuzzy_match['score'],
            }

        return {
            'excluded': True,
            'reason': self.MANUAL_REVIEW_REASON,
            'suggestion': self.MANUAL_REVIEW_SUGGESTION,
        }

    def _match_exact_leaf(
        self,
        category_cache: Dict[str, Any],
        category_name: str,
    ) -> Optional[Dict[str, Any]]:
        if not category_name:
            return None

        normalized = str(category_name).strip()
        for separator in ('|', '>', '｜', '＞', '\\', '、'):
            normalized = normalized.replace(separator, '/')

        exact_map = category_cache.get('exact_leaf_map') or {}
        for part in reversed(normalized.split('/')):
            candidate = str(part or '').strip()
            if not candidate:
                continue
            matched = exact_map.get(candidate)
            if matched:
                return matched
        return None

    def _match_by_title(self, category_cache: Dict[str, Any], title: str) -> Optional[Dict[str, Any]]:
        if not title:
            return None

        keyword_index = category_cache.get('keyword_index') or {}
        leaf_by_id = category_cache.get('leaf_by_id') or {}
        candidate_ids: Set[int] = set()
        for char in set(str(title)):
            candidate_ids.update(keyword_index.get(char) or [])

        if not candidate_ids:
            return None

        best_row = None
        best_score = 0
        for category_id in candidate_ids:
            row = leaf_by_id.get(category_id)
            if not row:
                continue
            name = str(row.get('name') or '').strip()
            if not name:
                continue

            score = self._calculate_similarity(title, name)
            if score > best_score:
                best_row = row
                best_score = score

        if best_row and best_score >= self.MIN_FUZZY_SCORE:
            return {'category': best_row, 'score': best_score}
        return None

    def _calculate_similarity(self, title: str, keyword: str) -> int:
        source_title = str(title or '').strip()
        source_keyword = str(keyword or '').strip()
        if not source_title or not source_keyword:
            return 0
        if source_keyword in source_title:
            return 100

        if len(source_title) <= len(source_keyword):
            return int(SequenceMatcher(None, source_title, source_keyword).ratio() * 100)

        best_ratio = 0.0
        window = len(source_keyword)
        for start in range(0, len(source_title) - window + 1):
            ratio = SequenceMatcher(None, source_title[start:start + window], source_keyword).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
        return int(best_ratio * 100)


import_category_mapping_service = ImportCategoryMappingService()
