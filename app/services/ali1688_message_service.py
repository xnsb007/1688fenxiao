import hashlib
import hmac
import json
import logging
import re
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.config import (
    ALI1688_APP_SECRET,
    ALI1688_MESSAGE_CALLBACK_SECRET,
    ALI1688_MESSAGE_MAX_SKEW_SECONDS,
    ALI1688_MESSAGE_SIGNATURE_REQUIRED,
    SOURCE_TYPE,
)
from app.models import get_db
from app.services.ali1688_service import ali1688_service

logger = logging.getLogger(__name__)

FENXIAO_PRICE_CHANGE = 'FENXIAO_PRICE_CHANGE'
PRODUCT_RELATION_VIEW_PRODUCT_EXPIRE = 'PRODUCT_RELATION_VIEW_PRODUCT_EXPIRE'
PRODUCT_PRODUCT_INVENTORY_CHANGE = 'PRODUCT_PRODUCT_INVENTORY_CHANGE'
RELATION_VIEW_PRODUCT_EXPIRE = 'RELATION_VIEW_PRODUCT_EXPIRE'
SUPPORTED_1688_MESSAGE_TYPES = {
    FENXIAO_PRICE_CHANGE,
    PRODUCT_RELATION_VIEW_PRODUCT_EXPIRE,
    PRODUCT_PRODUCT_INVENTORY_CHANGE,
}
CHANGE_TYPE_OFF_SALE = 'off_sale'
CHANGE_TYPE_PRICE = 'price'
CHANGE_TYPE_STOCK = 'stock'
PRODUCT_CHANGE_TYPES = {CHANGE_TYPE_OFF_SALE, CHANGE_TYPE_PRICE, CHANGE_TYPE_STOCK}
PRODUCT_CHANGE_TYPE_LABELS = {
    CHANGE_TYPE_OFF_SALE: '商品下架变动',
    CHANGE_TYPE_PRICE: '商品价格变动',
    CHANGE_TYPE_STOCK: '商品库存变动',
}


class Ali1688MessageError(Exception):
    def __init__(self, message, code='INVALID_MESSAGE', retryable=False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class Ali1688MessageService:
    def verify_signature(self, raw_body, query_args=None, headers=None, payload=None):
        query_args = query_args or {}
        headers = headers or {}
        payload = payload or {}
        secret = ALI1688_MESSAGE_CALLBACK_SECRET or ALI1688_APP_SECRET
        if not ALI1688_MESSAGE_SIGNATURE_REQUIRED:
            return True, 'signature disabled'
        if not secret:
            return False, 'callback secret missing'

        signatures = []
        for key in ('sign', 'signature', '_aop_signature', 'msg_signature'):
            value = query_args.get(key) or payload.get(key)
            if value:
                signatures.append(str(value).strip())
        for key in ('X-Ali-Signature', 'X-1688-Signature', 'X-AOP-Signature', 'X-Hub-Signature-256'):
            value = headers.get(key)
            if value:
                signatures.append(str(value).strip())

        signatures = [s for s in signatures if s]
        if not signatures:
            return False, 'signature missing'

        if not self._verify_timestamp(query_args, payload):
            return False, 'timestamp expired'

        raw = raw_body if isinstance(raw_body, (bytes, bytearray)) else str(raw_body or '').encode('utf-8')
        body_text = raw.decode('utf-8', errors='ignore')
        candidates = set()
        for digestmod in (hashlib.sha1, hashlib.sha256, hashlib.md5):
            candidates.add(hmac.new(secret.encode('utf-8'), raw, digestmod).hexdigest())
            candidates.add(hmac.new(secret.encode('utf-8'), body_text.encode('utf-8'), digestmod).hexdigest())
        canonical = self._build_canonical_string(query_args, payload)
        if canonical:
            for digestmod in (hashlib.sha1, hashlib.sha256, hashlib.md5):
                candidates.add(hmac.new(secret.encode('utf-8'), canonical.encode('utf-8'), digestmod).hexdigest())
        candidates |= {item.upper() for item in list(candidates)}
        normalized_candidates = {self._normalize_signature(c) for c in candidates}
        for signature in signatures:
            normalized = self._normalize_signature(signature)
            if normalized in normalized_candidates:
                return True, 'ok'
        return False, 'signature mismatch'

    def _normalize_signature(self, value):
        value = str(value or '').strip()
        if '=' in value and value.lower().startswith(('sha1=', 'sha256=', 'md5=')):
            value = value.split('=', 1)[1]
        return value.lower()

    def _verify_timestamp(self, query_args, payload):
        if ALI1688_MESSAGE_MAX_SKEW_SECONDS <= 0:
            return True
        value = (
            query_args.get('timestamp')
            or query_args.get('_aop_timestamp')
            or payload.get('timestamp')
            or payload.get('_aop_timestamp')
        )
        if not value:
            return True
        parsed = self._parse_epoch_seconds(value)
        if not parsed:
            return True
        return abs(time.time() - parsed) <= ALI1688_MESSAGE_MAX_SKEW_SECONDS

    def _build_canonical_string(self, query_args, payload):
        pairs = {}
        for source in (query_args or {}, payload or {}):
            for key, value in source.items():
                if key in ('sign', 'signature', '_aop_signature', 'msg_signature'):
                    continue
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
                pairs[str(key)] = '' if value is None else str(value)
        if not pairs:
            return ''
        return ''.join(f'{key}{pairs[key]}' for key in sorted(pairs))

    def _parse_epoch_seconds(self, value):
        text = str(value or '').strip()
        if not text:
            return None
        if re.fullmatch(r'\d{13}', text):
            return int(text) / 1000
        if re.fullmatch(r'\d{10}', text):
            return int(text)
        return None

    def normalize_payload(self, payload):
        if not isinstance(payload, dict):
            raise Ali1688MessageError('JSON body must be object')
        data = dict(payload)
        event_type = (
            data.get('type')
            or data.get('eventType')
            or data.get('messageType')
            or data.get('topic')
            or data.get('tag')
            or data.get('bizType')
            or data.get('event')
            or ''
        )
        event_type = str(event_type or '').strip()
        if not event_type:
            if 'OfferInventoryChangeList' in data:
                event_type = PRODUCT_PRODUCT_INVENTORY_CHANGE
            elif 'productIds' in data and 'status' in data:
                event_type = PRODUCT_RELATION_VIEW_PRODUCT_EXPIRE
            elif 'productId' in data and 'memberId' in data:
                event_type = FENXIAO_PRICE_CHANGE
        data['event_type'] = event_type
        return data

    def process_callback(self, payload, raw_body='', signature_valid=False):
        data = self.normalize_payload(payload)
        event_type = data.get('event_type')
        message_id = self._build_message_id(event_type, data)
        data['_message_id'] = message_id
        product_ids = self._extract_product_ids(event_type, data)
        member_id = str(data.get('memberId') or data.get('member_id') or '').strip()
        log_row, duplicated = self.record_received(
            message_id=message_id,
            event_type=event_type,
            member_id=member_id,
            product_ids=product_ids,
            payload=data,
            raw_body=raw_body,
            signature_valid=signature_valid,
        )
        if duplicated and log_row and str(log_row.get('process_status')) == 'success':
            result = self._json_loads_safe(log_row.get('process_result')) or {}
            result['duplicated'] = True
            return result

        try:
            if event_type not in SUPPORTED_1688_MESSAGE_TYPES:
                raise Ali1688MessageError(f'Unsupported message type: {event_type}', code='UNSUPPORTED_MESSAGE')
            if event_type == FENXIAO_PRICE_CHANGE:
                result = self.handle_price_change(data)
            elif event_type == PRODUCT_RELATION_VIEW_PRODUCT_EXPIRE:
                result = self.handle_product_expire(data)
            else:
                result = self.handle_inventory_change(data)
        except Ali1688MessageError as exc:
            self.mark_processed(message_id, 'failed', {'code': exc.code}, str(exc))
            raise
        except Exception as exc:
            logger.exception('1688 message process failed message_id=%s event_type=%s', message_id, event_type)
            self.mark_processed(message_id, 'failed', {'code': 'PROCESS_ERROR'}, str(exc))
            raise Ali1688MessageError(str(exc), code='PROCESS_ERROR', retryable=True)

        self.mark_processed(message_id, 'success', result, '')
        return result

    def handle_price_change(self, payload):
        product_id = self._require_numeric_string(payload, 'productId')
        member_id = self._require_string(payload, 'memberId')
        msg_send_time = self._require_msg_time(payload)
        detail_result = ali1688_service.get_distribution_product_info(product_id)
        if not detail_result.get('success'):
            raise Ali1688MessageError(
                detail_result.get('error') or 'fetch distribution product info failed',
                code='FETCH_PRODUCT_FAILED',
                retryable=True,
            )
        detail = detail_result.get('detail') or {}
        updated = self.update_product_price_from_detail(product_id, detail, msg_send_time)
        price = detail.get('price')
        self.record_product_change_from_context(
            payload,
            change_type=CHANGE_TYPE_PRICE,
            event_type=FENXIAO_PRICE_CHANGE,
            offer_id=product_id,
            member_id=member_id,
            detail_text=f'价格变动，最新成本价 {price if price not in (None, "") else "-"}',
            detail={
                'price': str(price if price is not None else ''),
                'sku_count': detail.get('sku_count', 0),
                'updated': updated,
            },
            msg_send_time=msg_send_time,
        )
        return {
            'success': True,
            'event_type': FENXIAO_PRICE_CHANGE,
            'product_id': product_id,
            'member_id': member_id,
            'msg_send_time': msg_send_time,
            'updated': updated,
            'price': price,
            'sku_count': detail.get('sku_count', 0),
        }

    def handle_product_expire(self, payload):
        product_ids_text = self._require_string(payload, 'productIds')
        member_id = self._require_string(payload, 'memberId')
        status = self._require_string(payload, 'status')
        msg_send_time = self._require_msg_time(payload)
        product_ids = [item.strip() for item in str(product_ids_text).split(',') if item.strip()]
        if not product_ids:
            raise Ali1688MessageError('productIds is empty')
        invalid_ids = [item for item in product_ids if not re.fullmatch(r'\d+', item)]
        if invalid_ids:
            raise Ali1688MessageError(f'productIds contains invalid id: {invalid_ids[0]}')
        if status == RELATION_VIEW_PRODUCT_EXPIRE:
            affected = self.mark_products_expired(product_ids, status, msg_send_time)
            action = 'expired'
        else:
            affected = self.update_products_relation_status(product_ids, status, msg_send_time)
            action = 'status_recorded'
        for product_id in product_ids:
            self.record_product_change_from_context(
                payload,
                change_type=CHANGE_TYPE_OFF_SALE,
                event_type=PRODUCT_RELATION_VIEW_PRODUCT_EXPIRE,
                offer_id=product_id,
                member_id=member_id,
                detail_text='商品关系已下架' if status == RELATION_VIEW_PRODUCT_EXPIRE else f'商品关系状态变动：{status}',
                detail={
                    'status': status,
                    'action': action,
                    'affected': affected,
                },
                msg_send_time=msg_send_time,
            )
        return {
            'success': True,
            'event_type': PRODUCT_RELATION_VIEW_PRODUCT_EXPIRE,
            'product_ids': product_ids,
            'member_id': member_id,
            'status': status,
            'action': action,
            'affected': affected,
            'msg_send_time': msg_send_time,
        }

    def handle_inventory_change(self, payload):
        changes = payload.get('OfferInventoryChangeList')
        if isinstance(changes, str):
            try:
                changes = json.loads(changes)
            except Exception:
                changes = None
        if not isinstance(changes, list) or not changes:
            raise Ali1688MessageError('OfferInventoryChangeList must be non-empty array')
        results = []
        for index, item in enumerate(changes):
            if not isinstance(item, dict):
                raise Ali1688MessageError(f'OfferInventoryChangeList[{index}] must be object')
            offer_id = self._require_numeric_string(item, 'offerId', prefix=f'OfferInventoryChangeList[{index}].')
            quantity = self._require_int(item, 'quantity', prefix=f'OfferInventoryChangeList[{index}].')
            sku_id = str(item.get('skuId') or '').strip()
            offer_on_sale = self._to_optional_bool(item.get('offerOnSale'))
            sku_on_sale = self._to_optional_bool(item.get('skuOnSale'))
            biz_time = str(item.get('bizTime') or '').strip()
            affected = self.update_inventory(
                offer_id=offer_id,
                sku_id=sku_id,
                quantity=quantity,
                offer_on_sale=offer_on_sale,
                sku_on_sale=sku_on_sale,
                biz_time=biz_time,
            )
            self.record_product_change_from_context(
                payload,
                change_type=CHANGE_TYPE_STOCK,
                event_type=PRODUCT_PRODUCT_INVENTORY_CHANGE,
                offer_id=offer_id,
                sku_id=sku_id,
                detail_text=self._build_inventory_change_detail_text(sku_id, quantity, offer_on_sale, sku_on_sale),
                detail={
                    'quantity': quantity,
                    'offer_on_sale': offer_on_sale,
                    'sku_on_sale': sku_on_sale,
                    'biz_time': biz_time,
                    'affected': affected,
                },
                msg_send_time=biz_time or str(payload.get('msgSendTime') or '').strip(),
            )
            results.append({
                'offer_id': offer_id,
                'sku_id': sku_id,
                'quantity': quantity,
                'offer_on_sale': offer_on_sale,
                'sku_on_sale': sku_on_sale,
                'biz_time': biz_time,
                'affected': affected,
            })
        return {
            'success': True,
            'event_type': PRODUCT_PRODUCT_INVENTORY_CHANGE,
            'total': len(changes),
            'updated': sum(1 for item in results if item.get('affected')),
            'results': results,
        }

    def update_product_price_from_detail(self, offer_id, detail, msg_send_time=''):
        price = self._extract_distribution_price(detail)
        sku_info_value = detail.get('sku_info')
        sku_info = self._json_loads_safe(sku_info_value) if isinstance(sku_info_value, str) else sku_info_value
        sku_json = json.dumps(sku_info, ensure_ascii=False) if isinstance(sku_info, list) else sku_info_value
        sku_count = len(sku_info) if isinstance(sku_info, list) else int(detail.get('sku_count') or 0)
        stock = self._extract_total_stock(detail, sku_info)
        conn = get_db()
        cursor = conn.cursor()
        try:
            fields = ['price = %s', 'cost_price = %s', 'sell_price = %s', 'last_1688_msg_time = %s']
            params = [price, price, price, msg_send_time]
            if sku_json:
                fields.extend(['sku_info = %s', 'sku_count = %s'])
                params.extend([sku_json, sku_count])
            if stock is not None:
                fields.append('stock = %s')
                params.append(stock)
            params.extend([str(offer_id), SOURCE_TYPE])
            cursor.execute(
                f'''
                    UPDATE import_product
                    SET {', '.join(fields)}
                    WHERE offer_id = %s AND source_type = %s
                ''',
                params,
            )
            affected = cursor.rowcount
            conn.commit()
            return affected
        finally:
            conn.close()

    def mark_products_expired(self, product_ids, status, msg_send_time=''):
        if not product_ids:
            return 0
        placeholders = ','.join(['%s' for _ in product_ids])
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f'''
                    UPDATE import_product
                    SET status = %s,
                        sync_error = %s,
                        last_1688_msg_time = %s
                    WHERE offer_id IN ({placeholders}) AND source_type = %s
                ''',
                ['expired', f'1688商品关系已下架: {status}', msg_send_time] + product_ids + [SOURCE_TYPE],
            )
            affected = cursor.rowcount
            conn.commit()
            return affected
        finally:
            conn.close()

    def update_products_relation_status(self, product_ids, status, msg_send_time=''):
        if not product_ids:
            return 0
        placeholders = ','.join(['%s' for _ in product_ids])
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f'''
                    UPDATE import_product
                    SET status = %s,
                        last_1688_msg_time = %s
                    WHERE offer_id IN ({placeholders}) AND source_type = %s
                ''',
                [status.lower(), msg_send_time] + product_ids + [SOURCE_TYPE],
            )
            affected = cursor.rowcount
            conn.commit()
            return affected
        finally:
            conn.close()

    def update_inventory(self, offer_id, sku_id='', quantity=0, offer_on_sale=None, sku_on_sale=None, biz_time=''):
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                'SELECT sku_info FROM import_product WHERE offer_id = %s AND source_type = %s',
                (str(offer_id), SOURCE_TYPE),
            )
            row = cursor.fetchone()
            if not row:
                conn.commit()
                return 0

            sku_info = self._json_loads_safe(row.get('sku_info')) if row.get('sku_info') else None
            sku_updated = False
            total_stock = None
            if isinstance(sku_info, list) and sku_id:
                for sku in sku_info:
                    current_sku_id = str(
                        sku.get('sku_id')
                        or sku.get('skuId')
                        or sku.get('spec_id')
                        or sku.get('specId')
                        or ''
                    ).strip()
                    if current_sku_id == str(sku_id):
                        sku['stock'] = int(quantity)
                        sku['amountOnSale'] = int(quantity)
                        sku['quantity'] = int(quantity)
                        if sku_on_sale is not None:
                            sku['skuOnSale'] = bool(sku_on_sale)
                        if biz_time:
                            sku['lastInventoryBizTime'] = biz_time
                        sku_updated = True
                        break
                total_stock = sum(self._safe_int(sku.get('stock') or sku.get('amountOnSale')) for sku in sku_info)
            elif isinstance(sku_info, list):
                total_stock = sum(self._safe_int(sku.get('stock') or sku.get('amountOnSale')) for sku in sku_info)
            else:
                total_stock = int(quantity)

            status_value = None
            if offer_on_sale is not None:
                status_value = 'active' if offer_on_sale else 'off_sale'

            fields = ['stock = %s', 'last_1688_msg_time = %s']
            params = [int(total_stock if total_stock is not None else quantity), biz_time]
            if sku_updated:
                fields.append('sku_info = %s')
                params.append(json.dumps(sku_info, ensure_ascii=False))
            if status_value:
                fields.append('status = %s')
                params.append(status_value)
            params.extend([str(offer_id), SOURCE_TYPE])
            cursor.execute(
                f'''
                    UPDATE import_product
                    SET {', '.join(fields)}
                    WHERE offer_id = %s AND source_type = %s
                ''',
                params,
            )
            affected = cursor.rowcount
            conn.commit()
            return affected
        finally:
            conn.close()

    def follow_product(self, product_id, access_token=None):
        return ali1688_service.follow_product(product_id, access_token=access_token)

    def record_received(self, message_id, event_type, member_id='', product_ids=None, payload=None, raw_body='', signature_valid=False):
        product_ids = product_ids or []
        payload_json = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
        raw_text = raw_body.decode('utf-8', errors='ignore') if isinstance(raw_body, (bytes, bytearray)) else str(raw_body or '')
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute('SELECT * FROM ali1688_message_log WHERE message_id = %s', (message_id,))
            existing = cursor.fetchone()
            if existing:
                cursor.execute(
                    '''
                        UPDATE ali1688_message_log
                        SET attempt_count = attempt_count + 1,
                            last_received_at = CURRENT_TIMESTAMP,
                            signature_valid = %s
                        WHERE message_id = %s
                    ''',
                    (1 if signature_valid else 0, message_id),
                )
                conn.commit()
                return dict(existing), True
            cursor.execute(
                '''
                    INSERT INTO ali1688_message_log
                    (message_id, event_type, member_id, product_ids, payload_json, raw_body, signature_valid, process_status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'received')
                ''',
                (
                    message_id,
                    event_type,
                    member_id,
                    ','.join(product_ids),
                    payload_json,
                    raw_text,
                    1 if signature_valid else 0,
                ),
            )
            conn.commit()
            return None, False
        finally:
            conn.close()

    def mark_processed(self, message_id, status, result=None, error_message=''):
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''
                    UPDATE ali1688_message_log
                    SET process_status = %s,
                        process_result = %s,
                        error_message = %s,
                        processed_at = CURRENT_TIMESTAMP
                    WHERE message_id = %s
                ''',
                (status, json.dumps(result or {}, ensure_ascii=False), str(error_message or '')[:2000], message_id),
            )
            conn.commit()
        finally:
            conn.close()

    def record_product_change_from_context(
        self,
        payload,
        change_type,
        event_type,
        offer_id,
        sku_id='',
        member_id='',
        detail_text='',
        detail=None,
        msg_send_time='',
    ):
        message_id = str((payload or {}).get('_message_id') or '').strip()
        if not message_id:
            return 0
        member_id = member_id or str((payload or {}).get('memberId') or (payload or {}).get('member_id') or '').strip()
        return self.record_product_change(
            message_id=message_id,
            change_type=change_type,
            event_type=event_type,
            offer_id=offer_id,
            sku_id=sku_id,
            member_id=member_id,
            detail_text=detail_text,
            detail=detail,
            msg_send_time=msg_send_time,
        )

    def record_product_change(
        self,
        message_id,
        change_type,
        event_type,
        offer_id,
        sku_id='',
        member_id='',
        detail_text='',
        detail=None,
        msg_send_time='',
        source=SOURCE_TYPE,
    ):
        message_id = str(message_id or '').strip()
        change_type = str(change_type or '').strip()
        offer_id = str(offer_id or '').strip()
        if not message_id or not offer_id or change_type not in PRODUCT_CHANGE_TYPES:
            return 0

        detail_json = json.dumps(detail or {}, ensure_ascii=False, sort_keys=True, default=str)
        occurred_at = self._parse_message_time_for_db(msg_send_time) or datetime.now()
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''
                    INSERT INTO ali1688_product_change_log
                    (message_id, change_type, event_type, source_type, offer_id, sku_id, member_id,
                     detail_text, detail_json, msg_send_time, occurred_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        event_type = VALUES(event_type),
                        member_id = VALUES(member_id),
                        detail_text = VALUES(detail_text),
                        detail_json = VALUES(detail_json),
                        msg_send_time = VALUES(msg_send_time),
                        occurred_at = VALUES(occurred_at)
                ''',
                (
                    message_id,
                    change_type,
                    event_type,
                    source,
                    offer_id,
                    str(sku_id or '').strip(),
                    str(member_id or '').strip(),
                    str(detail_text or '')[:1000],
                    detail_json,
                    str(msg_send_time or '').strip(),
                    occurred_at,
                ),
            )
            affected = cursor.rowcount
            conn.commit()
            return affected
        finally:
            conn.close()

    def list_product_changes(self, page=1, page_size=20, change_type='all', keyword='', source=SOURCE_TYPE):
        page = max(1, int(page or 1))
        page_size = max(1, min(100, int(page_size or 20)))
        offset = (page - 1) * page_size
        normalized_type = str(change_type or 'all').strip()
        keyword = str(keyword or '').strip()

        where_clauses = ['c.source_type = %s']
        params = [source]
        if normalized_type in PRODUCT_CHANGE_TYPES:
            where_clauses.append('c.change_type = %s')
            params.append(normalized_type)
        if keyword:
            keyword_pattern = f'%{keyword}%'
            where_clauses.append(
                '(c.offer_id LIKE %s OR c.sku_id LIKE %s OR c.detail_text LIKE %s '
                'OR p.title LIKE %s OR p.shop_name LIKE %s OR p.supplier_name LIKE %s)'
            )
            params.extend([keyword_pattern] * 6)

        where_sql = ' AND '.join(where_clauses)
        join_sql = '''
            LEFT JOIN import_product p
                ON p.offer_id = c.offer_id AND p.source_type = c.source_type
            LEFT JOIN ali1688_message_log m
                ON m.message_id = c.message_id
        '''
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f'''
                    SELECT COUNT(*) AS total_count
                    FROM ali1688_product_change_log c
                    {join_sql}
                    WHERE {where_sql}
                ''',
                params,
            )
            total = cursor.fetchone()['total_count']
            data_params = list(params) + [page_size, offset]
            cursor.execute(
                f'''
                    SELECT
                        c.id,
                        c.message_id,
                        c.change_type,
                        c.event_type,
                        c.offer_id,
                        c.sku_id,
                        c.member_id,
                        c.detail_text,
                        c.detail_json,
                        c.msg_send_time,
                        c.occurred_at,
                        c.created_at,
                        COALESCE(p.title, '') AS title,
                        COALESCE(p.image_url, '') AS image_url,
                        COALESCE(p.shop_name, p.supplier_name, '') AS shop_name,
                        COALESCE(p.status, '') AS product_status,
                        COALESCE(m.process_status, '') AS process_status
                    FROM ali1688_product_change_log c
                    {join_sql}
                    WHERE {where_sql}
                    ORDER BY c.occurred_at DESC, c.id DESC
                    LIMIT %s OFFSET %s
                ''',
                data_params,
            )
            records = [self._decorate_product_change_row(row) for row in cursor.fetchall()]
        finally:
            conn.close()

        return {
            'success': True,
            'records': records,
            'total': total or 0,
            'page': page,
            'page_size': page_size,
            'stats': self.get_product_change_stats(source=source),
        }

    def get_product_change_stats(self, source=SOURCE_TYPE):
        stats = {
            'all': 0,
            CHANGE_TYPE_OFF_SALE: 0,
            CHANGE_TYPE_PRICE: 0,
            CHANGE_TYPE_STOCK: 0,
        }
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''
                    SELECT change_type, COUNT(*) AS count
                    FROM ali1688_product_change_log
                    WHERE source_type = %s
                    GROUP BY change_type
                ''',
                (source,),
            )
            for row in cursor.fetchall():
                change_type = row.get('change_type')
                count = int(row.get('count') or 0)
                if change_type in stats:
                    stats[change_type] = count
                stats['all'] += count
        finally:
            conn.close()
        return stats

    def _decorate_product_change_row(self, row):
        item = dict(row or {})
        item['change_type_label'] = PRODUCT_CHANGE_TYPE_LABELS.get(item.get('change_type'), '商品变动')
        item['detail'] = self._json_loads_safe(item.get('detail_json')) or {}
        item['occurred_at'] = self._format_datetime_value(item.get('occurred_at'))
        item['created_at'] = self._format_datetime_value(item.get('created_at'))
        return item

    def _build_inventory_change_detail_text(self, sku_id, quantity, offer_on_sale=None, sku_on_sale=None):
        parts = []
        if sku_id:
            parts.append(f'SKU {sku_id}')
        parts.append(f'库存变为 {quantity}')
        if offer_on_sale is False:
            parts.append('商品已下架')
        if sku_on_sale is False:
            parts.append('SKU已下架')
        return '；'.join(parts)

    def _parse_message_time_for_db(self, value):
        text = str(value or '').strip()
        if not text:
            return None
        try:
            if re.fullmatch(r'\d{13}', text):
                return datetime.fromtimestamp(int(text) / 1000)
            if re.fullmatch(r'\d{10}', text):
                return datetime.fromtimestamp(int(text))
            if re.fullmatch(r'\d{14}', text):
                return datetime.strptime(text, '%Y%m%d%H%M%S')
            if re.fullmatch(r'\d{17}', text):
                return datetime.strptime(text[:14], '%Y%m%d%H%M%S')
            if re.fullmatch(r'\d{17}[+-]\d{4}', text):
                return datetime.strptime(text[:14], '%Y%m%d%H%M%S')
            return datetime.fromisoformat(text.replace('Z', '+00:00')).replace(tzinfo=None)
        except Exception:
            return None

    def _format_datetime_value(self, value):
        if isinstance(value, datetime):
            return value.strftime('%Y-%m-%d %H:%M:%S')
        return str(value or '')

    def _build_message_id(self, event_type, payload):
        for key in ('messageId', 'msgId', 'id', 'eventId', 'notifyId'):
            value = str(payload.get(key) or '').strip()
            if value:
                raw_message_id = f'{event_type or "UNKNOWN"}:{value}'
                if len(raw_message_id) <= 128:
                    return raw_message_id
                return f'{event_type or "UNKNOWN"}:{hashlib.sha256(raw_message_id.encode("utf-8")).hexdigest()}'
        digest_payload = dict(payload)
        digest_payload.pop('sign', None)
        digest_payload.pop('signature', None)
        digest_payload.pop('_aop_signature', None)
        raw = json.dumps(digest_payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        return f'{event_type or "UNKNOWN"}:{hashlib.sha256(raw.encode("utf-8")).hexdigest()}'

    def _extract_product_ids(self, event_type, payload):
        if event_type == FENXIAO_PRICE_CHANGE:
            value = payload.get('productId')
            return [str(value).strip()] if value else []
        if event_type == PRODUCT_RELATION_VIEW_PRODUCT_EXPIRE:
            return [item.strip() for item in str(payload.get('productIds') or '').split(',') if item.strip()]
        if event_type == PRODUCT_PRODUCT_INVENTORY_CHANGE:
            changes = payload.get('OfferInventoryChangeList') or []
            ids = []
            if isinstance(changes, list):
                for item in changes:
                    if isinstance(item, dict) and item.get('offerId'):
                        ids.append(str(item.get('offerId')).strip())
            return ids
        return []

    def _require_string(self, payload, field, prefix=''):
        value = payload.get(field)
        if value is None or str(value).strip() == '':
            raise Ali1688MessageError(f'{prefix}{field} required')
        return str(value).strip()

    def _require_numeric_string(self, payload, field, prefix=''):
        value = self._require_string(payload, field, prefix=prefix)
        if not re.fullmatch(r'\d+', value):
            raise Ali1688MessageError(f'{prefix}{field} must be numeric')
        return value

    def _require_msg_time(self, payload):
        value = self._require_string(payload, 'msgSendTime')
        if not self._is_valid_time_text(value):
            raise Ali1688MessageError('msgSendTime format invalid')
        return value

    def _require_int(self, payload, field, prefix=''):
        value = payload.get(field)
        try:
            parsed = int(value)
        except Exception:
            raise Ali1688MessageError(f'{prefix}{field} must be integer')
        if parsed < 0:
            raise Ali1688MessageError(f'{prefix}{field} must be non-negative')
        return parsed

    def _is_valid_time_text(self, value):
        text = str(value or '').strip()
        if re.fullmatch(r'\d{10}|\d{13}', text):
            return True
        if re.fullmatch(r'\d{14}|\d{17}([+-]\d{4})?', text):
            return True
        try:
            datetime.fromisoformat(text.replace('Z', '+00:00'))
            return True
        except Exception:
            return False

    def _to_optional_bool(self, value):
        if value is None or value == '':
            return None
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ('true', '1', 'yes', 'y', 'on'):
            return True
        if text in ('false', '0', 'no', 'n', 'off'):
            return False
        return None

    def _extract_distribution_price(self, detail):
        candidates = []
        if isinstance(detail, dict):
            candidates.extend([detail.get('price'), detail.get('consignPrice'), detail.get('reference_price')])
            sku_info = detail.get('sku_info')
            sku_list = self._json_loads_safe(sku_info) if isinstance(sku_info, str) else sku_info
            if isinstance(sku_list, list):
                for sku in sku_list:
                    if isinstance(sku, dict):
                        candidates.extend([sku.get('consign_price'), sku.get('consignPrice'), sku.get('price')])
        for value in candidates:
            parsed = self._parse_decimal_price(value)
            if parsed is not None:
                return parsed
        return Decimal('0.00')

    def _parse_decimal_price(self, value):
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        text = text.split('~')[0].strip().replace(',', '')
        try:
            amount = Decimal(text)
        except (InvalidOperation, ValueError):
            return None
        if amount < 0:
            return Decimal('0.00')
        return amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    def _extract_total_stock(self, detail, sku_info=None):
        if isinstance(sku_info, list):
            return sum(self._safe_int(sku.get('stock') or sku.get('amountOnSale')) for sku in sku_info if isinstance(sku, dict))
        for key in ('stock', 'amount_on_sale', 'amountOnSale'):
            if key in detail:
                return self._safe_int(detail.get(key))
        return None

    def _safe_int(self, value, default=0):
        try:
            return int(float(value))
        except Exception:
            return default

    def _json_loads_safe(self, value):
        if isinstance(value, (dict, list)):
            return value
        if not value:
            return None
        try:
            return json.loads(value)
        except Exception:
            return None


ali1688_message_service = Ali1688MessageService()
