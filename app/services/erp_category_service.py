import json
import os
import time
import requests
from app.models import get_db

PLACEHOLDER = '%s'


class ErpCategoryService:
    _categories_cache = None
    _categories_cache_time = 0
    _categories_cache_ttl = 300
    _lookup_cache = None
    _lookup_cache_time = 0
    _lookup_cache_ttl = 300

    def _config(self):
        return {
            'erp_api_url': os.environ.get('ERP_API_URL', 'https://dev.1bgo.com').rstrip('/'),
            'erp_api_key': (os.environ.get('ERP_API_KEY', '') or '').strip()
        }

    def _headers(self, access_token='', tenant_id=''):
        headers = {'Content-Type': 'application/json'}
        if access_token:
            headers['token'] = access_token
            headers['accessToken'] = access_token
            headers['access-token'] = access_token
            headers['Authorization'] = f'Bearer {access_token}'
        if tenant_id is not None and str(tenant_id).strip() != '':
            value = str(tenant_id).strip()
            headers['tenant-id'] = value
            headers['tenantId'] = value
        return headers

    def _base_url(self):
        return self._config()['erp_api_url']

    def _flatten_categories(self, nodes, parent_id=None, level=1):
        rows = []
        if not isinstance(nodes, list):
            return rows
        queue = [(nodes, parent_id, level)]
        while queue:
            current_nodes, current_parent_id, current_level = queue.pop(0)
            if not isinstance(current_nodes, list):
                continue
            for node in current_nodes:
                if not isinstance(node, dict):
                    continue
                category_id = node.get('id') or node.get('categoryId') or node.get('value')
                if category_id is None:
                    continue
                try:
                    category_id = int(category_id)
                except Exception:
                    continue
                name = str(node.get('name') or node.get('categoryName') or '').strip()
                parent_value = node.get('parentId') if current_parent_id in (None, '') else current_parent_id
                try:
                    normalized_parent_id = int(parent_value) if parent_value not in (None, '') else None
                except Exception:
                    normalized_parent_id = None
                children = node.get('children')

                raw_status = node.get('status')
                if raw_status is None:
                    raw_status = node.get('categoryStatus') or node.get('enable') or node.get('disabled')

                try:
                    status_value = int(raw_status)
                except (TypeError, ValueError):
                    status_str = str(raw_status).lower().strip()
                    if status_str in ('0', 'false', 'off', 'disable', 'disabled', 'close', 'closed', 'no'):
                        status_value = 0
                    elif status_str in ('1', 'true', 'on', 'enable', 'enabled', 'open', 'yes'):
                        status_value = 1
                    else:
                        status_value = 0

                rows.append({
                    'id': category_id,
                    'parentId': normalized_parent_id,
                    'name': name,
                    'picUrl': str(node.get('picUrl') or '').strip(),
                    'sort': int(node.get('sort') or 0),
                    'status': status_value,
                    'description': str(node.get('description') or '').strip(),
                    'createTime': str(node.get('createTime') or '').strip(),
                    'visible': 1 if bool(node.get('visible', True)) else 0
                })
                if isinstance(children, list) and children:
                    queue.append((children, category_id, current_level + 1))
        return rows

    def _normalize_rows(self, data):
        nodes = []
        if isinstance(data, list):
            nodes = data
        elif isinstance(data, dict):
            candidates = [
                data.get('list'),
                data.get('records'),
                data.get('items'),
                data.get('rows'),
                data.get('categories'),
                data.get('data')
            ]
            for candidate in candidates:
                if isinstance(candidate, list):
                    nodes = candidate
                    break
                if isinstance(candidate, dict):
                    nested = (
                        candidate.get('list')
                        or candidate.get('records')
                        or candidate.get('items')
                        or candidate.get('rows')
                        or candidate.get('categories')
                    )
                    if isinstance(nested, list):
                        nodes = nested
                        break
            if not nodes and data.get('id') is not None:
                nodes = [data]
        rows = self._flatten_categories(nodes)
        return rows

    def get_cached_categories(self):
        current_time = time.time()
        if self._categories_cache is not None and (current_time - self._categories_cache_time) < self._categories_cache_ttl:
            return self._categories_cache
        return None

    def set_cached_categories(self, categories):
        self._categories_cache = categories
        self._categories_cache_time = time.time()
        self._reset_lookup_cache()

    def invalidate_cache(self):
        self._categories_cache = None
        self._categories_cache_time = 0
        self._reset_lookup_cache()

    def _reset_lookup_cache(self):
        self._lookup_cache = None
        self._lookup_cache_time = 0

    def _to_nullable_int(self, value):
        try:
            if value is None or str(value).strip() == '':
                return None
            return int(str(value).strip())
        except Exception:
            return None

    def _load_category_lookup(self):
        current_time = time.time()
        if self._lookup_cache is not None and (current_time - self._lookup_cache_time) < self._lookup_cache_ttl:
            return self._lookup_cache

        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute('SELECT id, parentId, name FROM erp_category')
            rows = cursor.fetchall()
        finally:
            conn.close()

        by_id = {}
        for row in rows or []:
            category_id = self._to_nullable_int((row or {}).get('id'))
            if category_id is None:
                continue
            by_id[category_id] = {
                'id': category_id,
                'parentId': self._to_nullable_int((row or {}).get('parentId')) or 0,
                'name': str((row or {}).get('name') or '').strip()
            }

        self._lookup_cache = {'by_id': by_id}
        self._lookup_cache_time = current_time
        return self._lookup_cache

    def get_category_path_info(self, category_id):
        normalized_category_id = self._to_nullable_int(category_id)
        if normalized_category_id is None:
            return {
                'category_id': None,
                'path': [],
                'path_names': [],
                'level': 0,
                'found': False
            }

        lookup = self._load_category_lookup()
        by_id = lookup.get('by_id') or {}
        path = []
        visited = set()
        current_id = normalized_category_id

        while current_id is not None and current_id not in visited:
            visited.add(current_id)
            item = by_id.get(current_id)
            if not item:
                break
            path.append(dict(item))
            parent_id = self._to_nullable_int(item.get('parentId'))
            if parent_id in (None, 0):
                break
            current_id = parent_id

        path.reverse()
        return {
            'category_id': normalized_category_id,
            'path': path,
            'path_names': [str(item.get('name') or '').strip() for item in path if str(item.get('name') or '').strip()],
            'level': len(path),
            'found': bool(path)
        }

    def sync_categories(self, access_token='', tenant_id=''):
        url = f"{self._base_url()}/admin-api/product/category/list"
        config = self._config()
        auth_tokens = []
        if access_token and str(access_token).strip():
            auth_tokens.append(str(access_token).strip())
        if config['erp_api_key'] and config['erp_api_key'] not in auth_tokens:
            auth_tokens.append(config['erp_api_key'])
        if not auth_tokens:
            auth_tokens.append('')
        last_error = '获取ERP分类失败'
        body = None
        for token in auth_tokens:
            try:
                response = requests.get(url, headers=self._headers(token, tenant_id), timeout=30)
            except Exception as e:
                last_error = str(e)
                continue
            body_text = response.text or ''
            try:
                body = response.json()
            except Exception:
                last_error = f'分类接口返回非JSON: {body_text[:300]}'
                continue
            if response.status_code != 200:
                last_error = body.get('msg') if isinstance(body, dict) else f'HTTP {response.status_code}'
                continue
            if isinstance(body, dict) and str(body.get('code')) in ('0', '200'):
                break
            last_error = body.get('msg', '获取ERP分类失败') if isinstance(body, dict) else '获取ERP分类失败'
            if '未登录' not in str(last_error):
                break
        if not isinstance(body, dict) or str(body.get('code')) not in ('0', '200'):
            return {'success': False, 'error': last_error}
        data = body.get('data')
        
        # 调试：打印第一条数据查看状态字段
        if data and isinstance(data, list) and len(data) > 0:
            first_item = data[0]
            print(f"[Category Sync] First item raw data: {json.dumps(first_item, ensure_ascii=False)[:500]}")
            print(f"[Category Sync] Status field: {first_item.get('status')}, type: {type(first_item.get('status'))}")
        
        rows = self._normalize_rows(data)
        if not rows:
            return {'success': False, 'error': 'ERP分类返回为空或结构无法解析，已跳过本地分类更新'}
        conn = get_db()
        cursor = conn.cursor()
        try:
            # 获取同步前的分类ID列表，用于后续清理
            cursor.execute('SELECT id FROM erp_category')
            existing_ids = {row['id'] for row in cursor.fetchall()}
            new_ids = {row['id'] for row in rows}

            # 删除ERP中已不存在的分类
            ids_to_delete = existing_ids - new_ids
            if ids_to_delete:
                placeholders = ','.join([PLACEHOLDER] * len(ids_to_delete))
                cursor.execute(f'DELETE FROM erp_category WHERE id IN ({placeholders})', tuple(ids_to_delete))
                deleted_count = cursor.rowcount
                print(f"[Category Sync] Deleted {deleted_count} obsolete categories")

            # 插入或更新分类
            inserted_count = 0
            updated_count = 0
            for row in rows:
                cursor.execute(f'''
                    INSERT INTO erp_category (id, parentId, name, picUrl, sort, status, description, createTime, visible)
                    VALUES ({PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER})
                    ON DUPLICATE KEY UPDATE
                        parentId = VALUES(parentId),
                        name = VALUES(name),
                        picUrl = VALUES(picUrl),
                        sort = VALUES(sort),
                        status = VALUES(status),
                        description = VALUES(description),
                        createTime = VALUES(createTime),
                        visible = VALUES(visible)
                ''', (
                    row['id'], row['parentId'], row['name'], row['picUrl'], row['sort'], row['status'],
                    row['description'], row['createTime'], row['visible']
                ))
                # rowcount: 1=insert, 2=update (MySQL ON DUPLICATE KEY UPDATE特性)
                if cursor.rowcount == 1:
                    inserted_count += 1
                elif cursor.rowcount == 2:
                    updated_count += 1
                else:
                    # 某些MySQL版本可能返回0（数据无变化）
                    updated_count += 1

            conn.commit()
            self._reset_lookup_cache()

            # 获取同步后的实际总数
            cursor.execute('SELECT COUNT(*) AS cnt FROM erp_category')
            total_in_db = cursor.fetchone()['cnt']

            print(f"[Category Sync] ERP returned {len(rows)} categories, inserted {inserted_count}, updated {updated_count}, deleted {len(ids_to_delete)}, total in DB: {total_in_db}")

            return {
                'success': True,
                'total': total_in_db,  # 返回数据库实际总数，而非ERP返回数
                'erp_total': len(rows),
                'inserted': inserted_count,
                'updated': updated_count,
                'deleted': len(ids_to_delete)
            }
        finally:
            conn.close()

    def count_categories(self):
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) AS cnt FROM erp_category')
        row = cursor.fetchone() or {}
        conn.close()
        return int(row.get('cnt', 0) or 0)

    def get_stats(self):
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) AS cnt, MAX(updated_at) AS last_sync_at FROM erp_category')
        row = cursor.fetchone() or {}
        conn.close()
        last_sync_at = row.get('last_sync_at')
        if last_sync_at is not None:
            try:
                last_sync_at = last_sync_at.isoformat(sep=' ', timespec='seconds')
            except Exception:
                last_sync_at = str(last_sync_at)
        return {
            'total': int(row.get('cnt', 0) or 0),
            'last_sync_at': last_sync_at
        }


erp_category_service = ErpCategoryService()
