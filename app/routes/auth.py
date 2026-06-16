from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from functools import wraps
import requests
import os
import json
import base64
import random
import string
import time
from urllib.parse import urlparse
from datetime import datetime
from cryptography.hazmat.primitives import padding as crypto_padding
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

DEFAULT_AES_REQUEST_KEY = '52549111389893486934626385991395'
DEFAULT_AES_RESPONSE_KEY = '96103715984234343991809655248883'

auth_bp = Blueprint('auth', __name__)

def _log_task(request_method, request_path, request_params, response_data, success, error_message='', execution_time=0, username=''):
    """记录任务执行日志"""
    try:
        from app.services.task_log_service import task_log_service
        ip_address = request.remote_addr or ''
        user_agent = request.headers.get('User-Agent', '')[:500]
        
        task_log_service.log(
            username=username or session.get('username', 'unknown'),
            request_method=request_method,
            request_path=request_path,
            request_params=request_params,
            response_data=response_data,
            success=success,
            error_message=error_message,
            ip_address=ip_address,
            user_agent=user_agent,
            execution_time=execution_time
        )
    except Exception as e:
        print(f"[Task Log] Failed to log: {e}")

def _get_env(names, default=''):
    for name in names:
        value = os.environ.get(name)
        if value is not None and str(value).strip() != '':
            return value
    return default

def _to_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')

def _build_login_urls(base_url):
    base = (base_url or '').strip().rstrip('/')
    if not base:
        base = 'https://dev.1bgo.com'

    urls = []
    parsed = urlparse(base)
    path = (parsed.path or '').rstrip('/')

    if path.endswith('/admin-api'):
        urls.append(base + '/system/auth/login')
    else:
        urls.append(base + '/admin-api/system/auth/login')

    if path.endswith('/backend'):
        root = base[: -len('/backend')]
        urls.append(root + '/admin-api/system/auth/login')
    elif '/backend/' in path:
        idx = base.find('/backend/')
        if idx > 0:
            root = base[:idx]
            urls.append(root + '/admin-api/system/auth/login')

    unique_urls = []
    seen = set()
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)
    return unique_urls

def _random_aes_key():
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(32))

def _aes_encrypt(text, key):
    key_bytes = key.encode('utf-8')
    padder = crypto_padding.PKCS7(128).padder()
    padded = padder.update(text.encode('utf-8')) + padder.finalize()
    cipher = Cipher(algorithms.AES(key_bytes), modes.ECB())
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(encrypted).decode('utf-8')

def _aes_decrypt(text, key):
    key_bytes = key.encode('utf-8')
    cipher = Cipher(algorithms.AES(key_bytes), modes.ECB())
    decryptor = cipher.decryptor()
    decrypted = decryptor.update(base64.b64decode(text)) + decryptor.finalize()
    unpadder = crypto_padding.PKCS7(128).unpadder()
    data = unpadder.update(decrypted) + unpadder.finalize()
    return data.decode('utf-8')

def _to_pem_public_key(key):
    normalized = key.strip().replace('\r', '').replace('\n', '')
    if 'BEGIN PUBLIC KEY' in key:
        return key.encode('utf-8')
    padding_len = (4 - len(normalized) % 4) % 4
    if padding_len:
        normalized = normalized + ('=' * padding_len)
    lines = [normalized[i:i + 64] for i in range(0, len(normalized), 64)]
    return ('-----BEGIN PUBLIC KEY-----\n' + '\n'.join(lines) + '\n-----END PUBLIC KEY-----\n').encode('utf-8')

def _to_pem_private_key(key):
    normalized = key.strip().replace('\r', '').replace('\n', '')
    if 'BEGIN PRIVATE KEY' in key:
        return key.encode('utf-8')
    padding_len = (4 - len(normalized) % 4) % 4
    if padding_len:
        normalized = normalized + ('=' * padding_len)
    lines = [normalized[i:i + 64] for i in range(0, len(normalized), 64)]
    return ('-----BEGIN PRIVATE KEY-----\n' + '\n'.join(lines) + '\n-----END PRIVATE KEY-----\n').encode('utf-8')

def _rsa_encrypt(text, public_key):
    pem = _to_pem_public_key(public_key)
    key_obj = serialization.load_pem_public_key(pem)
    encrypted = key_obj.encrypt(text.encode('utf-8'), asym_padding.PKCS1v15())
    return base64.b64encode(encrypted).decode('utf-8')

def _rsa_decrypt(text, private_key):
    pem = _to_pem_private_key(private_key)
    key_obj = serialization.load_pem_private_key(pem, password=None)
    decrypted = key_obj.decrypt(base64.b64decode(text), asym_padding.PKCS1v15())
    return decrypted.decode('utf-8')

def _normalize_cipher_text(text):
    if text is None:
        return ''
    value = str(text).strip()
    if not value:
        return ''
    if value.startswith('"') and value.endswith('"'):
        try:
            return json.loads(value)
        except Exception:
            return value[1:-1]
    return value

def _crypto_debug_enabled():
    if _to_bool(_get_env(['LOGIN_CRYPTO_DEBUG', 'FLASK_DEBUG'], 'false'), False):
        return True
    env = (_get_env(['APP_ENV', 'FLASK_ENV'], '') or '').strip().lower()
    return env in ('dev', 'development', 'local')

def _crypto_debug_log(event, payload):
    if not _crypto_debug_enabled():
        return
    try:
        print(f"[LOGIN-CRYPTO-DEBUG] {event}: {json.dumps(payload, ensure_ascii=False)}")
    except Exception:
        print(f"[LOGIN-CRYPTO-DEBUG] {event}: {payload}")

def _build_login_request_variants(payload):
    variants = []
    enable_encrypt = _to_bool(_get_env(['VITE_APP_API_ENCRYPT_ENABLE', 'LOGIN_API_ENCRYPT_ENABLE'], 'true'), True)
    encrypt_header = _get_env(['VITE_APP_API_ENCRYPT_HEADER', 'LOGIN_API_ENCRYPT_HEADER'], 'X-Api-Encrypt')
    aes_key_header = _get_env(['VITE_APP_AES_ENCRYPT_KEY_HEADER', 'LOGIN_AES_ENCRYPT_KEY_HEADER'], 'X-Encrypt-Key')
    algorithm = (_get_env(['VITE_APP_API_ENCRYPT_ALGORITHM', 'LOGIN_API_ENCRYPT_ALGORITHM'], 'RSA') or '').strip().upper()

    if enable_encrypt:
        raw_data = json.dumps(payload, ensure_ascii=False)
        if algorithm == 'AES':
            aes_key = _get_env(['VITE_APP_API_ENCRYPT_REQUEST_KEY', 'LOGIN_API_ENCRYPT_REQUEST_KEY'], DEFAULT_AES_REQUEST_KEY)
            encrypted = _aes_encrypt(raw_data, aes_key)
            variants.append({
                'name': 'AES',
                'request_kwargs': {
                    'data': json.dumps(encrypted),
                    'headers': {'Content-Type': 'application/json', encrypt_header: 'true', 'isEncrypt': 'true'}
                }
            })
        elif algorithm == 'RSA':
            public_key = _get_env(['VITE_APP_API_ENCRYPT_REQUEST_KEY', 'LOGIN_API_ENCRYPT_REQUEST_PUBLIC_KEY'], '').strip()
            if not public_key:
                raise ValueError('未配置登录请求RSA公钥')
            aes_key = _random_aes_key()
            encrypted_data = _aes_encrypt(raw_data, aes_key)
            encrypted_key = _rsa_encrypt(aes_key, public_key)
            variants.append({
                'name': 'RSA',
                'request_kwargs': {
                    'data': json.dumps(encrypted_data),
                    'headers': {
                        'Content-Type': 'application/json',
                        encrypt_header: 'true',
                        aes_key_header: encrypted_key,
                        'isEncrypt': 'true'
                    }
                }
            })
        else:
            raise ValueError(f'不支持的登录加密算法: {algorithm}')
    else:
        variants.append({
            'name': 'PLAIN',
            'request_kwargs': {
                'json': payload,
                'headers': {'Content-Type': 'application/json'}
            }
        })
    return variants

def _parse_login_response(response, variant_name):
    text = response.text or ''
    encrypt_header = _get_env(['VITE_APP_API_ENCRYPT_HEADER', 'LOGIN_API_ENCRYPT_HEADER'], 'X-Api-Encrypt')
    aes_key_header = _get_env(['VITE_APP_AES_ENCRYPT_KEY_HEADER', 'LOGIN_AES_ENCRYPT_KEY_HEADER'], 'X-Encrypt-Key')
    is_encrypt_response = (response.headers.get(encrypt_header) or response.headers.get(encrypt_header.lower())) == 'true'

    if is_encrypt_response and text:
        cipher_body = _normalize_cipher_text(text)
        if variant_name == 'AES':
            response_key = _get_env(['VITE_APP_API_ENCRYPT_RESPONSE_KEY', 'LOGIN_API_ENCRYPT_RESPONSE_KEY'], DEFAULT_AES_RESPONSE_KEY)
            text = _aes_decrypt(cipher_body, response_key)
        elif variant_name == 'RSA':
            private_key = _get_env(['VITE_APP_API_ENCRYPT_RESPONSE_KEY', 'LOGIN_API_ENCRYPT_RESPONSE_PRIVATE_KEY', 'LOGIN_API_ENCRYPT_RESPONSE_KEY'], '').strip()
            encrypted_aes_key = response.headers.get(aes_key_header) or response.headers.get(aes_key_header.lower())
            if not private_key:
                raise ValueError('未配置VITE_APP_API_ENCRYPT_RESPONSE_KEY，无法解密登录响应')
            if not encrypted_aes_key:
                raise ValueError('响应缺少X-Encrypt-Key，无法解密登录响应')
            aes_key = _rsa_decrypt(_normalize_cipher_text(encrypted_aes_key), private_key)
            _crypto_debug_log('decrypt-header', {
                'mode': variant_name,
                'has_x_encrypt_key': True,
                'response_cipher_length': len(cipher_body)
            })
            text = _aes_decrypt(cipher_body, aes_key)

    try:
        result = json.loads(text)
        _crypto_debug_log('decrypt-result', {
            'mode': variant_name,
            'result_keys': list(result.keys()) if isinstance(result, dict) else [],
            'is_success_field': bool(isinstance(result, dict) and ('success' in result or 'code' in result))
        })
        return result
    except Exception:
        _crypto_debug_log('decrypt-result-raw', {
            'mode': variant_name,
            'raw_length': len(str(text))
        })
        return {'success': False, 'msg': text}

def _decode_login_request_data():
    encrypt_header = _get_env(['VITE_APP_API_ENCRYPT_HEADER', 'LOGIN_API_ENCRYPT_HEADER'], 'X-Api-Encrypt')
    is_encrypted = (request.headers.get(encrypt_header) or request.headers.get(encrypt_header.lower()) or '').lower() == 'true'
    if not is_encrypted:
        return request.get_json(silent=True) or {}

    algorithm = (_get_env(['VITE_APP_API_ENCRYPT_ALGORITHM', 'LOGIN_API_ENCRYPT_ALGORITHM'], 'RSA') or '').strip().upper()
    body = request.get_data(as_text=True) or ''
    if not body:
        return {}

    if algorithm == 'AES':
        request_key = _get_env(['VITE_APP_API_ENCRYPT_REQUEST_KEY', 'LOGIN_API_ENCRYPT_REQUEST_KEY'], DEFAULT_AES_REQUEST_KEY)
        decrypted = _aes_decrypt(body, request_key)
        return json.loads(decrypted)

    if algorithm == 'RSA':
        aes_key_header = _get_env(['VITE_APP_AES_ENCRYPT_KEY_HEADER', 'LOGIN_AES_ENCRYPT_KEY_HEADER'], 'X-Encrypt-Key')
        encrypted_aes_key = request.headers.get(aes_key_header) or request.headers.get(aes_key_header.lower()) or ''
        if not encrypted_aes_key:
            raise ValueError('缺少加密请求头 X-Encrypt-Key')
        private_key = _get_env(['VITE_APP_API_ENCRYPT_RESPONSE_KEY', 'LOGIN_API_ENCRYPT_RESPONSE_PRIVATE_KEY', 'LOGIN_API_ENCRYPT_RESPONSE_KEY'], '').strip()
        if not private_key:
            raise ValueError('未配置登录请求RSA私钥')
        aes_key = _rsa_decrypt(encrypted_aes_key, private_key)
        decrypted = _aes_decrypt(body, aes_key)
        return json.loads(decrypted)

    raise ValueError(f'不支持的登录加密算法: {algorithm}')

def _extract_token_payload(result):
    if not isinstance(result, dict):
        return {}
    data = result.get('data', {})
    if isinstance(data, dict) and isinstance(data.get('data'), dict):
        return data.get('data', {})
    if isinstance(data, dict):
        return data
    return {}

def _normalize_expire_time(value):
    if value is None or value == '':
        return 0
    if isinstance(value, (int, float)):
        ts = int(value)
        return ts * 1000 if ts < 10**12 else ts
    text = str(value).strip()
    if not text:
        return 0
    if text.isdigit():
        ts = int(text)
        return ts * 1000 if ts < 10**12 else ts
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f'):
        try:
            dt = datetime.strptime(text.replace('Z', ''), fmt)
            return int(dt.timestamp() * 1000)
        except Exception:
            continue
    return 0

def _read_refresh_token():
    token = session.get('refresh_token', '') or session.get('refreshToken', '')
    return str(token).strip()

def _read_access_token():
    token = session.get('token', '') or session.get('access_token', '')
    return str(token).strip()

def _extract_expire_time_from_payload(token_data):
    if not isinstance(token_data, dict):
        return 0
    expire_time = _normalize_expire_time(
        token_data.get('expiresTime')
        or token_data.get('expires_time')
        or token_data.get('expireTime')
        or token_data.get('tokenExpireTime')
    )
    if expire_time:
        return expire_time
    expires_in = token_data.get('expiresIn')
    if expires_in is None:
        expires_in = token_data.get('expires_in')
    if expires_in is None or expires_in == '':
        return 0
    try:
        expires_in_val = int(float(expires_in))
    except Exception:
        return 0
    if expires_in_val <= 0:
        return 0
    return int(time.time() * 1000) + expires_in_val * 1000

def _save_token_session(token_data):
    if not isinstance(token_data, dict):
        return False
    access_token = str(token_data.get('accessToken') or token_data.get('access_token') or '').strip()
    refresh_token = str(token_data.get('refreshToken') or token_data.get('refresh_token') or '').strip()
    expire_time = _extract_expire_time_from_payload(token_data)
    tenant_id = str(token_data.get('tenantId') or token_data.get('tenant_id') or session.get('tenant_id', '')).strip()

    if access_token:
        session['token'] = access_token
        session['access_token'] = access_token
    if refresh_token:
        session['refresh_token'] = refresh_token
        session['refreshToken'] = refresh_token
    if expire_time:
        session['token_expire_time'] = expire_time
        session['expiresTime'] = expire_time
    if tenant_id:
        session['tenant_id'] = tenant_id
    return bool(access_token)

def _refresh_request_variants(refresh_token):
    payload_json = [
        {'refreshToken': refresh_token},
        {'refresh_token': refresh_token},
        {'token': refresh_token}
    ]
    return [
        {'json': payload_json[0], 'headers': {'Content-Type': 'application/json'}},
        {'json': payload_json[1], 'headers': {'Content-Type': 'application/json'}},
        {'json': payload_json[2], 'headers': {'Content-Type': 'application/json'}},
        {'data': {'refreshToken': refresh_token}},
        {'params': {'refreshToken': refresh_token}},
    ]

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('auth.login_page'))
        return f(*args, **kwargs)
    return decorated_function

@auth_bp.route('/login')
def login_page():
    if session.get('logged_in'):
        return redirect(url_for('index'))
    return render_template('login.html')

@auth_bp.route('/api/login', methods=['POST'])
def login():
    start_time = time.time()
    try:
        encrypt_header = _get_env(['VITE_APP_API_ENCRYPT_HEADER', 'LOGIN_API_ENCRYPT_HEADER'], 'X-Api-Encrypt')
        aes_key_header = _get_env(['VITE_APP_AES_ENCRYPT_KEY_HEADER', 'LOGIN_AES_ENCRYPT_KEY_HEADER'], 'X-Encrypt-Key')
        is_encrypted_request = (request.headers.get(encrypt_header) or request.headers.get(encrypt_header.lower()) or '').lower() == 'true'

        username = ''
        erp_api_url = os.environ.get('ERP_API_URL', 'https://dev.1bgo.com')
        login_urls = _build_login_urls(erp_api_url)

        if is_encrypted_request:
            body = request.get_data(as_text=True) or ''
            if not body:
                return jsonify({'success': False, 'error': '登录请求体为空'})
            headers = {
                'Content-Type': 'application/json',
                encrypt_header: 'true',
                'isEncrypt': request.headers.get('isEncrypt', 'true')
            }
            encrypted_aes_key = request.headers.get(aes_key_header) or request.headers.get(aes_key_header.lower())
            if encrypted_aes_key:
                headers[aes_key_header] = encrypted_aes_key
            request_variants = [{
                'name': 'PASS_THROUGH',
                'request_kwargs': {
                    'data': body,
                    'headers': headers
                }
            }]
        else:
            data = request.get_json(silent=True) or {}
            username = data.get('username', '').strip()
            password = data.get('password', '')
            captcha_verification = data.get('captchaVerification', '') or data.get('captchaToken', '')

            if not username:
                return jsonify({'success': False, 'error': '请输入用户名'})
            if not password:
                return jsonify({'success': False, 'error': '请输入密码'})
            if not captcha_verification:
                return jsonify({'success': False, 'error': '请完成滑动验证'})

            payload = {
                'username': username,
                'password': password,
                'captchaVerification': captcha_verification
            }
            request_variants = _build_login_request_variants(payload)

        last_http_status = None
        last_error = ''
        for login_url in login_urls:
            for variant in request_variants:
                response = requests.post(
                    login_url,
                    timeout=30,
                    **variant['request_kwargs']
                )
                last_http_status = response.status_code
                print(f"[Login API] URL: {login_url} mode={variant['name']} status={response.status_code}")
                print(f"[Login API] Response body length: {len(response.text or '')}")
                print(f"[Login API] Response body full: {response.text}")

                if response.status_code in (404, 405):
                    last_error = f'登录失败: HTTP {response.status_code}'
                    continue

                if response.status_code == 200:
                    if variant['name'] == 'PASS_THROUGH':
                        is_encrypt_response = (response.headers.get(encrypt_header) or response.headers.get(encrypt_header.lower()) or '').lower() == 'true'
                        if is_encrypt_response:
                            algorithm = (_get_env(['VITE_APP_API_ENCRYPT_ALGORITHM', 'LOGIN_API_ENCRYPT_ALGORITHM'], 'RSA') or '').strip().upper()
                            response_mode = 'AES' if algorithm == 'AES' else 'RSA'
                            result = _parse_login_response(response, response_mode)
                        else:
                            result = response.json()
                    else:
                        result = _parse_login_response(response, variant['name'])
                    if result.get('code') == 0 or result.get('success'):
                        data = _extract_token_payload(result)
                        session['logged_in'] = True
                        session['username'] = username or data.get('username', '')
                        _save_token_session(data)
                        session['login_time'] = int(time.time())
                        execution_time = int((time.time() - start_time) * 1000)
                        _log_task('POST', '/api/login', {'username': username}, {'success': True}, True, '', execution_time, username)
                        return jsonify({
                            'success': True,
                            'redirect': '/products',
                            'message': '登录成功'
                        })
                    execution_time = int((time.time() - start_time) * 1000)
                    error_msg = result.get('msg', result.get('message', '登录失败'))
                    _log_task('POST', '/api/login', {'username': username}, {'success': False}, False, error_msg, execution_time, username)
                    return jsonify({
                        'success': False,
                        'error': error_msg
                    })

                return jsonify({
                    'success': False,
                    'error': f'登录失败: HTTP {response.status_code}'
                })

        if last_error:
            return jsonify({'success': False, 'error': last_error})
        if last_http_status:
            return jsonify({'success': False, 'error': f'登录失败: HTTP {last_http_status}'})
        return jsonify({'success': False, 'error': '登录失败: 未获取到可用登录接口'})
    except ValueError as e:
        return jsonify({'success': False, 'error': f'登录请求解密失败: {str(e)}'})
    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'error': '请求超时，请重试'})
    except requests.exceptions.ConnectionError:
        return jsonify({'success': False, 'error': '连接服务器失败'})
    except Exception as e:
        print(f"Login error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@auth_bp.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True, 'redirect': '/login'})

@auth_bp.route('/api/check-auth')
def check_auth():
    return jsonify({
        'logged_in': session.get('logged_in', False),
        'username': session.get('username', '')
    })

@auth_bp.route('/api/auth/refresh', methods=['POST'])
def refresh_token_api():
    refresh_token = _read_refresh_token()
    if not refresh_token:
        return jsonify({'success': False, 'code': 'UNAUTHORIZED', 'error': '缺少refreshToken'}), 401

    result = refresh_access_token(with_detail=True)
    if result.get('success'):
        session['logged_in'] = True
        return jsonify({
            'success': True,
            'data': {
                'accessToken': _read_access_token(),
                'refreshToken': _read_refresh_token(),
                'expiresTime': _normalize_expire_time(session.get('token_expire_time', 0)),
                'tenantId': session.get('tenant_id', '')
            }
        })

    if result.get('retryable'):
        return jsonify({'success': False, 'code': 'TOKEN_REFRESH_RETRYABLE', 'error': result.get('error', '刷新请求暂时失败，请重试')}), 503
    return jsonify({'success': False, 'code': 'UNAUTHORIZED', 'error': result.get('error', '刷新登录状态失败')}), 401

def is_token_expired():
    expire_time = _normalize_expire_time(session.get('token_expire_time', 0))
    if expire_time:
        session['token_expire_time'] = expire_time
    access_token = _read_access_token()
    if not access_token:
        return True
    if not expire_time:
        return False
    return int(time.time() * 1000) >= (expire_time - 5 * 60 * 1000)

def refresh_access_token(with_detail=False, max_retries=2):
    refresh_token = _read_refresh_token()
    if not refresh_token:
        result = {'success': False, 'retryable': False, 'error': '缺少refreshToken'}
        return result if with_detail else False
    
    erp_api_url = os.environ.get('ERP_API_URL', 'https://dev.1bgo.com')
    base_url = erp_api_url.rstrip('/')
    refresh_urls = [
        f"{base_url}/admin-api/system/auth/refresh-token",
        f"{base_url}/system/auth/refresh-token",
    ]
    last_result = {'success': False, 'retryable': False, 'error': '刷新登录状态失败'}
    access_token = _read_access_token()
    tenant_id = str(session.get('tenant_id', '')).strip()

    for refresh_url in refresh_urls:
        request_variants = _refresh_request_variants(refresh_token)
        for variant in request_variants:
            for attempt in range(max_retries):
                try:
                    headers = dict(variant.get('headers') or {})
                    if access_token:
                        headers['Authorization'] = f'Bearer {access_token}'
                        headers['accessToken'] = access_token
                    if tenant_id:
                        headers['tenant-id'] = tenant_id
                        headers['tenantId'] = tenant_id
                    request_kwargs = dict(variant)
                    request_kwargs['headers'] = headers
                    response = requests.post(
                        refresh_url,
                        timeout=10,
                        **request_kwargs
                    )
                    if response.status_code == 200:
                        result = response.json()
                        code = str(result.get('code', ''))
                        if result.get('success') or code in ('0', '200'):
                            data = _extract_token_payload(result)
                            if _save_token_session(data):
                                success_result = {'success': True, 'retryable': False, 'error': ''}
                                return success_result if with_detail else True
                            last_result = {'success': False, 'retryable': False, 'error': '刷新响应缺少accessToken'}
                            break
                        error_msg = str(result.get('msg') or result.get('message') or '刷新登录状态失败')
                        if ('refreshToken' in error_msg or 'refresh token' in error_msg.lower()) and ('过期' in error_msg or '失效' in error_msg or 'invalid' in error_msg.lower()):
                            last_result = {'success': False, 'retryable': False, 'error': 'refreshToken已过期，请重新登录'}
                            break
                        last_result = {'success': False, 'retryable': True, 'error': error_msg}
                        continue
                    if response.status_code in (401, 403):
                        message = 'refreshToken已过期，请重新登录'
                        try:
                            payload = response.json()
                            message = str(payload.get('msg') or payload.get('message') or payload.get('error') or message)
                        except Exception:
                            pass
                        last_result = {'success': False, 'retryable': False, 'error': message}
                        break
                    if response.status_code >= 500:
                        last_result = {'success': False, 'retryable': True, 'error': '刷新服务暂时不可用'}
                        time.sleep(0.2 * (attempt + 1))
                        continue
                    last_result = {'success': False, 'retryable': False, 'error': f'刷新失败: HTTP {response.status_code}'}
                    break
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                    last_result = {'success': False, 'retryable': True, 'error': '网络异常导致刷新失败'}
                    time.sleep(0.2 * (attempt + 1))
                    continue
                except Exception as e:
                    last_result = {'success': False, 'retryable': False, 'error': str(e)}
                    break

    return last_result if with_detail else False

def get_valid_token_result():
    """获取有效token，并保留刷新失败原因供调用方区分临时故障和登录失效。"""
    if not session.get('logged_in'):
        return {'success': False, 'token': '', 'retryable': False, 'code': 'UNAUTHORIZED', 'error': '未登录'}

    if not _read_access_token() and _read_refresh_token():
        refresh_result = refresh_access_token(with_detail=True)
        if not refresh_result.get('success'):
            return {
                'success': False,
                'token': '',
                'retryable': bool(refresh_result.get('retryable')),
                'code': 'TOKEN_REFRESH_RETRYABLE' if refresh_result.get('retryable') else 'UNAUTHORIZED',
                'error': refresh_result.get('error') or '刷新登录状态失败'
            }

    if is_token_expired():
        refresh_result = refresh_access_token(with_detail=True)
        if not refresh_result.get('success'):
            return {
                'success': False,
                'token': '',
                'retryable': bool(refresh_result.get('retryable')),
                'code': 'TOKEN_REFRESH_RETRYABLE' if refresh_result.get('retryable') else 'UNAUTHORIZED',
                'error': refresh_result.get('error') or '刷新登录状态失败'
            }

    token = _read_access_token()
    if not token:
        return {'success': False, 'token': '', 'retryable': False, 'code': 'UNAUTHORIZED', 'error': '缺少accessToken'}
    return {'success': True, 'token': token, 'retryable': False, 'code': '', 'error': ''}

def refresh_access_token_standalone(refresh_token, access_token='', tenant_id='', max_retries=2):
    """不依赖 Flask session 的 refresh token 调用，用于后台 reconciler / task_store 恢复场景。

    返回 (success: bool, token_data: dict, error: str)
    token_data 至少包含 accessToken / refreshToken / expiresTime / tenantId 四个字段（成功时）。
    """
    refresh_token = str(refresh_token or '').strip()
    if not refresh_token:
        return False, {}, '缺少refreshToken'

    erp_api_url = os.environ.get('ERP_API_URL', 'https://dev.1bgo.com')
    base_url = erp_api_url.rstrip('/')
    refresh_urls = [
        f"{base_url}/admin-api/system/auth/refresh-token",
        f"{base_url}/system/auth/refresh-token",
    ]
    last_error = '刷新登录状态失败'

    for refresh_url in refresh_urls:
        for variant in _refresh_request_variants(refresh_token):
            for attempt in range(max_retries):
                try:
                    headers = dict(variant.get('headers') or {})
                    if access_token:
                        headers['Authorization'] = f'Bearer {access_token}'
                        headers['accessToken'] = access_token
                    if tenant_id:
                        headers['tenant-id'] = str(tenant_id)
                        headers['tenantId'] = str(tenant_id)
                    request_kwargs = dict(variant)
                    request_kwargs['headers'] = headers
                    response = requests.post(refresh_url, timeout=10, **request_kwargs)
                    if response.status_code == 200:
                        try:
                            payload = response.json()
                        except Exception:
                            last_error = '刷新响应解析失败'
                            break
                        code = str(payload.get('code', ''))
                        if payload.get('success') or code in ('0', '200'):
                            token_data = _extract_token_payload(payload) or {}
                            new_access = str(token_data.get('accessToken') or token_data.get('access_token') or '').strip()
                            new_refresh = str(token_data.get('refreshToken') or token_data.get('refresh_token') or '').strip()
                            if not new_access:
                                last_error = '刷新响应缺少accessToken'
                                break
                            expire_time = _extract_expire_time_from_payload(token_data)
                            tenant = str(token_data.get('tenantId') or token_data.get('tenant_id') or tenant_id or '').strip()
                            return True, {
                                'accessToken': new_access,
                                'refreshToken': new_refresh or refresh_token,
                                'expiresTime': expire_time,
                                'tenantId': tenant,
                            }, ''
                        err = str(payload.get('msg') or payload.get('message') or '刷新登录状态失败')
                        last_error = err
                        if ('refreshToken' in err or 'refresh token' in err.lower()) and (
                            '过期' in err or '失效' in err or 'invalid' in err.lower()
                        ):
                            return False, {}, 'refreshToken已过期，请重新登录'
                        continue
                    if response.status_code in (401, 403):
                        try:
                            payload = response.json()
                            last_error = str(payload.get('msg') or payload.get('message') or 'refreshToken已过期，请重新登录')
                        except Exception:
                            last_error = 'refreshToken已过期，请重新登录'
                        return False, {}, last_error
                    if response.status_code >= 500:
                        last_error = '刷新服务暂时不可用'
                        time.sleep(0.2 * (attempt + 1))
                        continue
                    last_error = f'刷新失败: HTTP {response.status_code}'
                    break
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                    last_error = '网络异常导致刷新失败'
                    time.sleep(0.2 * (attempt + 1))
                    continue
                except Exception as exc:
                    last_error = str(exc)
                    break

    return False, {}, last_error


def get_valid_token():
    """获取有效的token，如果即将过期则自动刷新
    
    Returns:
        str: 有效的accessToken，如果无法获取则返回空字符串
    """
    return get_valid_token_result().get('token', '')

def require_auth(f):
    """装饰器：检查登录状态，如果token过期尝试刷新"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return jsonify({'success': False, 'code': 'UNAUTHORIZED', 'error': '未登录'}), 401
        
        # 检查并刷新token
        token_result = get_valid_token_result()
        if not token_result.get('success'):
            if token_result.get('retryable'):
                return jsonify({
                    'success': False,
                    'code': 'TOKEN_REFRESH_RETRYABLE',
                    'error': token_result.get('error') or '刷新登录状态暂时失败，请稍后重试'
                }), 503
            return jsonify({'success': False, 'code': 'UNAUTHORIZED', 'error': '登录已过期，请重新登录'}), 401
        
        return f(*args, **kwargs)
    return decorated_function
