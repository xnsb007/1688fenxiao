# -*- coding: utf-8 -*-
"""
CategoryImportRuleV2 - 三级类目校验策略

校验顺序：
1. 前置拦截：一级类目直接拒绝
2. 精准匹配：全路径 O(1) 查找（name → category, 全路径 → category）
3. 兜底匹配：标题分词 + 相似度，阈值 0.85，超时 500ms
   拒绝记录写入 import_log 表
"""

import concurrent.futures
import logging
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# 兜底匹配最低置信度（0.85 = 85分）
MIN_FUZZY_SCORE = 85

# 兜底匹配超时（秒）
FUZZY_TIMEOUT_SECONDS = 0.5


class CategoryImportRuleV2:

    LEVEL1_REJECT_REASON = '一级类目禁止导入，请填写二级或三级类目'
    LEVEL1_REJECT_SUGGESTION = '请填写二级或三级类目'
    UNRECOGNIZED_REASON = '类目无法识别，请人工修正'

    # ------------------------------------------------------------------ #
    # 缓存构建                                                              #
    # ------------------------------------------------------------------ #

    def load_category_cache_v2(self, cursor) -> Dict[str, Any]:
        """
        加载全量类目缓存，构建：
        - top_level_names：一级类目名称集合（用于前置拦截）
        - full_path_index：Map<类目名 or 全路径, category_entry>（精准匹配，O(1)）
        - leaf_by_id / keyword_index：叶子节点信息（兜底匹配）
        """
        cursor.execute(
            'SELECT id, name, parentId FROM erp_category ORDER BY id ASC'
        )
        rows = [dict(r) for r in cursor.fetchall()]

        # 构建 id → row 查找表
        all_by_id: Dict[int, Dict] = {}
        for row in rows:
            try:
                all_by_id[int(row['id'])] = row
            except Exception:
                continue

        # 识别一级类目（parentId 为空/0）
        top_level_ids: Set[int] = set()
        top_level_names: Set[str] = set()
        for row in rows:
            parent_id = row.get('parentId')
            if parent_id in (None, 0, '0', ''):
                try:
                    top_level_ids.add(int(row['id']))
                except Exception:
                    pass
                name = str(row.get('name') or '').strip()
                if name:
                    top_level_names.add(name)

        def build_full_path(cat_id_int: int) -> str:
            """从根到叶构建以 '/' 分隔的全路径"""
            parts: List[str] = []
            visited: Set[int] = set()
            cid = cat_id_int
            while cid and cid not in visited:
                visited.add(cid)
                cat = all_by_id.get(cid)
                if not cat:
                    break
                name = str(cat.get('name') or '').strip()
                if name:
                    parts.append(name)
                parent_id = cat.get('parentId')
                if parent_id in (None, 0, '0', ''):
                    break
                try:
                    cid = int(parent_id)
                except Exception:
                    break
            parts.reverse()
            return '/'.join(parts)

        # 构建全路径索引（仅对二级及以下类目）
        # 同时以 简单名称 和 全路径 为 key，均指向同一 category_entry
        full_path_index: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            try:
                cat_id = int(row['id'])
            except Exception:
                continue
            if cat_id in top_level_ids:
                continue
            name = str(row.get('name') or '').strip()
            if not name:
                continue
            cat_entry = {
                'id': cat_id,
                'name': name,
                'parentId': row.get('parentId'),
            }
            # 简单名称索引（同名取先出现的，ORDER BY id ASC 保证稳定性）
            full_path_index.setdefault(name, cat_entry)
            # 全路径索引
            full_path = build_full_path(cat_id)
            if full_path and full_path != name:
                full_path_index.setdefault(full_path, cat_entry)

        # 构建叶子节点索引（供兜底模糊匹配）
        child_ids: Set[int] = set()
        for row in rows:
            parent_id = row.get('parentId')
            if parent_id not in (None, 0, '0', ''):
                try:
                    child_ids.add(int(parent_id))
                except Exception:
                    pass

        leaf_rows: List[Dict[str, Any]] = []
        keyword_index: Dict[str, List[int]] = {}
        for row in rows:
            try:
                cat_id = int(row['id'])
            except Exception:
                continue
            if cat_id in child_ids:
                continue
            # 一级类目（无子类目）也会进入此分支，但一级类目同步会被 ERP 拒绝，
            # 因此不允许参与标题相似度兜底匹配，避免导入后无法同步到 ERP。
            if cat_id in top_level_ids:
                continue
            name = str(row.get('name') or '').strip()
            if not name:
                continue
            entry = {'id': cat_id, 'name': name, 'parentId': row.get('parentId')}
            leaf_rows.append(entry)
            for char in set(name):
                keyword_index.setdefault(char, []).append(cat_id)

        return {
            'top_level_names': top_level_names,
            'top_level_ids': top_level_ids,
            'full_path_index': full_path_index,
            'all_by_id': all_by_id,
            'leaf_rows': leaf_rows,
            'leaf_by_id': {r['id']: r for r in leaf_rows},
            'keyword_index': keyword_index,
        }

    # ------------------------------------------------------------------ #
    # 三级校验入口                                                          #
    # ------------------------------------------------------------------ #

    def evaluate_row_v2(
        self,
        category_cache: Dict[str, Any],
        category_name: str,
        title: str,
        offer_id: str = '',
        import_task_id: str = '',
    ) -> Dict[str, Any]:
        """
        三级校验策略：
        1. 一级类目 → 立即拒绝
        2. 精准全路径匹配 → 直接绑定
        3. 标题相似度兜底（阈值 0.85，500ms 超时）→ 置信度达标绑定，否则拒绝

        返回结构与原 evaluate_row 兼容，新增 log_entry 供调用方写 import_log。
        """
        raw = str(category_name or '').strip()

        # ── 第一级：前置拦截一级类目 ──────────────────────────────────────
        if raw and raw in (category_cache.get('top_level_names') or set()):
            return {
                'excluded': True,
                'reason': self.LEVEL1_REJECT_REASON,
                'suggestion': self.LEVEL1_REJECT_SUGGESTION,
                'log_entry': self._make_log_entry(
                    offer_id, import_task_id, raw,
                    self.LEVEL1_REJECT_REASON, self.LEVEL1_REJECT_SUGGESTION,
                    title,
                ),
            }

        # ── 第二级：全路径精准匹配（O(1)）──────────────────────────────────
        matched = self._match_exact(category_cache, raw)
        if matched:
            return {
                'excluded': False,
                'match_type': 'exact',
                'category': matched,
                'score': 100,
            }

        # ── 第三级：标题相似度兜底（阈值 0.85，超时 500ms）─────────────────
        fuzzy = self._match_by_title_v2(category_cache, str(title or '').strip())
        if fuzzy:
            return {
                'excluded': False,
                'match_type': 'fuzzy',
                'category': fuzzy['category'],
                'score': fuzzy['score'],
            }

        # ── 兜底失败：拒绝并记录 ───────────────────────────────────────────
        suggestion = self._suggest_close_category(category_cache, raw)
        return {
            'excluded': True,
            'reason': self.UNRECOGNIZED_REASON,
            'suggestion': suggestion,
            'log_entry': self._make_log_entry(
                offer_id, import_task_id, raw,
                self.UNRECOGNIZED_REASON, suggestion, title,
            ),
        }

    # ------------------------------------------------------------------ #
    # 精准匹配                                                              #
    # ------------------------------------------------------------------ #

    def _normalize_separators(self, text: str) -> str:
        for sep in ('|', '>', '｜', '＞', '\\', '、', '→', '->'):
            text = text.replace(sep, '/')
        return text

    def _match_exact(
        self,
        cache: Dict[str, Any],
        category_name: str,
    ) -> Optional[Dict[str, Any]]:
        """
        O(1) 全路径精准匹配：
        1. 直接按名称查 full_path_index
        2. 规范化分隔符后再查
        3. 若输入含路径分隔符，从最末级段逐级尝试
        """
        if not category_name:
            return None

        full_path_index = cache.get('full_path_index') or {}

        # 直接查找（覆盖"休闲零食"这类简单名称）
        result = full_path_index.get(category_name)
        if result:
            return result

        # 规范化分隔符后查找（覆盖"食品>休闲零食"等格式）
        normalized = self._normalize_separators(category_name)
        if normalized != category_name:
            result = full_path_index.get(normalized)
            if result:
                return result

        # 若含路径分隔符，从最末级向上逐段匹配
        # 例："食品/休闲零食/坚果" → 先试"坚果"，再试"休闲零食"
        parts = [p.strip() for p in normalized.split('/') if p.strip()]
        if len(parts) > 1:
            for part in reversed(parts):
                result = full_path_index.get(part)
                if result:
                    return result

        return None

    # ------------------------------------------------------------------ #
    # 兜底匹配                                                              #
    # ------------------------------------------------------------------ #

    def _match_by_title_v2(
        self,
        cache: Dict[str, Any],
        title: str,
    ) -> Optional[Dict[str, Any]]:
        """标题相似度兜底：置信度 ≥ 0.85，异步执行，超时 500ms 视为失败"""
        if not title:
            return None

        def compute():
            return self._do_fuzzy_match(cache, title)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(compute)
            try:
                return future.result(timeout=FUZZY_TIMEOUT_SECONDS)
            except concurrent.futures.TimeoutError:
                logger.warning(
                    'CategoryImportRuleV2 fuzzy match timed out (>500ms) title=%s',
                    title[:60],
                )
                return None

    def _do_fuzzy_match(
        self,
        cache: Dict[str, Any],
        title: str,
    ) -> Optional[Dict[str, Any]]:
        keyword_index = cache.get('keyword_index') or {}
        leaf_by_id = cache.get('leaf_by_id') or {}

        candidate_ids: Set[int] = set()
        for char in set(title):
            candidate_ids.update(keyword_index.get(char) or [])

        if not candidate_ids:
            return None

        best_row = None
        best_score = 0
        for cat_id in candidate_ids:
            row = leaf_by_id.get(cat_id)
            if not row:
                continue
            name = str(row.get('name') or '').strip()
            if not name:
                continue
            score = self._calculate_similarity(title, name)
            if score > best_score:
                best_row = row
                best_score = score

        if best_row and best_score >= MIN_FUZZY_SCORE:
            return {'category': best_row, 'score': best_score}
        return None

    def _calculate_similarity(self, title: str, keyword: str) -> int:
        s_title = str(title or '').strip()
        s_kw = str(keyword or '').strip()
        if not s_title or not s_kw:
            return 0
        if s_kw in s_title:
            return 100
        if len(s_title) <= len(s_kw):
            return int(SequenceMatcher(None, s_title, s_kw).ratio() * 100)
        best = 0.0
        window = len(s_kw)
        for start in range(0, len(s_title) - window + 1):
            ratio = SequenceMatcher(None, s_title[start:start + window], s_kw).ratio()
            if ratio > best:
                best = ratio
        return int(best * 100)

    # ------------------------------------------------------------------ #
    # 建议修正值                                                            #
    # ------------------------------------------------------------------ #

    def _suggest_close_category(self, cache: Dict[str, Any], raw_name: str) -> str:
        """对无法识别的类目，在全路径索引中找最相近的名称作为建议"""
        if not raw_name:
            return ''
        full_path_index = cache.get('full_path_index') or {}
        best_name = ''
        best_score = 0
        for name in full_path_index:
            if '/' in name:
                continue  # 只对比简单名称
            score = self._calculate_similarity(raw_name, name)
            if score > best_score:
                best_name = name
                best_score = score
        if best_score >= 60 and best_name:
            return f'建议修正为：{best_name}'
        return '请在 category-sync 中确认正确的类目名称后重新导入'

    # ------------------------------------------------------------------ #
    # import_log 写入                                                       #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _make_log_entry(
        offer_id: str,
        import_task_id: str,
        raw_category_name: str,
        reject_reason: str,
        suggestion_value: str,
        product_title: str,
    ) -> Dict[str, str]:
        return {
            'offer_id': str(offer_id or ''),
            'import_task_id': str(import_task_id or ''),
            'raw_category_name': str(raw_category_name or ''),
            'reject_reason': str(reject_reason or ''),
            'suggestion_value': str(suggestion_value or ''),
            'product_title': str(product_title or ''),
        }

    @staticmethod
    def write_import_logs(cursor, log_entries: List[Dict[str, Any]]) -> None:
        """批量写入 import_log 拒绝记录"""
        if not log_entries:
            return
        for entry in log_entries:
            try:
                cursor.execute(
                    '''
                    INSERT INTO import_log
                        (offer_id, import_task_id, raw_category_name,
                         reject_reason, suggestion_value, product_title)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ''',
                    (
                        str(entry.get('offer_id') or '')[:64],
                        str(entry.get('import_task_id') or '')[:64],
                        str(entry.get('raw_category_name') or '')[:255],
                        str(entry.get('reject_reason') or '')[:255],
                        str(entry.get('suggestion_value') or '')[:255],
                        str(entry.get('product_title') or '')[:500],
                    ),
                )
            except Exception as exc:
                logger.warning(
                    'Failed to write import_log entry offer_id=%s: %s',
                    entry.get('offer_id'), exc,
                )


category_import_rule_v2 = CategoryImportRuleV2()
