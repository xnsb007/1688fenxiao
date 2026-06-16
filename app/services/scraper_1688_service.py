import requests
import json
import re
import time


class Scraper1688Service:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
        })
    
    def fetch_product_detail(self, offer_id):
        result = self._try_mtop_api(offer_id)
        if result.get('success'):
            return result
        
        result = self._try_mobile_page(offer_id)
        if result.get('success'):
            return result
        
        return {'success': False, 'error': 'Failed to fetch product detail'}
    
    def _try_mtop_api(self, offer_id):
        apis = [
            ('mtop.1688.wireless.offer.detail', '1.0'),
            ('mtop.1688.offer.detail.data.get', '1.0'),
            ('mtop.1688.offer.detail', '2.0'),
        ]
        
        for api_name, version in apis:
            try:
                result = self._call_mtop_api(offer_id, api_name, version)
                if result.get('success'):
                    return result
            except Exception as e:
                print(f"mtop api {api_name} error: {e}")
                continue
        
        return {'success': False, 'error': 'All mtop APIs failed'}
    
    def _call_mtop_api(self, offer_id, api_name, version):
        url = f"https://h5api.m.1688.com/h5/{api_name}/{version}/"
        t = str(int(time.time() * 1000))
        
        params = {
            'jsv': '2.7.2',
            'appKey': '12574478',
            't': t,
            'api': api_name,
            'v': version,
            'type': 'json',
            'dataType': 'json',
            'data': json.dumps({
                'offerId': offer_id,
                'offerIdStr': str(offer_id),
            }, ensure_ascii=False),
        }
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1',
            'Accept': 'application/json',
            'Referer': 'https://m.1688.com/',
            'Origin': 'https://m.1688.com',
        }
        
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        
        if resp.status_code != 200:
            return {'success': False, 'error': f'HTTP {resp.status_code}'}
        
        try:
            data = resp.json()
        except json.JSONDecodeError:
            return {'success': False, 'error': 'Invalid JSON response'}
        
        ret = data.get('ret', [])
        if ret and 'SUCCESS' not in str(ret):
            return {'success': False, 'error': str(ret)}
        
        offer_data = data.get('data', {})
        if not offer_data:
            return {'success': False, 'error': 'No data in response'}
        
        return self._parse_mtop_data(offer_data, offer_id)
    
    def _parse_mtop_data(self, data, offer_id):
        detail = {
            'offer_id': str(offer_id),
            'title': '',
            'price': 0,
            'image_url': '',
            'detail_desc': '',
            'supplier_id': '',
            'supplier_name': '',
            'category_id': '',
            'category_name': '',
            'images': '',
            'attributes': '',
            'sku_info': '',
            'sku_count': 0,
        }
        
        offer_info = data.get('offerInfo', data.get('data', data))
        
        if not offer_info:
            return {'success': False, 'error': 'No offer info'}
        
        detail['title'] = offer_info.get('subject', offer_info.get('title', ''))
        
        price_info = offer_info.get('price', offer_info.get('saleInfo', {}))
        if isinstance(price_info, dict):
            detail['price'] = float(price_info.get('price', price_info.get('consignPrice', 0)) or 0)
        elif price_info:
            try:
                detail['price'] = float(price_info)
            except (ValueError, TypeError):
                pass
        
        image_info = offer_info.get('image', offer_info.get('offerImage', {}))
        if isinstance(image_info, dict):
            detail['image_url'] = image_info.get('imageUrl', image_info.get('uri', ''))
        elif image_info:
            detail['image_url'] = str(image_info)
        
        if detail['image_url'] and not detail['image_url'].startswith('http'):
            detail['image_url'] = f"https://cbu01.alicdn.com/{detail['image_url']}"
        
        desc = offer_info.get('description', offer_info.get('desc', ''))
        if desc:
            detail['detail_desc'] = str(desc)[:5000]
        
        company = offer_info.get('company', offer_info.get('companyInfo', {}))
        if isinstance(company, dict):
            detail['supplier_id'] = str(company.get('companyId', company.get('id', '')))
            detail['supplier_name'] = company.get('companyName', company.get('name', ''))
        
        category = offer_info.get('category', offer_info.get('categoryInfo', {}))
        if isinstance(category, dict):
            detail['category_id'] = str(category.get('id', category.get('categoryId', '')))
            detail['category_name'] = category.get('name', category.get('categoryName', ''))
        
        images = offer_info.get('imageUrls', offer_info.get('images', []))
        if images and isinstance(images, list):
            detail['images'] = json.dumps(images[:20], ensure_ascii=False)
        
        attrs = offer_info.get('attributes', offer_info.get('attrs', []))
        if isinstance(attrs, list):
            attributes = []
            for attr in attrs:
                attributes.append({
                    'attribute_id': str(attr.get('id', attr.get('attributeId', ''))),
                    'name': attr.get('name', attr.get('attributeName', '')),
                    'value': attr.get('value', attr.get('attributeValue', ''))
                })
            if attributes:
                detail['attributes'] = json.dumps(attributes, ensure_ascii=False)
        
        skus = offer_info.get('skuInfos', offer_info.get('skus', []))
        if isinstance(skus, list):
            sku_list = []
            for sku in skus:
                attrs = []
                for attr in sku.get('attributes', sku.get('specs', [])):
                    attrs.append({
                        'attribute_id': str(attr.get('id', attr.get('attributeId', ''))),
                        'name': attr.get('name', attr.get('attributeName', '')),
                        'value': attr.get('value', attr.get('attributeValue', ''))
                    })
                sku_list.append({
                    'sku_id': str(sku.get('skuId', sku.get('id', ''))),
                    'spec_id': sku.get('specId', ''),
                    'price': float(sku.get('price', sku.get('salePrice', 0)) or 0),
                    'consign_price': float(sku.get('consignPrice', 0) or 0),
                    'stock': int(sku.get('amountOnSale', sku.get('stock', 0)) or 0),
                    'attributes': attrs
                })
            if sku_list:
                detail['sku_info'] = json.dumps(sku_list, ensure_ascii=False)
                detail['sku_count'] = len(sku_list)
        
        if detail['title']:
            return {'success': True, 'detail': detail}
        
        return {'success': False, 'error': 'No valid data found'}
    
    def _try_mobile_page(self, offer_id):
        url = f"https://m.1688.com/offer/{offer_id}.html"
        
        try:
            resp = self.session.get(url, timeout=15, allow_redirects=True)
            print(f"Mobile page status: {resp.status_code}, length: {len(resp.text)}")
            
            if resp.status_code == 200 and len(resp.text) > 5000:
                return self._parse_mobile_page(resp.text, offer_id)
        except Exception as e:
            print(f"Mobile page error for {offer_id}: {e}")
        
        return {'success': False, 'error': 'Mobile page failed'}
    
    def _parse_mobile_page(self, html_content, offer_id):
        data = self._extract_json_data(html_content)
        if data:
            return self._parse_mtop_data(data, offer_id)
        
        return {'success': False, 'error': 'No JSON data found'}
    
    def _extract_json_data(self, html_content):
        patterns = [
            r'window\.__INITIAL_DATA__\s*=\s*(\{)',
            r'var\s+pageData\s*=\s*(\{)',
            r'window\.globalData\s*=\s*(\{)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, html_content)
            if match:
                start_idx = match.start(1)
                brace_count = 0
                end_idx = start_idx
                
                for i in range(start_idx, len(html_content)):
                    if html_content[i] == '{':
                        brace_count += 1
                    elif html_content[i] == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            end_idx = i + 1
                            break
                
                if end_idx > start_idx:
                    try:
                        return json.loads(html_content[start_idx:end_idx])
                    except json.JSONDecodeError as e:
                        print(f"JSON decode error: {e}")
                        continue
        
        return None


scraper_1688_service = Scraper1688Service()
