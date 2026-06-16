import json
import logging
from urllib.parse import parse_qs

from flask import Blueprint, jsonify, request

from app.services.ali1688_message_service import (
    Ali1688MessageError,
    FENXIAO_PRICE_CHANGE,
    PRODUCT_PRODUCT_INVENTORY_CHANGE,
    PRODUCT_RELATION_VIEW_PRODUCT_EXPIRE,
    ali1688_message_service,
)

logger = logging.getLogger(__name__)

ali1688_message_bp = Blueprint('ali1688_message', __name__)


@ali1688_message_bp.route('/1688/message/callback', methods=['POST'])
@ali1688_message_bp.route('/api/1688/message/callback', methods=['POST'])
def ali1688_message_callback():
    raw_body = request.get_data(cache=True) or b''
    payload = request.get_json(silent=True)
    if payload is None:
        if request.form or request.mimetype == 'application/x-www-form-urlencoded':
            payload = request.form.to_dict(flat=True)
            if not payload and raw_body:
                parsed_form = parse_qs(raw_body.decode('utf-8', errors='ignore'), keep_blank_values=True)
                payload = {key: values[-1] if values else '' for key, values in parsed_form.items()}
        else:
            try:
                payload = json.loads(raw_body.decode('utf-8')) if raw_body else {}
            except Exception:
                return jsonify({'success': False, 'code': 'INVALID_JSON', 'error': '请求体必须是合法 JSON'}), 400

    signature_valid, signature_message = ali1688_message_service.verify_signature(
        raw_body=raw_body,
        query_args=request.args.to_dict(flat=True),
        headers=request.headers,
        payload=payload if isinstance(payload, dict) else {},
    )
    if not signature_valid:
        logger.warning('1688 callback signature invalid: %s', signature_message)
        return jsonify({'success': False, 'code': 'INVALID_SIGNATURE', 'error': signature_message}), 401

    try:
        result = ali1688_message_service.process_callback(
            payload=payload,
            raw_body=raw_body,
            signature_valid=signature_valid,
        )
        return jsonify({'success': True, 'result': result})
    except Ali1688MessageError as exc:
        status_code = 500 if exc.retryable else 400
        return jsonify({'success': False, 'code': exc.code, 'error': str(exc), 'retryable': exc.retryable}), status_code
    except Exception as exc:
        logger.exception('1688 callback unexpected error: %s', exc)
        return jsonify({'success': False, 'code': 'INTERNAL_ERROR', 'error': str(exc), 'retryable': True}), 500


@ali1688_message_bp.route('/1688/message/health', methods=['GET'])
def ali1688_message_health():
    return jsonify({
        'success': True,
        'supported_events': [
            FENXIAO_PRICE_CHANGE,
            PRODUCT_RELATION_VIEW_PRODUCT_EXPIRE,
            PRODUCT_PRODUCT_INVENTORY_CHANGE,
        ],
    })
