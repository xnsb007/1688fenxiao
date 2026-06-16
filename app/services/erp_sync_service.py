import os
import json
import requests
import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from app.models import get_db
from app.utils import calculate_adjusted_price_with_freight_fixed_nine

logger = logging.getLogger(__name__)
TOP_LEVEL_CATEGORY_NOT_ALLOWED_CODE = 'TOP_LEVEL_CATEGORY_NOT_ALLOWED'
TOP_LEVEL_CATEGORY_NOT_ALLOWED_MESSAGE = '顶级类目不允许直接同步，请选择二级或更深类目后重试。'

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)

class ERPSyncService:
    def __init__(self):
        self._erp_category_lookup = None
        self._erp_category_lookup_time = 0
    
    def _get_config(self):
        return {
            'erp_api_url': os.environ.get('ERP_API_URL', 'https://dev.1bgo.com'),
            'erp_api_key': os.environ.get('ERP_API_KEY', ''),
            'erp_api_secret': os.environ.get('ERP_API_SECRET', '')
        }

    def _request_with_refresh_retry(self, method, url, headers=None, timeout=30, **kwargs):
        response = requests.request(method, url, headers=headers, timeout=timeout, **kwargs)
        if response.status_code not in (401, 403) and not self._is_auth_failure_response(response):
            return response
        try:
            from flask import has_request_context, session
            if not has_request_context():
                return response
            from app.routes.auth import refresh_access_token, get_valid_token
            refresh_result = refresh_access_token(with_detail=True)
            if not refresh_result.get('success'):
                return response
            new_token = get_valid_token()
            if not new_token:
                return response
            retry_headers = dict(headers or {})
            retry_headers['Authorization'] = f'Bearer {new_token}'
            retry_headers['accessToken'] = new_token
            tenant_id = str(session.get('tenant_id', '')).strip()
            if tenant_id:
                retry_headers['tenant-id'] = tenant_id
                retry_headers['tenantId'] = tenant_id
            return requests.request(method, url, headers=retry_headers, timeout=timeout, **kwargs)
        except Exception:
            return response

    def _is_auth_failure_response(self, response):
        if response is None:
            return False
        if response.status_code in (401, 403):
            return True
        if response.status_code not in (200, 201):
            return False
        try:
            body = response.json()
        except Exception:
            return False
        return self._is_auth_failure_body(body)

    def _is_auth_failure_body(self, body):
        if not isinstance(body, dict):
            return False
        code = str(body.get('code') or body.get('status') or '').strip()
        message = str(
            body.get('msg')
            or body.get('message')
            or body.get('error')
            or body.get('errorMsg')
            or ''
        )
        if code in ('401', '403', 'UNAUTHORIZED', 'TOKEN_EXPIRED'):
            return True
        return any(keyword in message for keyword in ('账号未登录', '未登录', '登录已过期', 'token过期', 'Token过期'))
    
    def is_configured(self):
        config = self._get_config()
        return bool(config['erp_api_url'])

    def _load_erp_category_lookup(self):
        import time
        now = time.time()
        if self._erp_category_lookup and (now - self._erp_category_lookup_time) < 120:
            return self._erp_category_lookup
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                'SELECT id, parentId, name, sort, status, visible FROM erp_category ORDER BY sort ASC, id DESC'
            )
            rows = cursor.fetchall()
        finally:
            conn.close()

        by_id = {}
        by_name = {}
        child_ids_by_parent = {}

        for row in rows:
            item = dict(row)
            cid = item.get('id')
            name = str(item.get('name') or '').strip()
            try:
                cid = int(cid)
            except Exception:
                continue
            try:
                parent_id = int(item.get('parentId') or 0)
            except Exception:
                parent_id = 0
            normalized = {
                'id': cid,
                'parentId': parent_id,
                'name': name,
                'sort': item.get('sort') or 0,
                'status': item.get('status'),
                'visible': item.get('visible'),
                'has_children': False
            }
            by_id[cid] = normalized
            if name:
                by_name.setdefault(name, []).append(cid)
            child_ids_by_parent.setdefault(parent_id, []).append(cid)

        for parent_id, child_ids in child_ids_by_parent.items():
            if parent_id in by_id and child_ids:
                by_id[parent_id]['has_children'] = True

        self._erp_category_lookup = {
            'rows': list(by_id.values()),
            'by_id': by_id,
            'by_name': by_name,
            'child_ids_by_parent': child_ids_by_parent
        }
        self._erp_category_lookup_time = now
        return self._erp_category_lookup

    def _split_category_candidates(self, category_text):
        if not category_text:
            return []
        text = str(category_text).replace('|', '/').replace('>', '/').replace('＞', '/')
        parts = []
        for part in text.split('/'):
            value = str(part).strip()
            if value:
                parts.append(value)
        return parts

    def _is_selectable_category(self, category_item):
        return bool(category_item) and not bool(category_item.get('has_children'))

    def _get_category_path_items(self, category_id, lookup):
        by_id = lookup.get('by_id') or {}
        path = []
        visited = set()
        current_id = self._to_nullable_int(category_id)
        while current_id is not None and current_id not in visited:
            visited.add(current_id)
            item = by_id.get(current_id)
            if not item:
                break
            path.append(item)
            parent_id = self._to_nullable_int(item.get('parentId'))
            if parent_id in (None, 0):
                break
            current_id = parent_id
        path.reverse()
        return path

    def _get_category_path_names(self, category_id, lookup):
        return [
            self._to_str(item.get('name'))
            for item in self._get_category_path_items(category_id, lookup)
            if self._to_str(item.get('name'))
        ]

    def _get_category_depth(self, category_id, lookup):
        return len(self._get_category_path_items(category_id, lookup))

    def _is_sync_allowed_category(self, category_item, lookup):
        return bool(category_item) and self._get_category_depth(category_item.get('id'), lookup) >= 2

    def _get_selectable_child_names(self, category_id, lookup, limit=5):
        by_id = lookup.get('by_id') or {}
        child_ids_by_parent = lookup.get('child_ids_by_parent') or {}
        child_names = []
        for child_id in child_ids_by_parent.get(self._to_nullable_int(category_id) or 0, []):
            item = by_id.get(child_id)
            if not self._is_selectable_category(item):
                continue
            name = self._to_str(item.get('name'))
            if name:
                child_names.append(name)
            if len(child_names) >= limit:
                break
        return child_names

    def _find_sync_allowed_category_by_name(self, name, lookup):
        by_id = lookup.get('by_id') or {}
        for category_id in lookup.get('by_name', {}).get(name, []):
            item = by_id.get(category_id)
            if self._is_sync_allowed_category(item, lookup):
                return item
        return None

    def _find_category_by_name(self, name, lookup):
        by_id = lookup.get('by_id') or {}
        for category_id in lookup.get('by_name', {}).get(name, []):
            item = by_id.get(category_id)
            if item:
                return item
        return None

    def _find_sync_allowed_category_by_title(self, title, lookup):
        by_id = lookup.get('by_id') or {}
        best_name = ''
        best_item = None
        for name, category_ids in (lookup.get('by_name') or {}).items():
            if len(name) < 2 or name not in title:
                continue
            for category_id in category_ids:
                item = by_id.get(category_id)
                if not self._is_sync_allowed_category(item, lookup):
                    continue
                if len(name) > len(best_name):
                    best_name = name
                    best_item = item
                break
        return best_item

    def _resolve_sync_category_legacy(self, product):
        lookup = self._load_erp_category_lookup()
        by_id = lookup.get('by_id') or {}

        stored_category_id = self._to_nullable_int(product.get('erp_category_id'))
        stored_item = by_id.get(stored_category_id) if stored_category_id is not None else None
        if self._is_sync_allowed_category(stored_item, lookup):
            return {'category': stored_item}

        category_candidates = []
        category_candidates.extend(self._split_category_candidates(product.get('erp_category_name', '')))
        category_candidates.extend(self._split_category_candidates(product.get('category_name', '')))

        for name in reversed(category_candidates):
            item = self._find_sync_allowed_category_by_name(name, lookup)
            if item:
                return {'category': item}

        title = self._to_str(product.get('title', ''))
        if title:
            item = self._find_sync_allowed_category_by_title(title, lookup)
            if item:
                return {'category': item}

        invalid_item = stored_item
        if invalid_item is None:
            for name in reversed(category_candidates):
                invalid_item = self._find_category_by_name(name, lookup)
                if invalid_item:
                    break

        if invalid_item:
            invalid_depth = self._get_category_depth(invalid_item.get('id'), lookup)
            if invalid_depth == 1:
                return {
                    'category': None,
                    'code': TOP_LEVEL_CATEGORY_NOT_ALLOWED_CODE,
                    'error': TOP_LEVEL_CATEGORY_NOT_ALLOWED_MESSAGE
                }
            if invalid_depth >= 2:
                return {'category': invalid_item}
            suggestion = f"，请改为其子类目，如：{'、'.join(child_names)}" if child_names else "，请改为其子类目后再重试"
            return {
                'category': None,
                'error': f'当前ERP类目“{display_name}”是父级类目，不能直接同步{suggestion}'
            }

        return {
            'category': None,
            'error': '未匹配到有效ERP类目，请先同步分类并维护商品类目映射'
        }

    def _resolve_sync_category(self, product):
        lookup = self._load_erp_category_lookup()
        by_id = lookup.get('by_id') or {}

        stored_category_id = self._to_nullable_int(product.get('erp_category_id'))
        stored_item = by_id.get(stored_category_id) if stored_category_id is not None else None
        if self._is_sync_allowed_category(stored_item, lookup):
            return {'category': stored_item}

        category_candidates = []
        category_candidates.extend(self._split_category_candidates(product.get('erp_category_name', '')))
        category_candidates.extend(self._split_category_candidates(product.get('category_name', '')))

        for name in reversed(category_candidates):
            item = self._find_sync_allowed_category_by_name(name, lookup)
            if item:
                return {'category': item}

        title = self._to_str(product.get('title', ''))
        if title:
            item = self._find_sync_allowed_category_by_title(title, lookup)
            if item:
                return {'category': item}

        invalid_item = stored_item
        if invalid_item is None:
            for name in reversed(category_candidates):
                invalid_item = self._find_category_by_name(name, lookup)
                if invalid_item:
                    break

        if invalid_item:
            invalid_depth = self._get_category_depth(invalid_item.get('id'), lookup)
            if invalid_depth == 1:
                return {
                    'category': None,
                    'code': TOP_LEVEL_CATEGORY_NOT_ALLOWED_CODE,
                    'error': TOP_LEVEL_CATEGORY_NOT_ALLOWED_MESSAGE
                }
            if invalid_depth >= 2:
                return {'category': invalid_item}

        return {
            'category': None,
            'error': '未匹配到有效ERP类目，请先同步分类并维护商品类目映射'
        }

    def _resolve_sync_category_id(self, product):
        result = self._resolve_sync_category(product)
        category = result.get('category')
        if category:
            return category.get('id')
        return None

    def _remap_task_details(self, details, sync_items):
        normalized = []
        if not isinstance(details, list):
            return normalized
        for item in details:
            if not isinstance(item, dict):
                continue
            mapped_item = dict(item)
            index = self._to_nullable_int(item.get('index'))
            if index is not None and 0 <= index < len(sync_items):
                source_item = sync_items[index]
                mapped_item['index'] = source_item.get('index')
                mapped_item['spuId'] = self._to_str(
                    item.get('spuId') or source_item.get('offer_id')
                )
                mapped_item['spuName'] = self._to_str(
                    item.get('spuName') or source_item.get('title')
                )
            normalized.append({
                'index': mapped_item.get('index'),
                'spuId': self._to_str(mapped_item.get('spuId') or mapped_item.get('offer_id')),
                'spuName': self._to_str(mapped_item.get('spuName') or mapped_item.get('name')),
                'reason': self._to_str(mapped_item.get('reason') or mapped_item.get('msg') or mapped_item.get('message'))
            })
        return normalized
    
    def sync_products(self, products, access_token='', tenant_id=''):
        config = self._get_config()
        print(f"[ERP Sync] ERP_API_URL: {config['erp_api_url']}")
        
        if not config['erp_api_url']:
            return {
                'success': False,
                'error': 'ERP API未配置，请设置环境变量 ERP_API_URL'
            }
        
        sync_items = []
        preflight_failed_details = []
        for idx, product in enumerate(products or []):
            try:
                sync_data = self._prepare_sync_data(product)
                sync_items.append({
                    'index': idx,
                    'offer_id': self._to_str(product.get('offer_id')) if isinstance(product, dict) else '',
                    'title': self._to_str(product.get('title')) if isinstance(product, dict) else '',
                    'payload': sync_data
                })
                print(f"[ERP Sync] Prepared sync data for offer_id: {product.get('offer_id')}")
            except Exception as e:
                logger.exception(
                    "[ERP SYNC] Build payload failed offer_id=%s error=%s",
                    product.get('offer_id') if isinstance(product, dict) else None,
                    str(e)
                )
                preflight_failed_details.append({
                    'index': idx,
                    'spuId': self._to_str(product.get('offer_id')) if isinstance(product, dict) else '',
                    'spuName': self._to_str(product.get('title')) if isinstance(product, dict) else '',
                    'reason': str(e)
                })

        if not sync_items:
            first_reason = ''
            for item in preflight_failed_details:
                if item.get('reason'):
                    first_reason = str(item.get('reason'))
                    break
            return {
                'success': False,
                'error': first_reason or '构造ERP请求体失败',
                'total': len(products),
                'success_count': 0,
                'fail_count': len(products),
                'failed_details': preflight_failed_details,
                'validation_failed': True,
                'submitted_offer_ids': []
            }

        sync_data_list = [item.get('payload') for item in sync_items]
        submitted_offer_ids = [item.get('offer_id') for item in sync_items if item.get('offer_id')]

        print(f"[ERP Sync] Calling _call_erp_api with {len(sync_data_list)} products")
        try:
            result = self._call_erp_api(sync_data_list, config, access_token, tenant_id)
            print(f"[ERP Sync] _call_erp_api returned: {result}")
            
            if result.get('success'):
                erp_failed_details = self._remap_task_details(result.get('failed_details') or [], sync_items)
                merged_failed_details = preflight_failed_details + erp_failed_details
                merged_fail_count = len(preflight_failed_details) + (result.get('fail_count') or 0)
                if result.get('async_task'):
                    submitted_count = result.get('submitted_count')
                    if submitted_count is None:
                        submitted_count = len(sync_items) - (result.get('fail_count') or 0)
                    return {
                        'success': True,
                        'async_task': True,
                        'task_no': result.get('task_no'),
                        'task_offer_ids': submitted_offer_ids,
                        'total': len(products),
                        'success_count': submitted_count,
                        'fail_count': merged_fail_count,
                        'pending_count': result.get('pending_count') if result.get('pending_count') is not None else submitted_count,
                        'failed_details': merged_failed_details,
                        'message': result.get('message') or '已提交批量创建任务',
                        'request_meta': result.get('request_meta') or {}
                    }
                total = result.get('total')
                if total is None:
                    total = len(sync_items)
                success_count = result.get('success_count')
                if success_count is None:
                    success_count = total
                fail_count = result.get('fail_count')
                if fail_count is None:
                    fail_count = max(total - success_count, 0)
                total = success_count + fail_count + len(preflight_failed_details)
                fail_count = fail_count + len(preflight_failed_details)
                failed_details = merged_failed_details
                is_all_success = fail_count == 0
                success_message = result.get('message') or result.get('msg') or f'成功同步 {len(products)} 个商品到ERP'
                return {
                    'success': is_all_success,
                    'partial_success': not is_all_success,
                    'total': total,
                    'success_count': success_count,
                    'fail_count': fail_count,
                    'failed_details': failed_details,
                    'message': success_message if is_all_success else f"{success_message}，存在 {fail_count} 条失败记录",
                    'request_meta': result.get('request_meta') or {}
                }
            else:
                failed_details = self._remap_task_details(result.get('failed_details') or [], sync_items)
                failed_details = preflight_failed_details + failed_details
                if not failed_details:
                    if products and isinstance(products[0], dict):
                        failed_details = [{
                            'index': 0,
                            'spuId': self._to_str(products[0].get('offer_id')),
                            'spuName': self._to_str(products[0].get('title', '')),
                            'reason': result.get('error', '同步失败')
                        }]
                    else:
                        failed_details = [{
                            'index': 0,
                            'spuId': '',
                            'spuName': '',
                            'reason': result.get('error', '同步失败')
                        }]
                return {
                    'success': False,
                    'error': result.get('error', '同步失败'),
                    'total': len(products),
                    'success_count': 0,
                    'fail_count': len(products),
                    'failed_details': failed_details,
                    'erp_raw': result.get('raw'),
                    'request_meta': result.get('request_meta') or {}
                }
        except Exception as e:
            logger.exception("[ERP SYNC] sync_products failed total=%s", len(products))
            return {
                'success': False,
                'error': str(e),
                'total': len(products),
                'success_count': 0,
                'fail_count': len(products)
            }

    def build_payload_preview(self, products, max_items=3):
        payload = []
        errors = []
        for idx, product in enumerate(products):
            try:
                payload.append(self._prepare_sync_data(product))
            except Exception as e:
                errors.append({
                    'index': idx,
                    'offer_id': (product or {}).get('offer_id') if isinstance(product, dict) else None,
                    'reason': str(e)
                })
        return {
            'payload_total': len(payload),
            'payload_preview': payload[:max_items],
            'errors': errors
        }
    
    CHARGE_TYPE_TO_CHARGE_MODE = {0: 2, 1: 1, 2: 3}

    def batch_update_delivery_template(self, products, access_token='', tenant_id=''):
        config = self._get_config()
        if not config['erp_api_url']:
            return {'success': False, 'error': 'ERP API未配置'}

        request_items = []
        skipped_details = []
        for product in products or []:
            offer_id = self._to_str(product.get('offer_id')) if isinstance(product, dict) else ''
            title = self._to_str(product.get('title')) if isinstance(product, dict) else ''
            delivery_template = self._build_delivery_template(product)
            if delivery_template is None:
                skipped_details.append({
                    'offer_id': offer_id,
                    'title': title,
                    'reason': '商品无物流信息，无法构建deliveryTemplate'
                })
                continue
            request_items.append({
                'channelProductId': offer_id,
                'deliveryTemplate': delivery_template
            })

        if not request_items:
            return {
                'success': False,
                'error': '没有可操作的商品（所有商品均缺少物流信息）',
                'total': len(products),
                'skipped_count': len(skipped_details),
                'skipped_details': skipped_details
            }

        headers = {'Content-Type': 'application/json'}
        if access_token:
            headers['accessToken'] = access_token
            headers['Authorization'] = f'Bearer {access_token}'
        if tenant_id is not None and str(tenant_id).strip() != '':
            headers['tenant-id'] = str(tenant_id)
            headers['tenantId'] = str(tenant_id)

        endpoint = f"{config['erp_api_url']}/admin-api/product/spu/batch-update-delivery-template-task"
        try:
            response = self._request_with_refresh_retry(
                'PUT', endpoint, json=request_items, headers=headers, timeout=60
            )
            logger.info("[ERP DELIVERY TEMPLATE] Response status=%s", response.status_code)
            resp_data = {}
            try:
                resp_data = response.json()
            except Exception:
                return {'success': False, 'error': f'ERP返回非JSON: {response.text[:500]}'}

            if response.status_code >= 400 or resp_data.get('code', 0) != 0:
                error_msg = resp_data.get('msg') or resp_data.get('message') or f'HTTP {response.status_code}'
                return {
                    'success': False,
                    'error': error_msg,
                    'total': len(products),
                    'submitted_count': len(request_items),
                    'skipped_count': len(skipped_details),
                    'skipped_details': skipped_details
                }

            task_data = resp_data.get('data') or []
            result = {
                'success': True,
                'total': len(products),
                'submitted_count': len(request_items),
                'skipped_count': len(skipped_details),
                'skipped_details': skipped_details,
                'task_data': task_data
            }
            if isinstance(task_data, list) and len(task_data) > 0:
                first_task = task_data[0] if isinstance(task_data[0], dict) else {}
                result['task_no'] = first_task.get('taskNo', '')
                result['pending_count'] = first_task.get('pendingCount', 0)
                result['immediate_fail_count'] = first_task.get('immediateFailCount', 0)
                result['immediate_fail_details'] = first_task.get('immediateFailDetails', [])
                result['immediate_success_details'] = first_task.get('immediateSuccessDetails', [])
            return result
        except requests.exceptions.Timeout:
            return {'success': False, 'error': '请求ERP超时'}
        except requests.exceptions.ConnectionError as e:
            return {'success': False, 'error': f'连接ERP失败: {str(e)}'}
        except Exception as e:
            logger.exception("[ERP DELIVERY TEMPLATE] Unexpected error")
            return {'success': False, 'error': str(e)}

    def _build_delivery_template(self, product):
        shipping_info_str = product.get('product_shipping_info', '')
        if not shipping_info_str:
            return None
        shipping_info = self._parse_json_field(shipping_info_str)
        if not isinstance(shipping_info, dict):
            return None

        freight_templates = shipping_info.get('freightTemplate', [])
        if not freight_templates:
            return None

        first_template = freight_templates[0] if isinstance(freight_templates, list) else freight_templates
        if not isinstance(first_template, dict):
            return None

        template_id = first_template.get('id')
        express_sub = first_template.get('expressSubTemplate', {})
        if not isinstance(express_sub, dict):
            return None

        sub_dto = express_sub.get('subTemplateDTO', {})
        charge_type = self._to_int(sub_dto.get('chargeType'), default=1) if isinstance(sub_dto, dict) else 1
        charge_mode = self.CHARGE_TYPE_TO_CHARGE_MODE.get(charge_type, 1)

        rate_list = express_sub.get('rateList', [])
        charges = []
        is_weight_mode = charge_type == 0
        if isinstance(rate_list, list):
            for rate in rate_list:
                if not isinstance(rate, dict):
                    continue
                to_area = self._to_str(rate.get('toAreaCodeText', ''))
                rate_dto = rate.get('rateDTO', {})
                if not isinstance(rate_dto, dict):
                    rate_dto = {}
                start_count = self._to_float(rate_dto.get('firstUnit', 0), default=0.0)
                extra_count = self._to_float(rate_dto.get('nextUnit', 0), default=0.0)
                if is_weight_mode:
                    start_count = round(start_count / 1000, 4)
                    extra_count = round(extra_count / 1000, 4)
                charges.append({
                    'areaNames': to_area,
                    'startCount': start_count,
                    'startPrice': self._to_int(rate_dto.get('firstUnitFee', 0), default=0),
                    'extraCount': extra_count,
                    'extraPrice': self._to_int(rate_dto.get('nextUnitFee', 0), default=0),
                })

        if not charges:
            return None

        return {
            'name': str(template_id) if template_id is not None else '',
            'chargeMode': charge_mode,
            'sort': 0,
            'charges': charges,
        }

    def _build_logistics_sku_lookup(self, product):
        """从 official_logistics_sku_info 构建 sku_id -> {weight, volume} 的查找表
        
        数据来源: import_product.official_logistics_sku_info 列
        JSON 结构: {"officialLogisticsSkuInfoModels": [{"skuId": ..., "weight": 900.0, "volume": 0.0, ...}]}
        单位: weight 为克(g), volume 为立方厘米(cm³)
        """
        lookup = {}
        raw = product.get('official_logistics_sku_info', '')
        if not raw:
            return lookup
        logistics_data = self._parse_json_field(raw)
        if not isinstance(logistics_data, dict):
            return lookup
        models = logistics_data.get('officialLogisticsSkuInfoModels', [])
        if not isinstance(models, list):
            return lookup
        for item in models:
            if not isinstance(item, dict):
                continue
            sku_id = str(item.get('skuId', '')).strip()
            if not sku_id:
                continue
            weight_raw = item.get('weight')
            volume_raw = item.get('volume')
            weight_g = None
            volume_cm3 = None
            if weight_raw is not None and str(weight_raw).strip() != '':
                try:
                    weight_g = float(weight_raw)
                except (ValueError, TypeError):
                    pass
            if volume_raw is not None and str(volume_raw).strip() != '':
                try:
                    volume_cm3 = float(volume_raw)
                except (ValueError, TypeError):
                    pass
            lookup[sku_id] = {
                'weight_g': weight_g,
                'volume_cm3': volume_cm3
            }
        if lookup:
            logger.info(
                "[ERP SYNC] Built logistics lookup for offer_id=%s, %d SKU entries",
                product.get('offer_id'),
                len(lookup)
            )
        return lookup

    def _convert_weight_g_to_kg(self, weight_g):
        """将重量从克(g)转换为千克(kg)"""
        if weight_g is None:
            return 0.0
        try:
            weight_g = float(weight_g)
        except (ValueError, TypeError):
            return 0.0
        if weight_g <= 0:
            return 0.0
        weight_kg = round(weight_g / 1000.0, 4)
        return weight_kg

    def _convert_volume_cm3_to_m3(self, volume_cm3):
        """将体积从立方厘米(cm³)转换为立方米(m³)"""
        if volume_cm3 is None:
            return 0.0
        try:
            volume_cm3 = float(volume_cm3)
        except (ValueError, TypeError):
            return 0.0
        if volume_cm3 <= 0:
            return 0.0
        volume_m3 = round(volume_cm3 / 1000000.0, 8)
        return volume_m3

    def _prepare_sync_data(self, product):
        sku_info = self._parse_json_field(product.get('sku_info', ''))
        images = self._parse_json_field(product.get('images', ''))

        cost_price = self._to_float(product.get('cost_price', 0), default=self._to_float(product.get('price', 0), default=0.0))
        sell_price = self._to_float(product.get('sell_price', 0), default=round(cost_price * 1.15, 2))

        # 从 official_logistics_sku_info 构建物流信息查找表
        logistics_lookup = self._build_logistics_sku_lookup(product)
        
        sku_list = []
        if isinstance(sku_info, list):
            for sku in sku_info:
                if not isinstance(sku, dict):
                    continue
                sku_id = self._normalize_optional_id(sku.get('sku_id'))
                spec_id = self._normalize_optional_id(sku.get('spec_id'))
                sku_name = self._to_str(sku.get('name', '')) or self._to_str(sku.get('sku_name', '')) or self._to_str(product.get('title', ''))
                sku_properties = self._normalize_sku_properties(sku.get('properties') or sku.get('attributes'))
                sku_pic_url = self._extract_sku_pic_url(sku_properties)
                if not sku_pic_url:
                    sku_pic_url = self._to_str(sku.get('pic_url', '')) or self._to_str(sku.get('image_url', '')) or self._to_str(product.get('image_url', ''))
                sku_consign_price_cents = self._price_to_cents(
                    sku.get('consign_price', sku.get('consignPrice', sku.get('price', sell_price)))
                )

                # 从 official_logistics_sku_info 获取 weight 和 volume
                sku_weight_kg = 0.0
                sku_volume_m3 = 0.0
                matched_logistics = None
                if logistics_lookup and sku_id:
                    matched_logistics = logistics_lookup.get(str(sku_id).strip())
                if matched_logistics:
                    weight_g = matched_logistics.get('weight_g')
                    volume_cm3 = matched_logistics.get('volume_cm3')
                    sku_weight_kg = self._convert_weight_g_to_kg(weight_g)
                    sku_volume_m3 = self._convert_volume_cm3_to_m3(volume_cm3)
                    logger.info(
                        "[ERP SYNC] SKU logistics matched: offer_id=%s, sku_id=%s, weight_g=%s -> weight_kg=%s, volume_cm3=%s -> volume_m3=%s",
                        product.get('offer_id'), sku_id, weight_g, sku_weight_kg, volume_cm3, sku_volume_m3
                    )
                else:
                    # 尝试从 sku_info 自身的 weight/volume 字段获取（兜底）
                    raw_weight = sku.get('weight', 0)
                    raw_volume = sku.get('volume', 0)
                    if raw_weight and float(raw_weight or 0) > 0:
                        # 如果 sku_info 中的 weight 大于 10，视为克；否则视为千克
                        w = float(raw_weight)
                        sku_weight_kg = round(w / 1000.0, 4) if w > 10 else w
                    if raw_volume and float(raw_volume or 0) > 0:
                        v = float(raw_volume)
                        sku_volume_m3 = round(v / 1000000.0, 8) if v > 0.001 else v
                    if logistics_lookup:
                        logger.info(
                            "[ERP SYNC] SKU logistics not matched, using fallback: offer_id=%s, sku_id=%s, weight_kg=%s, volume_m3=%s",
                            product.get('offer_id'), sku_id, sku_weight_kg, sku_volume_m3
                        )

                sku_item = {
                    'id': self._to_nullable_int(sku_id),
                    'name': sku_name,
                    'price': sku_consign_price_cents,
                    'marketPrice': sku_consign_price_cents,
                    'costPrice': sku_consign_price_cents,
                    'barCode': self._to_str(sku.get('bar_code', '')) or self._to_str(sku_id or ''),
                    'picUrl': sku_pic_url,
                    'stock': self._to_int(sku.get('stock', 0), default=0, minimum=0),
                    'weight': sku_weight_kg,
                    'volume': sku_volume_m3,
                    'firstBrokeragePrice': self._price_to_cents(sku.get('first_brokerage_price', 0)),
                    'secondBrokeragePrice': self._price_to_cents(sku.get('second_brokerage_price', 0)),
                    'channelSkuId': self._to_str(sku_id or sku.get('sku_id', '')),
                    'properties': sku_properties
                }
                if sku_item['id'] is None and not sku_item['barCode']:
                    logger.warning(
                        "[ERP SYNC] Skip empty sku item, offer_id=%s, raw_sku=%s",
                        product.get('offer_id'),
                        json.dumps(sku, ensure_ascii=False)[:300]
                    )
                    continue
                sku_list.append(sku_item)
        
        product_offer_id = product.get('offer_id')
        source_product_id = self._normalize_required_id(product_offer_id)
        if source_product_id is None:
            raise ValueError(f"offer_id 为空或非法，无法构造ERP请求体: {product_offer_id}")

        normalized_images = [str(img).strip() for img in images if isinstance(img, str) and str(img).strip()] if isinstance(images, list) else []
        main_image = self._to_str(product.get('image_url', ''))
        title = self._to_str(product.get('title', ''))
        product_id = self._to_nullable_int(source_product_id)
        if not sku_list:
            fallback_price = self._price_to_cents(product.get('sell_price', product.get('price', 0)))
            # 尝试从 logistics_lookup 获取第一个 SKU 的物流信息作为默认值
            fallback_weight_kg = 0.0
            fallback_volume_m3 = 0.0
            if logistics_lookup:
                first_logistics = next(iter(logistics_lookup.values()), None)
                if first_logistics:
                    fallback_weight_kg = self._convert_weight_g_to_kg(first_logistics.get('weight_g'))
                    fallback_volume_m3 = self._convert_volume_cm3_to_m3(first_logistics.get('volume_cm3'))
                    logger.info(
                        "[ERP SYNC] Fallback SKU using first logistics entry: offer_id=%s, weight_kg=%s, volume_m3=%s",
                        product.get('offer_id'), fallback_weight_kg, fallback_volume_m3
                    )
            sku_list.append({
                'id': product_id,
                'name': title or '默认SKU',
                'price': fallback_price,
                'marketPrice': fallback_price,
                'costPrice': fallback_price,
                'barCode': self._to_str(product.get('offer_id', '')),
                'picUrl': main_image,
                'stock': self._to_int(product.get('stock', 999), default=999, minimum=0),
                'weight': fallback_weight_kg,
                'volume': fallback_volume_m3,
                'firstBrokeragePrice': 0,
                'secondBrokeragePrice': 0,
                'properties': [
                    {
                        'propertyName': '默认',
                        'sku_image_url': main_image,
                        'valueName': '默认'
                    }
                ]
            })
        category_result = self._resolve_sync_category(product)
        category = category_result.get('category')
        if not category:
            raise ValueError(category_result.get('error') or '未匹配到有效ERP类目，请先同步分类并维护商品类目映射')
        category_id = category.get('id')
        keyword = (
            self._to_str(product.get('erp_category_name', ''))
            or self._to_str(product.get('category_name', ''))
            or self._to_str(product.get('tags', ''))
            or title
        )
        # 使用数据库中的 description 字段（商品详情HTML）
        description = self._to_str(product.get('description', '')) or title or keyword
        introduction = (title or keyword)[:255]
        slider_pic_urls = normalized_images if normalized_images else ([main_image] if main_image else [])
        import_product_id = self._to_nullable_int(product.get('id'))
        extends_spu_id = self._to_nullable_int(product_offer_id)
        extends_info = self._build_extends_info(product)

        return {
            'id': product_id,
            'name': title,
            'keyword': keyword,
            'introduction': introduction,
            'description': description,
            'categoryId': category_id,
            'brandId': 1,
            'picUrl': main_image,
            'sliderPicUrls': slider_pic_urls,
            'sort': 1,
            'goodsType': 0,
            'specType': True,
            'deliveryTypes': [1],
            'deliveryTemplateId': 111,
            'deliveryTemplate': self._build_delivery_template(product),
            'giveIntegral': 0,
            'subCommissionType': True,
            'virtualSalesCount': 66,
            'salesCount': self._to_int(product.get('sales_count', 0), default=0, minimum=0),
            'browseCount': 1999,
            'skus': sku_list,
            'shoppable': self._to_bool(product.get('shoppable', True)),
            'lotteryable': self._to_bool(product.get('lotteryable', True)),
            'exchangeable': self._to_bool(product.get('exchangeable', True)),
            'returnable': self._to_bool(product.get('returnable', self._to_bool(product.get('support_return', 0)))),
            'validityType': self._to_int(product.get('validity_type', 0), default=0, minimum=0),
            'validStartTime': self._to_str(product.get('valid_start_time', '')),
            'validEndTime': self._to_str(product.get('valid_end_time', '')),
            'fixedStartTerm': self._to_int(product.get('fixed_start_term', 0), default=0, minimum=0),
            'fixedEndTerm': self._to_int(product.get('fixed_end_term', 0), default=0, minimum=0),
            'sourceType': self._to_str(product.get('source_type', '')),
            'shopName': self._to_str(product.get('shop_name') or product.get('supplier_name') or ''),
            'extendsInfo': extends_info
        }

    def _build_extends_info(self, product):
        return [{
            'type': 'store_service_specification',
            'value': self._extract_buyer_protection_values(product)
        }]

    def _extract_buyer_protection_values(self, product):
        if not isinstance(product, dict):
            return []

        extend_infos = self._parse_json_field(
            product.get('productExtendInfos')
            or product.get('product_extend_infos')
            or product.get('productExtendInfo')
        )
        if not isinstance(extend_infos, list):
            logger.warning(
                "[ERP SYNC] productExtendInfos is not a list. offer_id=%s raw_type=%s",
                product.get('offer_id'),
                type(extend_infos).__name__
            )
            return []

        buyer_protection_value = None
        for item in extend_infos:
            if not isinstance(item, dict):
                continue
            if self._to_str(item.get('key')) == 'buyerProtection':
                buyer_protection_value = item.get('value')
                break

        if buyer_protection_value in (None, ''):
            logger.info(
                "[ERP SYNC] buyerProtection not found or empty. offer_id=%s",
                product.get('offer_id')
            )
            return []

        if isinstance(buyer_protection_value, list):
            return [self._to_str(item) for item in buyer_protection_value if self._to_str(item)]

        if isinstance(buyer_protection_value, str):
            try:
                parsed = json.loads(buyer_protection_value)
            except Exception:
                logger.warning(
                    "[ERP SYNC] buyerProtection JSON parse failed. offer_id=%s raw_value=%r",
                    product.get('offer_id'),
                    buyer_protection_value
                )
                return []
            if isinstance(parsed, list):
                return [self._to_str(item) for item in parsed if self._to_str(item)]
            logger.warning(
                "[ERP SYNC] buyerProtection parsed value is not a list. offer_id=%s parsed_type=%s",
                product.get('offer_id'),
                type(parsed).__name__
            )
            return []

        logger.warning(
            "[ERP SYNC] buyerProtection value has unsupported type. offer_id=%s value_type=%s",
            product.get('offer_id'),
            type(buyer_protection_value).__name__
        )
        return []
    
    def _parse_json_field(self, value):
        if not value:
            return []
        if isinstance(value, (list, dict)):
            return value
        try:
            return json.loads(value)
        except:
            return []

    def _to_str(self, value, default=''):
        if value is None:
            return default
        return str(value).strip()

    def _to_float(self, value, default=0.0):
        try:
            if value is None or value == '':
                return float(default)
            return float(value)
        except Exception:
            return float(default)

    def _safe_decimal_non_negative(self, value, default='0.00', field_name='', offer_id='', sku_id=''):
        try:
            if value is None or (isinstance(value, str) and value.strip() == ''):
                raise ValueError('empty')
            amount = Decimal(str(value).strip())
        except (InvalidOperation, ValueError, TypeError):
            amount = Decimal(str(default))
            if field_name:
                logger.warning(
                    "[Batch Update Price] Invalid %s detected, fallback to %s. offer_id=%s sku_id=%s raw_value=%r",
                    field_name,
                    amount,
                    offer_id,
                    sku_id or '',
                    value
                )
        if amount < 0:
            logger.warning(
                "[Batch Update Price] Negative %s detected, fallback to 0. offer_id=%s sku_id=%s raw_value=%r",
                field_name or 'amount',
                offer_id,
                sku_id or '',
                value
            )
            amount = Decimal('0.00')
        return amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    def _calculate_market_price(self, base_price, freight):
        base_amount = self._safe_decimal_non_negative(base_price)
        return (base_amount * Decimal('1.7')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    def _to_int(self, value, default=0, minimum=None):
        try:
            if value is None or value == '':
                num = int(default)
            else:
                num = int(float(value))
        except Exception:
            num = int(default)
        if minimum is not None:
            return max(minimum, num)
        return num

    def _to_nullable_int(self, value):
        if value is None:
            return None
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip() == '':
            return None
        if isinstance(value, str):
            value_str = value.strip()
            if re.fullmatch(r'-?\d+', value_str):
                return int(value_str)
            if re.fullmatch(r'-?\d+\.0+', value_str):
                return int(value_str.split('.', 1)[0])
        try:
            return int(value)
        except Exception:
            return None

    def _price_to_cents(self, value):
        amount = self._to_float(value, default=0.0)
        return int(round(amount * 100))

    def _to_bool(self, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in ('1', 'true', 'yes', 'y', 'on'):
                return True
            if normalized in ('0', 'false', 'no', 'n', 'off', ''):
                return False
        return bool(value)

    def _normalize_optional_id(self, value):
        if value is None:
            return None
        value_str = str(value).strip()
        return value_str if value_str else None

    def _normalize_required_id(self, value):
        value_str = self._normalize_optional_id(value)
        if value_str is None:
            return None
        return value_str

    def _normalize_sku_properties(self, value):
        if value is None:
            return []
        source_list = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
        properties = []
        for item in source_list:
            if not isinstance(item, dict):
                continue
            properties.append({
                'propertyName': self._to_str(item.get('property_name') or item.get('propertyName') or item.get('name')),
                'valueName': self._to_str(item.get('value_name') or item.get('valueName') or item.get('value')),
                'sku_image_url': self._to_str(item.get('sku_image_url') or item.get('skuImageUrl') or item.get('imageUrl'))
            })
        return properties

    def _extract_sku_pic_url(self, properties):
        if not isinstance(properties, list):
            return ''
        for item in properties:
            if not isinstance(item, dict):
                continue
            image_url = self._to_str(item.get('sku_image_url') or item.get('skuImageUrl') or item.get('imageUrl'))
            if image_url:
                return image_url
        return ''

    def _normalize_delivery_types(self, value):
        if isinstance(value, list):
            normalized = []
            for item in value:
                if item is None or str(item).strip() == '':
                    continue
                delivery_type = self._to_int(item, default=0, minimum=0)
                if delivery_type > 0:
                    normalized.append(delivery_type)
            return normalized or [1]
        if isinstance(value, (int, float)):
            delivery_type = self._to_int(value, default=1, minimum=0)
            return [delivery_type] if delivery_type > 0 else [1]
        if isinstance(value, str):
            value_str = value.strip()
            if not value_str:
                return [1]
            if value_str.startswith('['):
                try:
                    parsed = json.loads(value_str)
                    if isinstance(parsed, list):
                        normalized = []
                        for item in parsed:
                            if item is None or str(item).strip() == '':
                                continue
                            delivery_type = self._to_int(item, default=0, minimum=0)
                            if delivery_type > 0:
                                normalized.append(delivery_type)
                        return normalized or [1]
                except Exception:
                    pass
            delivery_type = self._to_int(value_str, default=1, minimum=0)
            return [delivery_type] if delivery_type > 0 else [1]
        return [1]
    
    def _call_erp_api(self, sync_data_list, config, access_token='', tenant_id=''):
        headers = {
            'Content-Type': 'application/json'
        }
        if access_token:
            headers['accessToken'] = access_token
            headers['Authorization'] = f'Bearer {access_token}'
        if tenant_id is not None and str(tenant_id).strip() != '':
            tenant_value = str(tenant_id)
            headers['tenant-id'] = tenant_value
            headers['tenantId'] = tenant_value
        
        if config['erp_api_key'] and 'Authorization' not in headers:
            headers['Authorization'] = f'Bearer {config["erp_api_key"]}'
        
        try:
            endpoint = f"{config['erp_api_url']}/admin-api/product/spu/batch-create-task"
            sample_offer_ids = [item.get('name') for item in sync_data_list[:5] if isinstance(item, dict)]
            request_meta = {
                'endpoint': endpoint,
                'batch_size': len(sync_data_list),
                'sample_offer_ids': sample_offer_ids,
                'has_access_token': bool(access_token),
                'has_tenant_id': bool(str(tenant_id).strip()),
                'payload_preview': [
                    {
                        'id': item.get('id'),
                        'name': item.get('name'),
                        'categoryId': item.get('categoryId'),
                        'sku_count': len(item.get('skus') or [])
                    }
                    for item in sync_data_list[:2]
                    if isinstance(item, dict)
                ]
            }
            logger.info(
                "[ERP SYNC] Request endpoint=%s batch_size=%s tenant_id=%s sample_offer_ids=%s",
                endpoint,
                len(sync_data_list),
                tenant_id if str(tenant_id).strip() else "N/A",
                sample_offer_ids
            )
            request_body_str = json.dumps(sync_data_list, ensure_ascii=False, cls=DecimalEncoder)
            print(f"[ERP API] Request body: {request_body_str}")
            response = self._request_with_refresh_retry(
                'POST',
                endpoint,
                json=sync_data_list,
                headers=headers,
                timeout=60
            )
            logger.info("[ERP SYNC] Response status=%s", response.status_code)
            response_text = response.text or ''
            print(f"[ERP API] Response status: {response.status_code}")
            print(f"[ERP API] Response body: {response_text}")
            parsed = self._parse_erp_task_submit_response(response)
            parsed['request_meta'] = request_meta
            return parsed
        except requests.exceptions.Timeout:
            logger.exception("[ERP SYNC] Request timeout endpoint=%s", endpoint)
            return {'success': False, 'error': '请求超时'}
        except requests.exceptions.ConnectionError as e:
            logger.exception("[ERP SYNC] Connection error endpoint=%s", endpoint)
            return {'success': False, 'error': f'连接失败: {str(e)}'}
        except Exception as e:
            logger.exception("[ERP SYNC] Unexpected exception endpoint=%s", endpoint)
            return {'success': False, 'error': str(e)}

    def _is_erp_delete_not_found_message(self, message):
        normalized = self._to_str(message).lower()
        if not normalized:
            return False
        keywords = (
            '不存在',
            '未找到',
            'not found',
            'not exist',
            'does not exist',
            '已删除',
            'spu不存在',
            '商品不存在'
        )
        return any(keyword in normalized for keyword in keywords)

    def delete_product(self, product, access_token='', tenant_id=''):
        config = self._get_config()
        if not config['erp_api_url']:
            return {
                'success': False,
                'code': 'ERP_DELETE_SERVICE_ERROR',
                'error': 'ERP 服务异常，请稍后重试'
            }

        offer_id = self._normalize_required_id((product or {}).get('offer_id'))
        spu_id = self._to_nullable_int(offer_id)
        if spu_id is None:
            return {
                'success': False,
                'code': 'ERP_DELETE_INVALID_ID',
                'error': '商品 ERP 标识无效，无法删除'
            }

        headers = {'Content-Type': 'application/json'}
        if access_token:
            headers['accessToken'] = access_token
            headers['Authorization'] = f'Bearer {access_token}'
        if tenant_id is not None and str(tenant_id).strip() != '':
            tenant_value = str(tenant_id).strip()
            headers['tenant-id'] = tenant_value
            headers['tenantId'] = tenant_value
        if config['erp_api_key'] and 'Authorization' not in headers:
            headers['Authorization'] = f'Bearer {config["erp_api_key"]}'

        endpoint = f"{config['erp_api_url'].rstrip('/')}/admin-api/product/spu/batch-delete"
        payload = {'ids': [spu_id]}

        try:
            response = self._request_with_refresh_retry(
                'PUT',
                endpoint,
                json=payload,
                headers=headers,
                timeout=30
            )
            response_text = response.text or ''
            try:
                body = response.json()
            except Exception:
                body = {}

            message = ''
            if isinstance(body, dict):
                message = body.get('msg') or body.get('message') or ''
            if not message:
                message = response_text[:200]

            if response.status_code == 404 or self._is_erp_delete_not_found_message(message):
                return {
                    'success': True,
                    'not_found': True,
                    'message': message or 'ERP 商品不存在',
                    'status_code': response.status_code,
                    'raw': body if isinstance(body, dict) else response_text
                }

            if response.status_code in (200, 201) and isinstance(body, dict) and str(body.get('code')) == '0':
                return {
                    'success': True,
                    'not_found': False,
                    'message': message or 'ERP 删除成功',
                    'status_code': response.status_code,
                    'raw': body
                }

            return {
                'success': False,
                'code': 'ERP_DELETE_SERVICE_ERROR',
                'error': message or 'ERP 服务异常，请稍后重试',
                'status_code': response.status_code,
                'raw': body if isinstance(body, dict) else response_text
            }
        except requests.exceptions.Timeout:
            logger.exception("[ERP DELETE] Request timeout offer_id=%s", offer_id)
            return {
                'success': False,
                'code': 'ERP_DELETE_SERVICE_ERROR',
                'error': 'ERP 服务异常，请稍后重试'
            }
        except requests.exceptions.ConnectionError:
            logger.exception("[ERP DELETE] Connection error offer_id=%s", offer_id)
            return {
                'success': False,
                'code': 'ERP_DELETE_SERVICE_ERROR',
                'error': 'ERP 服务异常，请稍后重试'
            }
        except Exception as exc:
            logger.exception("[ERP DELETE] Unexpected error offer_id=%s", offer_id)
            return {
                'success': False,
                'code': 'ERP_DELETE_SERVICE_ERROR',
                'error': 'ERP 服务异常，请稍后重试',
                'detail': str(exc)
            }

    def query_batch_create_progress(self, task_no, access_token='', tenant_id=''):
        config = self._get_config()
        if not config['erp_api_url']:
            return {'success': False, 'error': 'ERP API未配置，请设置环境变量 ERP_API_URL'}
        headers = {'Content-Type': 'application/json'}
        if access_token:
            headers['accessToken'] = access_token
            headers['Authorization'] = f'Bearer {access_token}'
        if tenant_id is not None and str(tenant_id).strip() != '':
            tenant_value = str(tenant_id)
            headers['tenant-id'] = tenant_value
            headers['tenantId'] = tenant_value
        if config['erp_api_key'] and 'Authorization' not in headers:
            headers['Authorization'] = f'Bearer {config["erp_api_key"]}'
        endpoint = f"{config['erp_api_url']}/admin-api/product/spu/batch-create/progress"
        try:
            response = self._request_with_refresh_retry('GET', endpoint, params={'taskNo': task_no}, headers=headers, timeout=30)
            return self._parse_erp_task_progress_response(response)
        except requests.exceptions.Timeout:
            return {'success': False, 'error': '请求超时'}
        except requests.exceptions.ConnectionError as e:
            return {'success': False, 'error': f'连接失败: {str(e)}'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _parse_erp_response(self, response):
        raw_text = (response.text or '').strip()
        body = None
        if raw_text:
            try:
                body = response.json()
            except Exception:
                body = raw_text

        if response.status_code not in (200, 201):
            return {
                'success': False,
                'error': self._extract_error_message(body) or f'HTTP {response.status_code}: {raw_text[:200]}'
            }

        success = self._is_success_body(body)
        if success:
            batch_stats = self._extract_batch_stats(body)
            return {
                'success': True,
                'message': self._extract_success_message(body) or '创建成功',
                'raw': body,
                'total': batch_stats.get('total'),
                'success_count': batch_stats.get('success_count'),
                'fail_count': batch_stats.get('fail_count'),
                'failed_details': batch_stats.get('failed_details')
            }

        batch_stats = self._extract_batch_stats(body)
        return {
            'success': False,
            'error': self._extract_error_message(body) or '接口返回成功状态码但业务结果失败',
            'raw': body,
            'total': batch_stats.get('total'),
            'success_count': batch_stats.get('success_count'),
            'fail_count': batch_stats.get('fail_count'),
            'failed_details': batch_stats.get('failed_details')
        }

    def _is_success_body(self, body):
        if body is None:
            return True
        if isinstance(body, bool):
            return body
        if isinstance(body, dict):
            if isinstance(body.get('success'), bool):
                return body.get('success')
            code = body.get('code')
            if code is not None:
                return str(code) in ('0', '200', '201')
            if isinstance(body.get('data'), bool):
                return body.get('data')
            if isinstance(body.get('result'), bool):
                return body.get('result')
        return False

    def _extract_success_message(self, body):
        if isinstance(body, dict):
            return body.get('msg') or body.get('message')
        return None

    def _extract_error_message(self, body):
        if isinstance(body, dict):
            return (
                body.get('msg')
                or body.get('message')
                or body.get('errorMsg')
                or body.get('error')
            )
        if isinstance(body, str):
            return body[:200]
        return None

    def batch_update_price_by_local_mapping(self, products, access_token='', tenant_id='', max_retries=3, timeout=60):
        config = self._get_config()
        if not config['erp_api_url']:
            return {'success': False, 'error': 'ERP API未配置，请设置环境变量 ERP_API_URL'}

        spu_price_list = []
        sku_price_list = []
        valid_offer_ids = []

        for product in products or []:
            if not isinstance(product, dict):
                continue

            offer_id = str(product.get('offer_id', '')).strip()
            if not offer_id:
                continue

            spu_sell_price = self._to_float(product.get('sell_price', 0), default=0.0)
            spu_adjusted_price = self._to_float(product.get('adjusted_price', 0), default=0.0)
            spu_target_price = spu_adjusted_price if spu_adjusted_price > 0 else spu_sell_price
            spu_market_price = spu_adjusted_price if spu_adjusted_price > 0 else spu_sell_price
            valid_offer_ids.append(offer_id)
            spu_price_list.append({
                'id': offer_id,
                'price': self._price_to_cents(spu_target_price),
                'marketPrice': self._price_to_cents(spu_market_price)
            })

            sku_info = product.get('sku_info') or []
            if isinstance(sku_info, str):
                try:
                    sku_info = json.loads(sku_info)
                except Exception:
                    sku_info = []
            if not isinstance(sku_info, list):
                continue

            for sku in sku_info:
                if not isinstance(sku, dict):
                    continue
                sku_id = sku.get('skuId') or sku.get('sku_id') or sku.get('specId') or sku.get('spec_id') or ''
                sku_id = str(sku_id).strip()
                if not sku_id:
                    continue
                sku_adjusted_price = self._to_float(
                    sku.get('adjusted_price', sku.get('adjustedPrice', 0)),
                    default=0.0
                )
                sku_market_price = self._to_float(
                    sku.get('marketPrice', sku.get('market_price', 0)),
                    default=0.0
                )
                sku_price_list.append({
                    'channelSkuId': sku_id,
                    'price': self._price_to_cents(sku_adjusted_price),
                    'marketPrice': self._price_to_cents(sku_market_price)
                })

        if not spu_price_list:
            return {
                'success': False,
                'error': '没有有效的已同步商品数据',
                'offer_ids': [],
                'spu_count': 0,
                'sku_count': 0
            }

        request_body = {
            'spuPriceList': spu_price_list,
            'skuPriceList': sku_price_list
        }
        logger.info(
            "[Fix Batch Adjust] request preview=%s",
            json.dumps(
                {
                    'spuPriceList': spu_price_list[:3],
                    'skuPriceList': sku_price_list[:5]
                },
                ensure_ascii=False,
                cls=DecimalEncoder
            )
        )

        headers = {
            'Content-Type': 'application/json'
        }
        if access_token:
            headers['accessToken'] = access_token
            headers['Authorization'] = f'Bearer {access_token}'
        if tenant_id is not None and str(tenant_id).strip() != '':
            tenant_value = str(tenant_id)
            headers['tenant-id'] = tenant_value
            headers['tenantId'] = tenant_value
        if config['erp_api_key'] and 'Authorization' not in headers:
            headers['Authorization'] = f'Bearer {config["erp_api_key"]}'

        endpoint = f"{config['erp_api_url']}/admin-api/product/sku/batch-update-price-task"
        last_error = '请求失败'
        last_body = {}

        for attempt in range(1, max(1, int(max_retries)) + 1):
            try:
                logger.info(
                    "[Fix Batch Adjust] attempt=%s endpoint=%s spu_count=%s sku_count=%s",
                    attempt,
                    endpoint,
                    len(spu_price_list),
                    len(sku_price_list)
                )
                response = self._request_with_refresh_retry(
                    'PUT',
                    endpoint,
                    json=request_body,
                    headers=headers,
                    timeout=timeout
                )
                response_text = response.text or ''
                logger.info("[Fix Batch Adjust] status=%s body=%s", response.status_code, response_text[:1000])

                try:
                    body = response.json() if response_text else {}
                except Exception:
                    body = {}

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
                        'message': body.get('msg') or body.get('message') or '已提交批量调价任务',
                        'offer_ids': valid_offer_ids,
                        'spu_count': len(spu_price_list),
                        'sku_count': len(sku_price_list),
                        'task_nos': task_nos,
                        'request_body': request_body
                    }

                last_error = self._extract_error_message(body) or (
                    f'HTTP {response.status_code}: {response_text[:200]}'
                    if response.status_code not in (200, 201)
                    else 'ERP返回失败'
                )
            except requests.exceptions.Timeout:
                logger.exception("[Fix Batch Adjust] Request timeout attempt=%s", attempt)
                last_error = '请求超时'
            except requests.exceptions.ConnectionError as e:
                logger.exception("[Fix Batch Adjust] Connection error attempt=%s", attempt)
                last_error = f'连接失败: {str(e)}'
            except Exception as e:
                logger.exception("[Fix Batch Adjust] Unexpected exception attempt=%s", attempt)
                last_error = str(e)

        return {
            'success': False,
            'error': last_error,
            'offer_ids': valid_offer_ids,
            'spu_count': len(spu_price_list),
            'sku_count': len(sku_price_list),
            'response_body': last_body
        }

    def _extract_batch_stats(self, body):
        default_stats = {
            'total': None,
            'success_count': None,
            'fail_count': None,
            'failed_details': []
        }
        if not isinstance(body, dict):
            return default_stats

        data = body.get('data')
        if not isinstance(data, dict):
            return default_stats

        total = self._to_nullable_int(data.get('totalCount'))
        success_count = self._to_nullable_int(data.get('successCount'))
        fail_count = self._to_nullable_int(data.get('failCount'))

        failed_details = []
        details = data.get('failedDetails')
        if isinstance(details, list):
            for item in details:
                if not isinstance(item, dict):
                    continue
                failed_details.append({
                    'index': self._to_nullable_int(item.get('index')),
                    'name': self._to_str(item.get('name', '')),
                    'reason': self._to_str(item.get('reason', ''))
                })

        return {
            'total': total,
            'success_count': success_count,
            'fail_count': fail_count,
            'failed_details': failed_details
        }

    def _normalize_task_details(self, details):
        normalized = []
        if not isinstance(details, list):
            return normalized
        for item in details:
            if not isinstance(item, dict):
                continue
            normalized.append({
                'index': self._to_nullable_int(item.get('index')),
                'spuId': self._to_str(item.get('spuId') or item.get('id') or item.get('offerId')),
                'spuName': self._to_str(item.get('spuName') or item.get('name')),
                'reason': self._to_str(item.get('reason') or item.get('msg') or item.get('message'))
            })
        return normalized

    def _parse_erp_task_submit_response(self, response):
        raw_text = (response.text or '').strip()
        body = None
        if raw_text:
            try:
                body = response.json()
            except Exception:
                body = raw_text
        if response.status_code not in (200, 201):
            return {
                'success': False,
                'error': self._extract_error_message(body) or f'HTTP {response.status_code}: {raw_text[:200]}',
                'raw': body,
                'http_status': response.status_code
            }
        if not isinstance(body, dict):
            return {'success': False, 'error': 'ERP返回格式异常', 'raw': body, 'http_status': response.status_code}
        if str(body.get('code')) not in ('0', '200', '201'):
            return {
                'success': False,
                'error': self._extract_error_message(body) or 'ERP任务创建失败',
                'raw': body,
                'http_status': response.status_code
            }
        data = body.get('data') if isinstance(body.get('data'), dict) else {}
        task_no = self._to_str(data.get('taskNo'))
        if not task_no:
            return {'success': False, 'error': 'ERP未返回taskNo', 'raw': body, 'http_status': response.status_code}
        total_count = self._to_nullable_int(data.get('totalCount'))
        pending_count = self._to_nullable_int(data.get('pendingCount'))
        immediate_fail_count = self._to_nullable_int(data.get('immediateFailCount'))
        success_count = self._to_nullable_int(data.get('successCount'))
        fail_count = immediate_fail_count if immediate_fail_count is not None else self._to_nullable_int(data.get('failCount'))
        submitted_count = success_count
        if submitted_count is None and total_count is not None and fail_count is not None:
            submitted_count = max(total_count - fail_count, 0)
        if pending_count is None:
            pending_count = submitted_count
        failed_details = self._normalize_task_details(data.get('immediateFailDetails') or data.get('failedDetails'))
        return {
            'success': True,
            'async_task': True,
            'task_no': task_no,
            'message': self._extract_success_message(body) or '已提交批量创建任务',
            'total': total_count,
            'submitted_count': submitted_count,
            'pending_count': pending_count,
            'success_count': submitted_count,
            'fail_count': fail_count if fail_count is not None else 0,
            'failed_details': failed_details,
            'raw': body,
            'http_status': response.status_code
        }

    def _parse_erp_task_progress_response(self, response):
        raw_text = (response.text or '').strip()
        body = None
        if raw_text:
            try:
                body = response.json()
            except Exception:
                body = raw_text
        if response.status_code not in (200, 201):
            return {
                'success': False,
                'error': self._extract_error_message(body) or f'HTTP {response.status_code}: {raw_text[:200]}',
                'raw': body,
                'http_status': response.status_code
            }
        if not isinstance(body, dict):
            return {'success': False, 'error': 'ERP返回格式异常', 'raw': body, 'http_status': response.status_code}
        if str(body.get('code')) not in ('0', '200', '201'):
            return {
                'success': False,
                'error': self._extract_error_message(body) or '查询任务进度失败',
                'raw': body,
                'http_status': response.status_code
            }
        data = body.get('data') if isinstance(body.get('data'), dict) else {}
        failed_details = self._normalize_task_details(data.get('failedDetails'))
        success_details = self._normalize_task_details(data.get('successDetails'))
        return {
            'success': True,
            'async_task': True,
            'task_no': self._to_str(data.get('taskNo')),
            'task_status': self._to_int(data.get('taskStatus', 0), default=0, minimum=0),
            'task_status_desc': self._to_str(data.get('taskStatusDesc', '')),
            'total': self._to_nullable_int(data.get('totalCount')),
            'processed_count': self._to_nullable_int(data.get('processedCount')),
            'success_count': self._to_nullable_int(data.get('successCount')),
            'fail_count': self._to_nullable_int(data.get('failCount')),
            'failed_details': failed_details,
            'success_details': success_details,
            'raw': body,
            'http_status': response.status_code
        }

    def _with_delivery_types_array(self, payload_list):
        converted = []
        for item in payload_list:
            if not isinstance(item, dict):
                converted.append(item)
                continue
            new_item = dict(item)
            delivery_types = new_item.get('deliveryTypes')
            if isinstance(delivery_types, int):
                new_item['deliveryTypes'] = [delivery_types]
            converted.append(new_item)
        return converted

    def _with_id_null(self, payload_list):
        converted = []
        for item in payload_list:
            if not isinstance(item, dict):
                converted.append(item)
                continue
            new_item = dict(item)
            new_item['id'] = None
            skus = new_item.get('skus')
            if isinstance(skus, list):
                new_skus = []
                for sku in skus:
                    if not isinstance(sku, dict):
                        new_skus.append(sku)
                        continue
                    new_sku = dict(sku)
                    new_sku['id'] = None
                    new_skus.append(new_sku)
                new_item['skus'] = new_skus
            converted.append(new_item)
        return converted
    
    def test_connection(self):
        config = self._get_config()
        
        if not config['erp_api_url']:
            return {
                'success': False,
                'error': 'ERP API未配置'
            }
        
        try:
            response = requests.get(
                f"{config['erp_api_url']}/actuator/health",
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            
            if response.status_code == 200:
                return {'success': True, 'message': 'ERP连接正常'}
            else:
                return {'success': False, 'error': f'HTTP {response.status_code}'}
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def batch_update_price(self, products, adjust_ratio=1.0, access_token='', tenant_id=''):
        """批量调价

        Args:
            products: 商品列表，每个商品包含 offer_id, sell_price, adjusted_price, sku_info
            adjust_ratio: 调价比例
            access_token: 访问令牌
            tenant_id: 租户ID

        Returns:
            调价结果
        """
        config = self._get_config()

        if not config['erp_api_url']:
            return {
                'success': False,
                'error': 'ERP API未配置，请设置环境变量 ERP_API_URL'
            }

        ratio = self._to_float(adjust_ratio, default=1.0)
        spu_price_list = []
        sku_price_list = []

        for product in products:
            offer_id = str(product.get('offer_id', '')).strip()
            if not offer_id:
                continue

            sell_amount = self._safe_decimal_non_negative(
                product.get('sell_price'),
                field_name='sell_price',
                offer_id=offer_id
            )
            adjusted_price = self._to_float(product.get('adjusted_price', 0), default=0.0)
            sku_info_str = product.get('sku_info', '')
            sell_price = float(sell_amount)
            spu_base_price = sell_price if sell_price > 0 else adjusted_price

            spu_adjusted_price = calculate_adjusted_price_with_freight_fixed_nine(spu_base_price, 0, ratio)
            spu_market_price = self._calculate_market_price(sell_amount, 0)
            spu_price_list.append({
                'id': offer_id,
                'price': self._price_to_cents(spu_adjusted_price),
                'marketPrice': self._price_to_cents(spu_market_price)
            })

            if sku_info_str:
                try:
                    sku_info = json.loads(sku_info_str) if isinstance(sku_info_str, str) else sku_info_str
                    if not isinstance(sku_info, list):
                        raise ValueError(f'offer_id={offer_id} sku_info must be a JSON array')
                    for sku_index, sku in enumerate(sku_info):
                        sku_id = sku.get('sku_id') or sku.get('skuId') or sku.get('spec_id') or sku.get('specId') or ''
                        sku_id = str(sku_id).strip()
                        if not sku_id:
                            continue
                        sku_base_price = self._to_float(
                            sku.get('consign_price', sku.get('consignPrice', sku.get('price', sku.get('adjusted_price', 0)))),
                            default=0.0
                        )
                        consign_amount = self._safe_decimal_non_negative(
                            sku.get('consign_price', sku.get('consignPrice')),
                            field_name='consignPrice',
                            offer_id=offer_id,
                            sku_id=sku_id or f'index:{sku_index}'
                        )
                        sku_adjusted_price = calculate_adjusted_price_with_freight_fixed_nine(sku_base_price, 0, ratio)
                        sku_market_price = self._calculate_market_price(consign_amount, 0)
                        sku_price_list.append({
                            'channelSkuId': sku_id,
                            'price': self._price_to_cents(sku_adjusted_price),
                            'marketPrice': self._price_to_cents(sku_market_price)
                        })
                except Exception as exc:
                    logger.exception("[Batch Update Price] Parse sku_info error. offer_id=%s", offer_id)
                    return {'success': False, 'error': str(exc)}

        if not spu_price_list and not sku_price_list:
            return {
                'success': False,
                'error': '没有有效的调价数据'
            }

        request_body = {
            'spuPriceList': spu_price_list,
            'skuPriceList': sku_price_list
        }

        print(f"[Batch Update Price] Request body: {json.dumps(request_body, ensure_ascii=False, cls=DecimalEncoder)}")

        # 构建请求头
        headers = {
            'Content-Type': 'application/json'
        }
        if access_token:
            headers['accessToken'] = access_token
            headers['Authorization'] = f'Bearer {access_token}'
        if tenant_id is not None and str(tenant_id).strip() != '':
            tenant_value = str(tenant_id)
            headers['tenant-id'] = tenant_value
            headers['tenantId'] = tenant_value

        if config['erp_api_key'] and 'Authorization' not in headers:
            headers['Authorization'] = f'Bearer {config["erp_api_key"]}'

        try:
            endpoint = f"{config['erp_api_url']}/admin-api/product/sku/batch-update-price-task"
            print(f"[Batch Update Price] Endpoint: {endpoint}")

            response = self._request_with_refresh_retry(
                'PUT',
                endpoint,
                json=request_body,
                headers=headers,
                timeout=60
            )

            print(f"[Batch Update Price] Response status: {response.status_code}")
            print(f"[Batch Update Price] Response body: {response.text}")

            if response.status_code in (200, 201):
                try:
                    body = response.json() if response.text else {}
                except Exception as e:
                    print(f"[Batch Sync Price Async] JSON parse error: {e}")
                    print(f"[Batch Sync Price Async] Response content: {response.text[:1000]}")
                    return {
                        'success': False,
                        'error': f'ERP返回非JSON数据，可能是服务异常或Token过期，HTTP {response.status_code}'
                    }
                if str(body.get('code')) in ('0', '200'):
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
                        'message': body.get('msg') or body.get('message') or '调价成功',
                        'total': len(spu_price_list) + len(sku_price_list),
                        'success_count': len(spu_price_list) + len(sku_price_list),
                        'async_task': len(task_nos) > 0,
                        'task_nos': task_nos
                    }
                else:
                    return {
                        'success': False,
                        'error': self._extract_error_message(body) or '调价失败',
                        'total': len(spu_price_list) + len(sku_price_list)
                    }
            else:
                return {
                    'success': False,
                    'error': f'HTTP {response.status_code}: {response.text[:200]}',
                    'total': len(spu_price_list) + len(sku_price_list)
                }
                
        except requests.exceptions.Timeout:
            logger.exception("[Batch Update Price] Request timeout")
            return {'success': False, 'error': '请求超时'}
        except requests.exceptions.ConnectionError as e:
            logger.exception("[Batch Update Price] Connection error")
            return {'success': False, 'error': f'连接失败: {str(e)}'}
        except Exception as e:
            logger.exception("[Batch Update Price] Unexpected exception")
            return {'success': False, 'error': str(e)}

    def batch_sync_price_async(self, products, adjust_ratio=1.0, access_token='', tenant_id=''):
        """异步批量同步价格到ERP（SPU级别同步）

        Args:
            products: 商品列表，每个商品包含 offer_id, adjusted_price, sku_info
            adjust_ratio: 调价比例
            access_token: 访问令牌
            tenant_id: 租户ID

        Returns:
            异步任务结果，包含 task_no 如果需要轮询
        """
        config = self._get_config()

        if not config['erp_api_url']:
            return {
                'success': False,
                'error': 'ERP API未配置，请设置环境变量 ERP_API_URL'
            }

        ratio = self._to_float(adjust_ratio, default=1.0)
        spu_price_list = []
        sku_price_list = []

        for product in products:
            offer_id = str(product.get('offer_id', '')).strip()
            if not offer_id:
                continue

            sell_amount = self._safe_decimal_non_negative(
                product.get('sell_price'),
                field_name='sell_price',
                offer_id=offer_id
            )
            adjusted_price = self._to_float(product.get('adjusted_price', 0), default=0.0)
            sell_price = float(sell_amount)
            spu_base_price = sell_price if sell_price > 0 else adjusted_price
            spu_adjusted_price = calculate_adjusted_price_with_freight_fixed_nine(spu_base_price, 0, ratio)
            spu_market_price = self._calculate_market_price(sell_amount, 0)
            sku_info_str = product.get('sku_info', '')

            spu_price_list.append({
                'id': offer_id,
                'price': self._price_to_cents(spu_adjusted_price),
                'marketPrice': self._price_to_cents(spu_market_price)
            })

            if sku_info_str:
                try:
                    sku_info = json.loads(sku_info_str) if isinstance(sku_info_str, str) else sku_info_str
                    if not isinstance(sku_info, list):
                        raise ValueError(f'offer_id={offer_id} sku_info must be a JSON array')
                    for sku_index, sku in enumerate(sku_info):
                        sku_id = sku.get('sku_id') or sku.get('skuId') or sku.get('spec_id') or sku.get('specId') or ''
                        sku_id = str(sku_id).strip()
                        if not sku_id:
                            continue
                        sku_base_price = self._to_float(
                            sku.get('consign_price', sku.get('consignPrice', sku.get('price', sku.get('adjusted_price', 0)))),
                            default=0.0
                        )
                        consign_amount = self._safe_decimal_non_negative(
                            sku.get('consign_price', sku.get('consignPrice')),
                            field_name='consignPrice',
                            offer_id=offer_id,
                            sku_id=sku_id or f'index:{sku_index}'
                        )
                        sku_adjusted_price = calculate_adjusted_price_with_freight_fixed_nine(sku_base_price, 0, ratio)
                        sku_market_price = self._calculate_market_price(consign_amount, 0)
                        sku_price_list.append({
                            'channelSkuId': sku_id,
                            'price': self._price_to_cents(sku_adjusted_price),
                            'marketPrice': self._price_to_cents(sku_market_price)
                        })
                except Exception as e:
                    logger.exception("[Batch Sync Price Async] Parse sku_info error. offer_id=%s", offer_id)
                    return {'success': False, 'error': str(e)}

        if not spu_price_list:
            return {
                'success': False,
                'error': '没有有效的商品数据'
            }

        request_body = {
            'spuPriceList': spu_price_list,
            'skuPriceList': sku_price_list
        }

        print(f"[Batch Sync Price Async] Request body: {json.dumps(request_body, ensure_ascii=False, cls=DecimalEncoder)}")
        print(f"[Batch Sync Price Async] Request: spu_count={len(spu_price_list)}, sku_count={len(sku_price_list)}")
        if len(spu_price_list) == 0:
            print(f"[Batch Sync Price Async] WARNING: spuPriceList is empty!")

        headers = {
            'Content-Type': 'application/json'
        }
        if access_token:
            headers['accessToken'] = access_token
            headers['Authorization'] = f'Bearer {access_token}'
        if tenant_id is not None and str(tenant_id).strip() != '':
            headers['tenant-id'] = str(tenant_id)
            headers['tenantId'] = str(tenant_id)

        if config['erp_api_key'] and 'Authorization' not in headers:
            headers['Authorization'] = f'Bearer {config["erp_api_key"]}'

        try:
            endpoint = f"{config['erp_api_url']}/admin-api/product/sku/batch-update-price-task"
            print(f"[Batch Sync Price Async] Endpoint: {endpoint}")

            response = self._request_with_refresh_retry(
                'PUT',
                endpoint,
                json=request_body,
                headers=headers,
                timeout=60
            )

            print(f"[Batch Sync Price Async] Response status: {response.status_code}")
            print(f"[Batch Sync Price Async] Response body: {response.text[:500]}")

            if response.status_code in (200, 201):
                try:
                    body = response.json() if response.text else {}
                except Exception:
                    body = {}
                if str(body.get('code')) in ('0', '200'):
                    raw_data = body.get('data')
                    task_data = {}
                    if isinstance(raw_data, list) and raw_data:
                        task_data = raw_data[0] if isinstance(raw_data[0], dict) else {}
                    elif isinstance(raw_data, dict):
                        task_data = raw_data
                    task_no = task_data.get('taskNo')
                    immediate_fail_count = task_data.get('immediateFailCount', 0)
                    immediate_success_details = self._normalize_task_details(task_data.get('immediateSuccessDetails') or task_data.get('successDetails'))
                    total_count = task_data.get('totalCount', 0)
                    pending_count = task_data.get('pendingCount', 0)

                    return {
                        'success': True,
                        'async_task': True,
                        'task_no': task_no,
                        'total': total_count,
                        'pending_count': pending_count,
                        'immediate_fail_count': immediate_fail_count,
                        'immediate_success_count': len(immediate_success_details),
                        'immediate_success_details': immediate_success_details,
                        'immediate_fail_details': task_data.get('immediateFailDetails', []),
                        'raw': body
                    }
                else:
                    result = self._parse_erp_response(response)
                    if result.get('success'):
                        return {
                            'success': True,
                            'async_task': False,
                            'total': result.get('total', len(spu_price_list)),
                            'success_count': result.get('success_count', len(spu_price_list))
                        }
                    else:
                        return {
                            'success': False,
                            'error': result.get('error', '调价失败')
                        }
            else:
                return {
                    'success': False,
                    'error': f'HTTP {response.status_code}: {response.text[:200]}'
                }

        except requests.exceptions.Timeout:
            return {'success': False, 'error': '请求超时'}
        except requests.exceptions.ConnectionError as e:
            return {'success': False, 'error': f'连接失败: {str(e)}'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def batch_update_stock(self, products, stock_value=200, access_token='', tenant_id=''):
        """批量更新SKU库存
        
        Args:
            products: 商品列表，每个商品包含 offer_id, sku_info
            stock_value: 库存值，默认200
            access_token: 访问令牌
            tenant_id: 租户ID
            
        Returns:
            更新结果
        """
        config = self._get_config()
        
        if not config['erp_api_url']:
            return {
                'success': False,
                'error': 'ERP API未配置，请设置环境变量 ERP_API_URL'
            }
        
        # 构建库存更新请求体
        stock_list = []
        for product in products:
            offer_id = product.get('offer_id')
            sku_info_str = product.get('sku_info', '')
            
            # 解析 sku_info 获取 specId
            sku_info = None
            if sku_info_str:
                try:
                    if isinstance(sku_info_str, str):
                        sku_info = json.loads(sku_info_str)
                    elif isinstance(sku_info_str, list):
                        sku_info = sku_info_str
                except:
                    pass
            
            # 如果有 SKU 信息，为每个 SKU 创建库存更新记录
            if sku_info and isinstance(sku_info, list):
                for sku in sku_info:
                    sku_id = sku.get('sku_id') or sku.get('skuId') or sku.get('spec_id') or sku.get('specId') or ''
                    if sku_id:
                        stock_list.append({
                            'channelSkuId': str(sku_id),
                            'stock': stock_value
                        })
            else:
                stock_list.append({
                    'channelSkuId': '',
                    'stock': stock_value
                })
        
        if not stock_list:
            return {
                'success': False,
                'error': '没有有效的库存更新数据'
            }
        
        print(f"[Batch Update Stock] Stock list: {json.dumps(stock_list, ensure_ascii=False, cls=DecimalEncoder)}")
        
        # 构建请求头
        headers = {
            'Content-Type': 'application/json'
        }
        if access_token:
            headers['accessToken'] = access_token
            headers['Authorization'] = f'Bearer {access_token}'
        if tenant_id is not None and str(tenant_id).strip() != '':
            tenant_value = str(tenant_id)
            headers['tenant-id'] = tenant_value
            headers['tenantId'] = tenant_value
        
        if config['erp_api_key'] and 'Authorization' not in headers:
            headers['Authorization'] = f'Bearer {config["erp_api_key"]}'
        
        try:
            endpoint = f"{config['erp_api_url']}/admin-api/product/sku/batch-update-stock"
            print(f"[Batch Update Stock] Endpoint: {endpoint}")
            
            response = self._request_with_refresh_retry(
                'PUT',
                endpoint,
                json=stock_list,
                headers=headers,
                timeout=60
            )
            
            print(f"[Batch Update Stock] Response status: {response.status_code}")
            print(f"[Batch Update Stock] Response body: {response.text}")
            
            if response.status_code in (200, 201):
                result = self._parse_erp_response(response)
                if result.get('success'):
                    return {
                        'success': True,
                        'message': result.get('message') or '库存更新成功',
                        'total': len(stock_list),
                        'success_count': len(stock_list)
                    }
                else:
                    return {
                        'success': False,
                        'error': result.get('error', '库存更新失败'),
                        'total': len(stock_list)
                    }
            else:
                return {
                    'success': False,
                    'error': f'HTTP {response.status_code}: {response.text[:200]}',
                    'total': len(stock_list)
                }
                
        except requests.exceptions.Timeout:
            logger.exception("[Batch Update Stock] Request timeout")
            return {'success': False, 'error': '请求超时'}
        except requests.exceptions.ConnectionError as e:
            logger.exception("[Batch Update Stock] Connection error")
            return {'success': False, 'error': f'连接失败: {str(e)}'}
        except Exception as e:
            logger.exception("[Batch Update Stock] Unexpected exception")
            return {'success': False, 'error': str(e)}

erp_sync_service = ERPSyncService()
