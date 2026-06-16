import sys
import os
import json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'pythonSDK'))

import aop
from aop.api.biz.JxhyProductGetPageListParam import JxhyProductGetPageListParam
from aop.api.biz.AlibabaPifatuanProductDetailListParam import AlibabaPifatuanProductDetailListParam
from aop.api.biz.AlibabaFenxiaoProductInfoGetParam import AlibabaFenxiaoProductInfoGetParam
from aop.api.biz.ProductSkuinfoGetParam import ProductSkuinfoGetParam
from aop.api.biz.ProductKeywordsSearchParam import ProductKeywordsSearchParam
from aop.api.biz.AlibabaProductFollowParam import AlibabaProductFollowParam
from app.config import ALI1688_APP_KEY, ALI1688_APP_SECRET, ALI1688_ACCESS_TOKEN, ALI1688_SERVER
from app.services.scraper_1688_service import scraper_1688_service

def normalize_image_url(url):
    """标准化图片URL，补全域名"""
    if not url:
        return ''
    
    url = str(url).strip()
    if not url:
        return ''
    
    # 已经是完整URL
    if url.startswith('http://') or url.startswith('https://'):
        return url
    
    # 协议相对URL
    if url.startswith('//'):
        return 'https:' + url
    
    # 阿里云CDN相对路径，补全域名
    if url.startswith('img/') or url.startswith('/img/'):
        return 'https://cbu01.alicdn.com/' + url.lstrip('/')
    
    # 其他相对路径
    if not url.startswith('/'):
        return 'https://cbu01.alicdn.com/' + url
    
    return 'https://cbu01.alicdn.com' + url

class Ali1688Service:
    def __init__(self):
        aop.set_default_server(ALI1688_SERVER)
        if ALI1688_APP_KEY and ALI1688_APP_SECRET:
            aop.set_default_appinfo(int(ALI1688_APP_KEY), ALI1688_APP_SECRET)

    def follow_product(self, product_id, access_token=None):
        product_id = str(product_id or '').strip()
        if not product_id:
            return {
                'success': False,
                'already_followed': False,
                'error_code': 'INVALID_PRODUCT_ID',
                'error_message': 'product_id required',
                'raw': None,
            }

        try:
            req = AlibabaProductFollowParam()
            req.access_token = access_token or ALI1688_ACCESS_TOKEN
            req.productId = product_id
            resp = req.get_response()
            return self._parse_follow_response(resp)
        except Exception as e:
            return {
                'success': False,
                'already_followed': False,
                'error_code': 'FOLLOW_REQUEST_ERROR',
                'error_message': str(e),
                'raw': None,
            }

    def _parse_follow_response(self, resp):
        if resp is None:
            return {
                'success': False,
                'already_followed': False,
                'error_code': 'EMPTY_RESPONSE',
                'error_message': 'empty follow response',
                'raw': resp,
            }

        result = resp.get('result') if isinstance(resp, dict) else None
        candidate = result if isinstance(result, dict) else (resp if isinstance(resp, dict) else {})

        # 1688 API的success判断逻辑
        success = bool(
            candidate.get('success') is True
            or candidate.get('result') is True
            or candidate.get('isSuccess') is True
        )

        # 如果result字段是布尔值True，也算成功
        if result is True:
            success = True

        message = str(
            candidate.get('message')
            or candidate.get('errorMsg')
            or candidate.get('error_message')
            or candidate.get('msg')
            or ''
        )
        code = str(
            candidate.get('code')
            or candidate.get('errorCode')
            or candidate.get('error_code')
            or ''
        )

        already_followed = any(
            keyword in message.lower()
            for keyword in ('already', 'duplicate', 'exist', 'followed')
        ) or any(keyword in message for keyword in ('已关注', '已经关注', '重复关注'))

        if success or already_followed:
            return {
                'success': True,
                'already_followed': already_followed,
                'error_code': '',
                'error_message': '',
                'raw': resp,
            }

        return {
            'success': False,
            'already_followed': False,
            'error_code': code or 'FOLLOW_FAILED',
            'error_message': message or 'follow product failed',
            'raw': resp,
        }
    
    def search_products(self, keyword=None, page_num=1, page_size=50, filters=None):
        """分销严选商品列表查询 - jxhy.product.getPageList
        
        Args:
            keyword: 关键词，可选
            page_num: 页码，从1开始
            page_size: 每页数量，最大50
            filters: 筛选条件
        """
        req = JxhyProductGetPageListParam()
        req.access_token = ALI1688_ACCESS_TOKEN
        req.pageNum = page_num
        req.pageSize = min(page_size, 50)  # API限制最大50
        
        # keyword是可选参数
        if keyword:
            req.keyword = keyword
        
        if filters:
            if filters.get('price_min'):
                req.priceStart = str(filters['price_min'])
            if filters.get('price_max'):
                req.priceEnd = str(filters['price_max'])
            if filters.get('category_id'):
                req.categoryId = filters['category_id']
            if filters.get('rule_ids'):
                req.ruleIds = filters['rule_ids']
        
        try:
            resp = req.get_response()
            print(f"Search keyword: {keyword}, page: {page_num}, resp: {resp}")
            return self._parse_keyword_response(resp)
        except Exception as e:
            print(f"Search error: {e}")
            import traceback
            traceback.print_exc()
            return {'success': False, 'error': str(e), 'products': []}
    
    def _parse_keyword_response(self, resp):
        products = []
        page_info = {}
        
        print(f"API Response: {resp}")
        
        if resp and 'result' in resp:
            result = resp['result']
            if isinstance(result, dict) and 'result' in result:
                offer_list = result['result']
                page_info = result.get('pageInfo', {})
                
                for item in offer_list:
                    offer_id = item.get('itemId', '')
                    title = item.get('title', '')
                    
                    min_price = item.get('minPrice', 0) or 0
                    max_price = item.get('maxPrice', 0) or 0
                    price = min_price / 100 if min_price else 0
                    
                    image_url = item.get('imgUrl', '')
                    
                    sales_count = item.get('salesCnt90d', 0) or 0
                    
                    service_list = item.get('serviceList', [])
                    service_tags = []
                    deliver_time = None
                    support_return = False
                    
                    for s in service_list:
                        service_name = s.get('name', '')
                        if '48小时' in service_name or '24小时' in service_name:
                            deliver_time = service_name
                            service_tags.append(service_name)
                        elif '7天包换' in service_name:
                            service_tags.append('7天包换')
                            support_return = True
                        elif '7天无理由' in service_name:
                            service_tags.append('7天无理由包退')
                            support_return = True
                        elif '材质保障' in service_name:
                            service_tags.append('材质保障')
                        elif '极速退款' in service_name:
                            service_tags.append('极速退款')
                        elif '少货必赔' in service_name:
                            service_tags.append('少货必赔')
                        elif '坏单包赔' in service_name or '破损包赔' in service_name:
                            service_tags.append('坏单包赔')
                    
                    deliver_days = 48
                    if deliver_time:
                        if '24小时' in deliver_time:
                            deliver_days = 1
                        elif '48小时' in deliver_time:
                            deliver_days = 2
                    
                    products.append({
                        'offer_id': str(offer_id),
                        'title': title,
                        'price': price,
                        'image_url': image_url,
                        'sales_count': sales_count,
                        'supplier_name': '',
                        'shop_name': '',
                        'supplier_location': '',
                        'send_location': '',
                        'deliver_days': deliver_days,
                        'deliver_time': deliver_time or f'{deliver_days}小时发货',
                        'support_return': support_return,
                        'service_tags': service_tags,
                        'detail_url': f"https://detail.1688.com/offer/{offer_id}.html"
                    })
        
        total = page_info.get('totalRecords', len(products))
        return {
            'success': True,
            'products': products,
            'total': total,
            'page_num': page_info.get('currentPage', 1),
            'page_size': page_info.get('pageSize', 20)
        }
    
    def search_for_selection(self, keyword=None, quantity=5000, filters=None):
        """批量搜索商品，支持分页获取大量数据
        
        Args:
            keyword: 关键词，可选
            quantity: 需要获取的商品数量，默认5000
            filters: 筛选条件
        """
        all_products = []
        page_size = 50  # API最大支持50
        page_num = 1
        max_pages = (quantity + page_size - 1) // page_size  # 计算需要的页数
        
        while len(all_products) < quantity and page_num <= max_pages:
            result = self.search_products(keyword, page_num=page_num, page_size=page_size, filters=filters)
            if not result['success'] or not result['products']:
                break
            
            all_products.extend(result['products'])
            page_num += 1
            
            # 如果返回的商品数量少于page_size，说明没有更多数据了
            if len(result['products']) < page_size:
                break
        
        return {
            'success': True,
            'products': all_products[:quantity],
            'total': len(all_products[:quantity]),
            'total_fetched': len(all_products)
        }
    
    def search_keywords(self, keyword, page_num=1, page_size=20, filters=None):
        """国内分销词搜 - product.keywords.search"""
        req = ProductKeywordsSearchParam()
        req.access_token = ALI1688_ACCESS_TOKEN
        
        param = {
            'keywords': keyword,
            'pageNo': page_num,
            'pageSize': page_size
        }
        
        if filters:
            if filters.get('price_min'):
                param['priceStart'] = filters['price_min']
            if filters.get('price_max'):
                param['priceEnd'] = filters['price_max']
            if filters.get('category_id'):
                param['categoryId'] = filters['category_id']
            if filters.get('sort_type'):
                param['sortType'] = filters['sort_type']
            if filters.get('sort_order'):
                param['sortOrder'] = filters['sort_order']
        
        req.param = json.dumps(param, ensure_ascii=False)
        
        print(f"[DEBUG] Keywords search param: {req.param}")
        
        try:
            resp = req.get_response()
            print(f"[DEBUG] Keywords search response: {resp}")
            return self._parse_keywords_search_response(resp)
        except Exception as e:
            print(f"Keywords search error: {e}")
            import traceback
            traceback.print_exc()
            return {'success': False, 'error': str(e), 'products': []}
    
    def _parse_keywords_search_response(self, resp):
        products = []
        page_info = {}
        
        print(f"Keywords Search API Response: {resp}")
        
        if resp and 'result' in resp:
            result = resp['result']
            if isinstance(result, dict):
                if 'success' in result and result.get('success'):
                    offer_list = result.get('result', result.get('offerList', []))
                    page_info = result.get('pageInfo', result.get('pageResult', {}))
                elif 'offerList' in result:
                    offer_list = result['offerList']
                    page_info = result.get('pageInfo', {})
                else:
                    offer_list = result.get('result', [])
                    page_info = result.get('pageInfo', {})
            elif isinstance(result, list):
                offer_list = result
            else:
                offer_list = []
            
            for item in offer_list:
                offer_id = item.get('offerId', item.get('itemId', ''))
                title = item.get('subject', item.get('title', ''))
                
                price_info = item.get('offerPrice', item.get('price', item.get('saleInfo', {})))
                if isinstance(price_info, dict):
                    price = float(price_info.get('consignPrice', price_info.get('price', 0)) or 0)
                else:
                    price = float(price_info or 0)
                
                offer_image = item.get('offerImage', {})
                if isinstance(offer_image, dict):
                    image_url = offer_image.get('imageUrl', '')
                else:
                    image_url = item.get('imageUri', item.get('picUrl', item.get('imgUrl', '')))
                
                if image_url and not image_url.startswith('http'):
                    image_url = f"https://cbu01.alicdn.com/{image_url}"
                
                sales_count = item.get('saleNum', item.get('salesCnt90d', 0)) or 0
                
                supplier_info = item.get('companyInfo', item.get('supplier', {}))
                supplier_name = supplier_info.get('companyName', supplier_info.get('name', '')) if isinstance(supplier_info, dict) else ''
                supplier_location = supplier_info.get('province', supplier_info.get('address', '')) if isinstance(supplier_info, dict) else ''
                
                service_tags = []
                support_return = False
                deliver_days = 48
                deliver_time = ''
                
                trade_services = item.get('offerTradeServiceInfo', [])
                if isinstance(trade_services, list):
                    for service in trade_services:
                        if service.get('enable'):
                            service_name = service.get('serviceName', '')
                            service_tags.append(service_name)
                            if '7天包换' in service_name or '无理由' in service_name:
                                support_return = True
                            if '48小时' in service_name:
                                deliver_days = 2
                                deliver_time = '48小时发货'
                            elif '24小时' in service_name:
                                deliver_days = 1
                                deliver_time = '24小时发货'
                
                products.append({
                    'offer_id': str(offer_id),
                    'title': title,
                    'price': price,
                    'image_url': image_url,
                    'sales_count': sales_count,
                    'supplier_name': supplier_name,
                    'shop_name': supplier_name,
                    'supplier_location': supplier_location,
                    'send_location': supplier_location,
                    'deliver_days': deliver_days,
                    'deliver_time': deliver_time,
                    'support_return': support_return,
                    'service_tags': service_tags,
                    'detail_url': f"https://detail.1688.com/offer/{offer_id}.html"
                })
        
        total = page_info.get('totalCount', page_info.get('totalRecords', len(products)))
        return {
            'success': True,
            'products': products,
            'total': total,
            'page_num': page_info.get('currentPage', page_info.get('pageNo', 1)),
            'page_size': page_info.get('pageSize', 20)
        }
    
    def search_keywords_for_selection(self, keyword, quantity=100, filters=None):
        all_products = []
        page_size = 20
        page_num = 1
        
        while len(all_products) < quantity:
            result = self.search_keywords(keyword, page_num=page_num, page_size=page_size, filters=filters)
            if not result['success'] or not result['products']:
                break
            
            all_products.extend(result['products'])
            page_num += 1
            
            if len(result['products']) < page_size:
                break
        
        return {
            'success': True,
            'products': all_products[:quantity],
            'total': len(all_products[:quantity])
        }
    
    def get_product_detail(self, offer_id):
        try:
            req = AlibabaPifatuanProductDetailListParam()
            req.access_token = ALI1688_ACCESS_TOKEN
            req.offerIds = json.dumps([int(offer_id)])
            
            resp = req.get_response()
            return self._parse_detail_response(resp, offer_id)
        except Exception as e:
            print(f"Get product detail error: {e}")
            return {'success': False, 'error': str(e)}

    def get_yx_product_detail_for_import(self, offer_id):
        try:
            req = AlibabaPifatuanProductDetailListParam()
            req.access_token = ALI1688_ACCESS_TOKEN
            req.offerIds = json.dumps([int(offer_id)])
            resp = req.get_response()
            result = resp.get('result', {}) if isinstance(resp, dict) else {}
            if isinstance(result, dict) and result.get('success') is False:
                message = result.get('message', '')
                if '不是精选货源商品' in str(message):
                    return {'success': False, 'is_df': True, 'error': message, 'raw': resp}
                return {'success': False, 'error': message or '严选详情接口失败', 'raw': resp}
            parsed = self._parse_detail_response(resp, offer_id, allow_fallback=False)
            if parsed.get('success'):
                parsed['raw'] = resp
            return parsed
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def batch_get_yx_product_details(self, offer_ids):
        """批量获取严选商品详情

        利用严选接口的批量查询能力，一次查询多个商品详情

        Args:
            offer_ids: 商品ID列表

        Returns:
            {offer_id: detail_result}
        """
        results = {}
        if not offer_ids:
            return results

        try:
            req = AlibabaPifatuanProductDetailListParam()
            req.access_token = ALI1688_ACCESS_TOKEN
            # 批量查询，最多支持20个
            req.offerIds = json.dumps([int(oid) for oid in offer_ids])
            resp = req.get_response()

            print(f"[Batch YX] Request IDs: {offer_ids}")
            print(f"[Batch YX] Response: {resp}")

            if not isinstance(resp, dict):
                # 响应异常，所有商品标记为失败
                for offer_id in offer_ids:
                    results[offer_id] = {'success': False, 'error': 'API返回格式异常'}
                return results

            result = resp.get('result', {})

            # 先尝试解析商品详情列表（即使整体success=false，也可能返回部分商品）
            product_list = []
            if isinstance(result, dict):
                # 检查是否有嵌套的result
                if 'result' in result:
                    inner_result = result['result']
                    if isinstance(inner_result, list):
                        product_list = inner_result
                    elif isinstance(inner_result, dict):
                        product_list = [inner_result] if inner_result else []
                # 如果没有嵌套result，检查result本身是否是商品数据
                elif result.get('productInfo'):
                    product_list = [result]
            elif isinstance(result, list):
                product_list = result

            print(f"[Batch YX] Parsed product_list count: {len(product_list)}")

            # 构建结果映射 - 先处理返回的商品
            found_ids = set()
            for product_data in product_list:
                if not isinstance(product_data, dict):
                    print(f"[Batch YX] Skip non-dict product_data: {product_data}")
                    continue

                product_info = product_data.get('productInfo', product_data)
                if not isinstance(product_info, dict):
                    print(f"[Batch YX] Skip non-dict product_info: {product_info}")
                    continue

                # 尝试多种可能的字段名
                offer_id = None
                for field in ['offerId', 'itemId', 'offer_id', 'item_id', 'id']:
                    offer_id = product_info.get(field)
                    if offer_id:
                        offer_id = str(offer_id)
                        break

                print(f"[Batch YX] Found offer_id: {offer_id}, product_info keys: {product_info.keys() if isinstance(product_info, dict) else 'N/A'}")

                if offer_id:
                    found_ids.add(offer_id)
                    detail = self._parse_single_product_detail(product_data, offer_id)
                    results[offer_id] = {'success': True, 'detail': detail, 'source': SOURCE_TYPE}
                    print(f"[Batch YX] Added yx result for {offer_id}")

            print(f"[Batch YX] Found yx products: {found_ids}")
            print(f"[Batch YX] Requested IDs: {set(offer_ids)}")

            # 处理未返回的商品
            for offer_id in offer_ids:
                if offer_id not in found_ids:
                    # 检查是否有明确的错误信息
                    if isinstance(result, dict) and result.get('success') is False:
                        message = result.get('message', '')
                        if '不是精选货源商品' in str(message):
                            results[offer_id] = {'success': False, 'is_df': True, 'error': message}
                        else:
                            results[offer_id] = {'success': False, 'error': message or '严选详情接口失败'}
                    else:
                        # 没有返回数据，标记为代发
                        results[offer_id] = {'success': False, 'is_df': True, 'error': '非严选商品'}

            print(f"[Batch YX] Final results count: {len(results)}, yx count: {len(found_ids)}")
            return results

        except Exception as e:
            print(f"[Batch YX] Exception: {e}")
            import traceback
            traceback.print_exc()
            # 批量查询失败，所有商品标记为失败
            for offer_id in offer_ids:
                results[offer_id] = {'success': False, 'error': str(e)}
            return results
    
    def _parse_single_product_detail(self, product_data, offer_id):
        """解析单个商品详情"""
        product_info = product_data.get('productInfo', product_data)
        
        detail = {
            'offer_id': str(offer_id),
            'title': product_info.get('subject', ''),
            'price': float(product_info.get('price', 0) or 0),
            'image_url': product_info.get('imageUri', ''),
            'detail_desc': product_info.get('description', ''),
            'supplier_id': str(product_info.get('companyId', '')),
            'supplier_name': product_data.get('wangwangAccount', '') or product_info.get('companyName', ''),
            'shop_name': product_data.get('wangwangAccount', '') or product_info.get('companyName', ''),
            'category_id': str(product_info.get('categoryID', '')),
            'category_name': product_info.get('categoryName', ''),
            'unit': product_info.get('saleInfo', {}).get('unit', '个'),
            'min_order': product_info.get('saleInfo', {}).get('minOrderQuantity', 1),
        }
        
        price_ranges = product_info.get('saleInfo', {}).get('priceRanges', [])
        if price_ranges:
            detail['price_ranges'] = json.dumps(price_ranges, ensure_ascii=False)
        
        images = product_info.get('imageUrls', [])
        if images:
            detail['images'] = json.dumps(images, ensure_ascii=False)
        
        attributes = []
        attrs_data = product_info.get('attributes', [])
        for attr in attrs_data:
            attributes.append({
'attribute_id': attr.get('attributeID', ''),
                        'name': attr.get('attributeName', ''),
                        'value': attr.get('value', '')
                    })
        if attributes:
            detail['attributes'] = json.dumps(attributes, ensure_ascii=False)
        
        sku_infos = product_info.get('skuInfos', [])
        if sku_infos:
            sku_list = []
            for sku in sku_infos:
                attrs = []
                for attr in sku.get('attributes', []):
                    attrs.append({
                        'attribute_id': attr.get('attributeID', ''),
                        'name': attr.get('attributeName', ''),
                        'value': attr.get('attributeValue', ''),
                        'sku_image_url': normalize_image_url(attr.get('skuImageUrl', ''))
                    })
                sku_list.append({
                    'cargoNumber': sku.get('cargoNumber', ''),
                    'amountOnSale': int(sku.get('amountOnSale', 0) or 0),
                    'skuId': str(sku.get('skuId', '')),
                    'specId': sku.get('specId', ''),
                    'consignPrice': float(sku.get('consignPrice', 0) or 0),
                    'attributes': attrs,
                    'multipleConsignPrice': float(sku.get('multipleConsignPrice', 0) or 0) if sku.get('multipleConsignPrice') else None,
                    'sku_id': str(sku.get('skuId', '')),
                    'spec_id': sku.get('specId', ''),
                    'price': float(sku.get('price', 0) or 0),
                    'consign_price': float(sku.get('consignPrice', 0) or 0),
                    'stock': int(sku.get('amountOnSale', 0) or 0),
                    'cargo_number': sku.get('cargoNumber', ''),
                    'multiple_consign_price': float(sku.get('multipleConsignPrice', 0) or 0) if sku.get('multipleConsignPrice') else None
                })
            detail['sku_info'] = json.dumps(sku_list, ensure_ascii=False)
            detail['sku_count'] = len(sku_list)
        
        return detail

    def get_distribution_product_info(self, offer_id):
        try:
            req = AlibabaFenxiaoProductInfoGetParam()
            req.access_token = ALI1688_ACCESS_TOKEN
            req.offerId = str(offer_id)
            resp = req.get_response()
        except Exception as e:
            return {'success': False, 'error': str(e)}
        if isinstance(resp, str):
            try:
                resp = json.loads(resp)
            except Exception:
                return {'success': False, 'error': '代发详情返回非JSON字符串', 'raw': resp}

        if not isinstance(resp, dict):
            return {'success': False, 'error': '代发详情返回格式异常', 'raw': resp}

        if resp.get('success') is False:
            error_msg = resp.get('errorMsg') or resp.get('message') or resp.get('errorMessage') or '代发详情接口失败'
            return {'success': False, 'error': error_msg, 'raw': resp}

        result = resp.get('result')
        if isinstance(result, dict) and result.get('success') is False:
            return {'success': False, 'error': result.get('message') or result.get('errorMsg') or '代发详情接口失败', 'raw': resp}

        product_info = None
        if isinstance(resp.get('productInfo'), dict):
            product_info = resp.get('productInfo')
        elif isinstance(result, dict) and isinstance(result.get('productInfo'), dict):
            product_info = result.get('productInfo')
        elif isinstance(result, dict) and isinstance(result.get('result'), dict):
            result_inner = result.get('result')
            if isinstance(result_inner.get('productInfo'), dict):
                product_info = result_inner.get('productInfo')
            else:
                product_info = result_inner
        elif isinstance(result, dict):
            product_info = result

        if not isinstance(product_info, dict):
            return {'success': False, 'error': '代发详情返回格式异常', 'raw': resp}

        # 基础信息
        detail = {
            'offer_id': str(offer_id),
            'title': product_info.get('subject', ''),
            'price': float(str(product_info.get('referencePrice', '0')).split('~')[0].strip() or 0),
            'image_url': '',
            'description': product_info.get('description', ''),
            'supplier_id': str(product_info.get('supplierUserId', '')),
            'supplier_name': product_info.get('supplierLoginId', '') or product_info.get('sellerLoginId', ''),
            'shop_name': product_info.get('supplierLoginId', '') or product_info.get('sellerLoginId', ''),
            'category_id': str(product_info.get('categoryID', '')),
            'category_name': product_info.get('categoryName', ''),
            'unit': '个',
            'min_order': 1,
            # 新增字段
            'product_id': product_info.get('productID'),
            'product_type': product_info.get('productType'),
            'status_api': product_info.get('status'),
            'language': product_info.get('language'),
            'period_of_validity': product_info.get('periodOfValidity'),
            'biz_type': product_info.get('bizType'),
            'picture_auth': product_info.get('pictureAuth', False),
            'supplier_user_id': str(product_info.get('supplierUserId', '')),
            'quality_level': product_info.get('qualityLevel'),
            'supplier_login_id': product_info.get('supplierLoginId'),
            'reference_price': product_info.get('referencePrice'),
            'seller_login_id': product_info.get('sellerLoginId'),
            'seller_id': product_info.get('sellerId'),
        }

        # 时间字段
        def parse_time(time_str):
            if not time_str:
                return None
            try:
                # 格式: 20251021005509000+0800
                if len(time_str) >= 17:
                    return time_str[:4] + '-' + time_str[4:6] + '-' + time_str[6:8] + ' ' + time_str[8:10] + ':' + time_str[10:12] + ':' + time_str[12:14]
                return None
            except:
                return None

        detail['create_time'] = parse_time(product_info.get('createTime'))
        detail['last_update_time'] = parse_time(product_info.get('lastUpdateTime'))
        detail['expire_time'] = parse_time(product_info.get('expireTime'))
        detail['modify_time'] = parse_time(product_info.get('modifyTime'))
        detail['approved_time'] = parse_time(product_info.get('approvedTime'))

        # 主图视频
        detail['main_video'] = product_info.get('mainVedio')

        # 图片
        image_data = product_info.get('productImage', {})
        image_urls = image_data.get('images', []) if isinstance(image_data, dict) else []
        if image_urls:
            detail['image_url'] = image_urls[0]
            detail['images'] = json.dumps(image_urls, ensure_ascii=False)

        # 商品属性
        attrs = []
        for attr in product_info.get('productAttribute', []) or []:
            attrs.append({
                'attribute_id': attr.get('attributeID', ''),
                'name': attr.get('attributeName', ''),
                'value': attr.get('value', ''),
                'is_custom': attr.get('isCustom', False)
            })
        if attrs:
            detail['attributes'] = json.dumps(attrs, ensure_ascii=False)
            detail['product_attributes'] = json.dumps(attrs, ensure_ascii=False)

        # SKU信息
        sku_list = []
        for sku in product_info.get('productSkuInfos', []) or []:
            sku_attrs = []
            for attr in sku.get('attributes', []) or []:
                sku_attrs.append({
                    'attribute_id': attr.get('attributeID', ''),
                    'name': attr.get('attributeName', ''),
                    'value': attr.get('attributeValue', ''),
                    'sku_image_url': normalize_image_url(attr.get('skuImageUrl'))
                })
            sku_list.append({
                'cargoNumber': sku.get('cargoNumber', ''),
                'amountOnSale': int(sku.get('amountOnSale', 0) or 0),
                'skuId': str(sku.get('skuId', '')),
                'specId': sku.get('specId', ''),
                'consignPrice': float(sku.get('consignPrice', 0) or 0),
                'attributes': sku_attrs,
                'multipleConsignPrice': float(sku.get('multipleConsignPrice', 0) or 0) if sku.get('multipleConsignPrice') else None,
                'sku_id': str(sku.get('skuId', '')),
                'spec_id': sku.get('specId', ''),
                'price': float(sku.get('consignPrice', 0) or 0),
                'consign_price': float(sku.get('consignPrice', 0) or 0),
                'stock': int(sku.get('amountOnSale', 0) or 0),
                'cargo_number': sku.get('cargoNumber', ''),
                'multiple_consign_price': float(sku.get('multipleConsignPrice', 0) or 0) if sku.get('multipleConsignPrice') else None
            })
        if sku_list:
            detail['sku_info'] = json.dumps(sku_list, ensure_ascii=False)
            detail['sku_count'] = len(sku_list)

        # 销售信息
        sale_info = product_info.get('productSaleInfo', {})
        if sale_info:
            detail['product_sale_info'] = json.dumps(sale_info, ensure_ascii=False)
            detail['unit'] = sale_info.get('unit', '个')
            detail['min_order'] = sale_info.get('minOrderQuantity', 1)
            detail['support_online_trade'] = sale_info.get('supportOnlineTrade', False)
            detail['mix_wholesale'] = sale_info.get('mixWholeSale', False)
            detail['price_auth'] = sale_info.get('priceAuth', False)
            detail['amount_on_sale'] = sale_info.get('amountOnSale', 0)
            detail['quote_type'] = sale_info.get('quoteType')
            price_ranges = sale_info.get('priceRanges', [])
            if price_ranges:
                detail['price_ranges'] = json.dumps(price_ranges, ensure_ascii=False)

        # 物流信息
        shipping_info = product_info.get('productShippingInfo', {})
        if shipping_info:
            detail['product_shipping_info'] = json.dumps(shipping_info, ensure_ascii=False)
            detail['shipping_info'] = json.dumps(shipping_info, ensure_ascii=False)
            detail['freight_template_id'] = str(shipping_info.get('freightTemplateID', ''))
            detail['send_goods_address_id'] = shipping_info.get('sendGoodsAddressId')
            detail['send_goods_address_text'] = shipping_info.get('sendGoodsAddressText')
            detail['distribution_free_postage'] = shipping_info.get('distributionFreePostage', False)

        # 扩展信息
        extend_infos = product_info.get('productExtendInfos', [])
        if extend_infos:
            detail['extend_infos'] = json.dumps(extend_infos, ensure_ascii=False)
            detail['product_extend_infos'] = json.dumps(extend_infos, ensure_ascii=False)
            # 解析buyerProtection
            for info in extend_infos:
                if info.get('key') == 'buyerProtection':
                    try:
                        protections = json.loads(info.get('value', '[]'))
                        detail['seven_days_refunds'] = 'shbp' in protections
                    except:
                        pass

        # 限售区域
        sale_limit = product_info.get('saleLimitAddress', {})
        if sale_limit:
            detail['sale_limit_address'] = json.dumps(sale_limit, ensure_ascii=False)

        # 服务能力
        service_capabilities = product_info.get('serviceCapabilities', [])
        if service_capabilities:
            detail['service_capabilities'] = json.dumps(service_capabilities, ensure_ascii=False)

        # 官方物流SKU信息
        official_logistics = product_info.get('productOfficialLogisticsModel', {})
        if official_logistics:
            detail['official_logistics_sku_info'] = json.dumps(official_logistics, ensure_ascii=False)

        # 加密物流订单支持
        encrypt_logistics = product_info.get('encryptLogisticsOrderSupportChannel', {})
        if encrypt_logistics:
            detail['encrypt_logistics_order_support'] = json.dumps(encrypt_logistics, ensure_ascii=False)

        return {'success': True, 'detail': detail, 'raw': resp}
    
    def _parse_detail_response(self, resp, offer_id, allow_fallback=True):
        if not resp or 'result' not in resp:
            if allow_fallback:
                print(f"API response invalid for {offer_id}, falling back to scraper")
                return scraper_1688_service.fetch_product_detail(offer_id)
            return {'success': False, 'error': '严选详情返回无效'}
        
        result = resp['result']
        
        if isinstance(result, dict):
            if result.get('success') is False:
                message = result.get('message', '')
                if allow_fallback:
                    print(f"API returned failure for {offer_id}: {message}, falling back to scraper")
                    return scraper_1688_service.fetch_product_detail(offer_id)
                return {'success': False, 'error': message or '严选详情接口失败'}
            
            if result.get('success') and 'result' in result:
                inner_result = result['result']
                if isinstance(inner_result, list) and len(inner_result) > 0:
                    product_data = inner_result[0]
                else:
                    product_data = inner_result
            else:
                product_data = result
        elif isinstance(result, list) and len(result) > 0:
            product_data = result[0]
        else:
            if allow_fallback:
                print(f"Invalid response format for {offer_id}, falling back to scraper")
                return scraper_1688_service.fetch_product_detail(offer_id)
            return {'success': False, 'error': '严选详情返回格式异常'}
        
        product_info = product_data.get('productInfo', product_data)
        
        detail = {
            'offer_id': str(offer_id),
            'title': product_info.get('subject', ''),
            'price': float(product_info.get('price', 0) or 0),
            'image_url': product_info.get('imageUri', ''),
            'detail_desc': product_info.get('description', ''),
            'supplier_id': str(product_info.get('companyId', '')),
            'supplier_name': product_data.get('wangwangAccount', '') or product_info.get('companyName', ''),
            'shop_name': product_data.get('wangwangAccount', '') or product_info.get('companyName', ''),
            'category_id': str(product_info.get('categoryID', '')),
            'category_name': product_info.get('categoryName', ''),
            'unit': product_info.get('saleInfo', {}).get('unit', '个'),
            'min_order': product_info.get('saleInfo', {}).get('minOrderQuantity', 1),
        }
        
        price_ranges = product_info.get('saleInfo', {}).get('priceRanges', [])
        if price_ranges:
            detail['price_ranges'] = json.dumps(price_ranges, ensure_ascii=False)
        
        images = product_info.get('imageUrls', [])
        if images:
            detail['images'] = json.dumps(images, ensure_ascii=False)
        
        attributes = []
        attrs_data = product_info.get('attributes', [])
        for attr in attrs_data:
            attributes.append({
                'attribute_id': attr.get('attributeID', ''),
                'name': attr.get('attributeName', ''),
                'value': attr.get('value', '')
            })
        if attributes:
            detail['attributes'] = json.dumps(attributes, ensure_ascii=False)
        
        sku_infos = product_info.get('skuInfos', [])
        if sku_infos:
            sku_list = []
            for sku in sku_infos:
                attrs = []
                for attr in sku.get('attributes', []):
                    attrs.append({
                        'attribute_id': attr.get('attributeID', ''),
                        'name': attr.get('attributeName', ''),
                        'value': attr.get('attributeValue', ''),
                        'sku_image_url': normalize_image_url(attr.get('skuImageUrl', ''))
                    })
                sku_list.append({
                    'cargoNumber': sku.get('cargoNumber', ''),
                    'amountOnSale': int(sku.get('amountOnSale', 0) or 0),
                    'skuId': str(sku.get('skuId', '')),
                    'specId': sku.get('specId', ''),
                    'consignPrice': float(sku.get('consignPrice', 0) or 0),
                    'attributes': attrs,
                    'multipleConsignPrice': float(sku.get('multipleConsignPrice', 0) or 0) if sku.get('multipleConsignPrice') else None,
                    'sku_id': str(sku.get('skuId', '')),
                    'spec_id': sku.get('specId', ''),
                    'price': float(sku.get('price', 0) or 0),
                    'consign_price': float(sku.get('consignPrice', 0) or 0),
                    'stock': int(sku.get('amountOnSale', 0) or 0),
                    'cargo_number': sku.get('cargoNumber', ''),
                    'multiple_consign_price': float(sku.get('multipleConsignPrice', 0) or 0) if sku.get('multipleConsignPrice') else None
                })
            detail['sku_info'] = json.dumps(sku_list, ensure_ascii=False)
            detail['sku_count'] = len(sku_list)
        else:
            sku_info = self.get_sku_info(offer_id)
            if sku_info.get('success'):
                detail['sku_info'] = sku_info.get('sku_info', '')
                detail['sku_count'] = sku_info.get('sku_count', 0)
        
        shipping_info = product_info.get('shippingInfo', {})
        if shipping_info:
            detail['product_shipping_info'] = json.dumps(shipping_info, ensure_ascii=False)
            detail['shipping_info'] = json.dumps(shipping_info, ensure_ascii=False)
            detail['freight_template_id'] = str(shipping_info.get('freightTemplateID', ''))
            detail['send_address'] = shipping_info.get('sendGoodsAddressText', '')
        
        return {'success': True, 'detail': detail}
    
    def get_sku_info(self, offer_id):
        try:
            req = ProductSkuinfoGetParam()
            req.offerId = str(offer_id)
            
            resp = req.get_response()
            return self._parse_sku_response(resp)
        except Exception as e:
            print(f"Get SKU info error: {e}")
            return {'success': False, 'error': str(e), 'sku_info': '', 'sku_count': 0}
    
    def _parse_sku_response(self, resp):
        if not resp:
            return {'success': False, 'sku_info': '', 'sku_count': 0}
        
        sku_list = []
        if 'result' in resp:
            result = resp['result']
            if isinstance(result, list):
                sku_list = result
            elif isinstance(result, dict) and 'skuInfoList' in result:
                sku_list = result['skuInfoList']
        
        sku_info = []
        for sku in sku_list:
            attrs = []
            for attr in sku.get('attributes', []):
                attrs.append({
                    'attribute_id': attr.get('attributeID', ''),
                    'name': attr.get('attributeName', ''),
                    'value': attr.get('attributeValue', ''),
                    'sku_image_url': normalize_image_url(attr.get('skuImageUrl', ''))
                })
            sku_info.append({
                'cargoNumber': sku.get('cargoNumber', ''),
                'amountOnSale': int(sku.get('amount', 0) or sku.get('amountOnSale', 0) or 0),
                'skuId': str(sku.get('skuId', '')),
                'specId': sku.get('specId', ''),
                'consignPrice': float(sku.get('price', 0) or sku.get('consignPrice', 0) or 0),
                'attributes': attrs,
                'multipleConsignPrice': float(sku.get('multipleConsignPrice', 0) or 0) if sku.get('multipleConsignPrice') else None,
                'sku_id': str(sku.get('skuId', '')),
                'spec_id': sku.get('specId', ''),
                'price': float(sku.get('price', 0) or 0),
                'consign_price': float(sku.get('price', 0) or sku.get('consignPrice', 0) or 0),
                'stock': int(sku.get('amount', 0) or sku.get('amountOnSale', 0) or 0),
                'cargo_number': sku.get('cargoNumber', ''),
                'multiple_consign_price': float(sku.get('multipleConsignPrice', 0) or 0) if sku.get('multipleConsignPrice') else None,
                'spec_attrs': sku.get('specAttrs', [])
            })
        
        return {
            'success': True,
            'sku_info': json.dumps(sku_info, ensure_ascii=False) if sku_info else '',
            'sku_count': len(sku_info)
        }
    
    def get_product_details_batch(self, offer_ids):
        results = {}
        for offer_id in offer_ids:
            result = self.get_product_detail(offer_id)
            results[offer_id] = result
        return results

ali1688_service = Ali1688Service()
