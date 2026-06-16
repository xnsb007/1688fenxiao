# -*- coding: utf-8 -*-
"""
任务执行日志服务
"""

import json
import time
from typing import Dict, Any, Optional
from app.models import get_db
from app.utils.time_utils import get_current_app_date_str


class TaskLogService:
    _LIST_SELECT_SQL = '''
        SELECT id, username, request_method, request_path,
               SUBSTRING(request_params, 1, 20) as request_params_preview,
               SUBSTRING(response_data, 1, 20) as response_data_preview,
               success, error_message, ip_address, execution_time,
               DATE_FORMAT(created_at, '%%Y-%%m-%%d %%H:%%i:%%s') as created_at
        FROM task_execution_log
    '''

    _DETAIL_SELECT_SQL = '''
        SELECT id, username, request_method, request_path, request_params,
               response_data, success, error_message, ip_address, user_agent,
               execution_time,
               DATE_FORMAT(created_at, '%%Y-%%m-%%d %%H:%%i:%%s') as created_at
        FROM task_execution_log
    '''
    """任务执行日志服务"""
    
    def log(
        self,
        username: str,
        request_method: str,
        request_path: str,
        request_params: Any = None,
        response_data: Any = None,
        success: bool = True,
        error_message: str = '',
        ip_address: str = '',
        user_agent: str = '',
        execution_time: int = 0
    ) -> int:
        """记录任务执行日志
        
        Args:
            username: 请求账号
            request_method: 请求方法
            request_path: 请求路径
            request_params: 请求参数
            response_data: 返回数据
            success: 是否成功
            error_message: 错误信息
            ip_address: IP地址
            user_agent: 用户代理
            execution_time: 执行时间(毫秒)
            
        Returns:
            日志ID
        """
        conn = get_db()
        cursor = conn.cursor()
        
        # 序列化参数和响应
        params_str = self._serialize(request_params)
        response_str = self._serialize(response_data)
        
        cursor.execute('''
            INSERT INTO task_execution_log 
            (username, request_method, request_path, request_params, response_data, 
             success, error_message, ip_address, user_agent, execution_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (username, request_method, request_path, params_str, response_str,
              1 if success else 0, error_message, ip_address, user_agent, execution_time))
        
        log_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return log_id
    
    def _serialize(self, data: Any, max_length: int = 100000) -> str:
        """序列化数据为JSON字符串，限制最大长度"""
        if data is None:
            return ''
        if isinstance(data, str):
            result = data
        else:
            try:
                result = json.dumps(data, ensure_ascii=False, default=str)
            except:
                result = str(data)
        if len(result) > max_length:
            result = result[:max_length] + '...[truncated]'
        return result
    
    def get_logs(
        self,
        page: int = 1,
        page_size: int = 20,
        username: str = '',
        request_method: str = '',
        success: Optional[bool] = None,
        start_date: str = '',
        end_date: str = ''
    ) -> Dict[str, Any]:
        """获取日志列表
        
        Args:
            page: 页码
            page_size: 每页数量
            username: 用户名过滤
            request_method: 请求方法过滤
            success: 成功状态过滤
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            分页结果
        """
        import time
        query_start = time.time()
        
        conn = get_db()
        cursor = conn.cursor()
        
        # 限制最大查询范围（最近90天）
        where_clauses = []
        params = []
        
        # 默认只查询最近90天的数据，提高性能
        if not start_date:
            from datetime import datetime, timedelta
            app_today = datetime.strptime(get_current_app_date_str(), '%Y-%m-%d')
            default_start = (app_today - timedelta(days=90)).strftime('%Y-%m-%d')
            where_clauses.append('created_at >= %s')
            params.append(f'{default_start} 00:00:00')
        
        if username:
            where_clauses.append('username LIKE %s')
            params.append(f'%{username}%')
        
        if request_method:
            where_clauses.append('request_method = %s')
            params.append(request_method)
        
        if success is not None:
            where_clauses.append('success = %s')
            params.append(1 if success else 0)
        
        if start_date:
            where_clauses.append('created_at >= %s')
            params.append(f'{start_date} 00:00:00')
        
        if end_date:
            where_clauses.append('created_at <= %s')
            params.append(f'{end_date} 23:59:59')
        
        where_sql = ' AND '.join(where_clauses) if where_clauses else '1=1'
        
        # 查询数据 - 只选择需要的字段，避免读取大TEXT字段
        offset = (page - 1) * page_size
        data_sql = f'''
            {self._LIST_SELECT_SQL}
            WHERE {where_sql}
            ORDER BY task_execution_log.created_at DESC
            LIMIT %s OFFSET %s
        '''
        query_params = params + [page_size, offset]
        cursor.execute(data_sql, query_params)
        rows = cursor.fetchall()
        
        # 使用近似总数，避免COUNT(*)慢查询
        # 如果是第一页且结果数小于page_size，则总数就是结果数
        # 否则使用一个估算值
        if page == 1 and len(rows) < page_size:
            total = len(rows)
        else:
            # 查询下一页是否存在来判断是否还有更多数据
            cursor.execute(f'''
                SELECT 1 FROM task_execution_log 
                WHERE {where_sql}
                ORDER BY task_execution_log.created_at DESC
                LIMIT 1 OFFSET %s
            ''', params + [page_size * page])
            has_more = cursor.fetchone() is not None
            # 估算总数：当前页码 * 每页数量 + (是否有更多 ? 1000 : 0)
            total = page * page_size + (1000 if has_more else 0)
        
        conn.close()
        
        query_time = int((time.time() - query_start) * 1000)
        print(f"[Task Log] Query completed in {query_time}ms, rows: {len(rows)}, page: {page}")
        
        return {
            'success': True,
            'logs': [self._normalize_log_row(row) for row in rows],
            'total': total,
            'page': page,
            'page_size': page_size,
            'query_time': query_time
        }
    
    def get_log_detail(self, log_id: int) -> Optional[Dict[str, Any]]:
        """获取日志详情
        
        Args:
            log_id: 日志ID
            
        Returns:
            日志详情
        """
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute(f'''
            {self._DETAIL_SELECT_SQL}
            WHERE id = %s
        ''', (log_id,))
        row = cursor.fetchone()
        
        conn.close()
        
        return self._normalize_log_row(row) if row else None

    def _normalize_log_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(row)
        created_at = result.get('created_at')
        if created_at is not None:
            result['created_at'] = str(created_at).strip()
        return result
    
    def get_stats(self) -> Dict[str, Any]:
        """获取日志统计 - 优化版本，单次查询获取所有统计"""
        conn = get_db()
        cursor = conn.cursor()
        
        # 使用单个查询获取所有统计，减少数据库往返
        cursor.execute('''
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN DATE(created_at) = CURDATE() THEN 1 ELSE 0 END) as today,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as success_count,
                SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as fail_count
            FROM task_execution_log
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
        ''')
        row = cursor.fetchone()
        
        conn.close()
        
        return {
            'total': row['total'] or 0,
            'today': row['today'] or 0,
            'success_count': row['success_count'] or 0,
            'fail_count': row['fail_count'] or 0
        }


task_log_service = TaskLogService()


def log_task(f):
    """装饰器：自动记录任务执行日志"""
    from functools import wraps
    from flask import session, request
    
    @wraps(f)
    def decorated_function(*args, **kwargs):
        start_time = time.time()
        username = session.get('username', 'unknown')
        request_method = request.method
        request_path = request.path
        
        # 获取请求参数
        if request.method == 'GET':
            request_params = dict(request.args)
        else:
            try:
                request_params = request.get_json() or {}
            except:
                request_params = {}
        
        ip_address = request.remote_addr or ''
        user_agent = request.headers.get('User-Agent', '')[:500]
        
        success = True
        error_message = ''
        response_data = None
        
        try:
            result = f(*args, **kwargs)
            if isinstance(result, tuple):
                response_data = result[0]
            else:
                response_data = result
            
            # 如果返回的是 Response 对象，获取其数据
            if hasattr(response_data, 'get_json'):
                try:
                    response_data = response_data.get_json()
                except:
                    pass
            
            return result
        except Exception as e:
            success = False
            error_message = str(e)
            raise
        finally:
            execution_time = int((time.time() - start_time) * 1000)
            
            task_log_service.log(
                username=username,
                request_method=request_method,
                request_path=request_path,
                request_params=request_params,
                response_data=response_data if success else None,
                success=success,
                error_message=error_message,
                ip_address=ip_address,
                user_agent=user_agent,
                execution_time=execution_time
            )
    
    return decorated_function
