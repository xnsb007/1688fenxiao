# -*- coding: utf-8 -*-
"""
腾讯云COS图片上传服务
使用官方 cos-python-sdk-v5
"""

import os
import uuid
import requests
import time
import json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional
import logging
import warnings

# 禁用 SSL 警告
warnings.filterwarnings('ignore', message='Unverified HTTPS request')
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except:
    pass

logger = logging.getLogger(__name__)

try:
    from qcloud_cos import CosConfig as QcloudCosConfig
    from qcloud_cos import CosS3Client
    COS_SDK_AVAILABLE = True
except ImportError:
    COS_SDK_AVAILABLE = False
    logger.warning("cos-python-sdk-v5 not installed, COS upload will be disabled")


class CosSettings:
    """腾讯云COS配置"""
    
    def __init__(self):
        self.secret_id = os.environ.get('TENCENT_COS_SECRET_ID', '')
        self.secret_key = os.environ.get('TENCENT_COS_SECRET_KEY', '')
        self.region = os.environ.get('TENCENT_COS_REGION', 'ap-guangzhou')
        self.bucket = os.environ.get('TENCENT_COS_BUCKET', '')
        self.public_domain = os.environ.get('TENCENT_COS_PUBLIC_DOMAIN', '') or os.environ.get('TENCENT_COS_CUSTOM_DOMAIN', '')
    
    def is_configured(self) -> bool:
        """检查是否已配置"""
        return bool(self.secret_id and self.secret_key and self.bucket)

    def get_public_host(self) -> str:
        domain = (self.public_domain or '').strip()
        if domain:
            domain = domain.replace('https://', '').replace('http://', '').strip().strip('/')
            return domain
        return f"{self.bucket}.cos.{self.region}.myqcloud.com"


class UploadResult:
    """上传结果"""
    
    def __init__(self):
        self.success = False
        self.url = ''
        self.original_url = ''
        self.error_message = ''
        self.retry_count = 0


class TencentCosUploader:
    """腾讯云COS上传服务"""
    
    MAX_WORKERS = 5
    MAX_RETRIES = 3
    TIMEOUT = 30
    
    def __init__(self, config: Optional[CosSettings] = None):
        self.config = config or CosSettings()
        self._client = None
    
    def _get_client(self):
        """获取COS客户端（单例）"""
        if self._client is None and COS_SDK_AVAILABLE and self.config.is_configured():
            cos_config = QcloudCosConfig(
                Region=self.config.region,
                SecretId=self.config.secret_id,
                SecretKey=self.config.secret_key,
                Token=None,
                Scheme='https'
            )
            self._client = CosS3Client(cos_config)
        return self._client
    
    def generate_cos_key(self, original_url: str, prefix: str = 'products') -> str:
        """生成COS对象键
        
        Args:
            original_url: 原图片URL
            prefix: 前缀目录
            
        Returns:
            COS对象键
        """
        timestamp = datetime.now().strftime('%Y%m%d')
        unique_id = uuid.uuid4().hex[:16]
        
        ext = '.jpg'
        if '.' in original_url:
            url_path = original_url.split('?')[0]
            possible_ext = url_path.rsplit('.', 1)[-1].lower()
            if possible_ext in ('jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'):
                ext = f'.{possible_ext}'
        
        return f"{prefix}/{timestamp}/{unique_id}{ext}"
    
    def upload_image_from_url(self, image_url: str, cos_key: str) -> UploadResult:
        """从URL下载图片并上传到COS
        
        Args:
            image_url: 原图片URL
            cos_key: COS对象键（云端路径）
            
        Returns:
            UploadResult: 上传结果
        """
        result = UploadResult()
        result.original_url = image_url
        
        if not COS_SDK_AVAILABLE:
            result.error_message = "COS SDK未安装"
            return result
        
        client = self._get_client()
        if client is None:
            result.error_message = "COS未配置"
            return result
        
        try:
            # 添加请求头模拟浏览器，避免被限流
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': 'https://detail.1688.com/',
                'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8'
            }
            response = requests.get(image_url, timeout=self.TIMEOUT, verify=False, headers=headers)
            if response.status_code != 200:
                result.error_message = f"下载图片失败: HTTP {response.status_code}"
                return result
            
            content_type = response.headers.get('Content-Type', 'image/jpeg')
            image_data = response.content
            
            response = client.put_object(
                Bucket=self.config.bucket,
                Body=image_data,
                Key=cos_key,
                ContentType=content_type,
                EnableMD5=False
            )
            
            if 'ETag' in response:
                result.success = True
                host = self.config.get_public_host()
                result.url = f"https://{host}/{cos_key}"
            else:
                result.error_message = "上传响应异常"
                
        except requests.exceptions.Timeout:
            result.error_message = "下载图片超时"
        except requests.exceptions.RequestException as e:
            result.error_message = f"下载图片失败: {str(e)}"
        except Exception as e:
            result.error_message = f"上传失败: {str(e)}"
            logger.exception(f"COS upload exception: {e}")
        
        return result
    
    def upload_with_retry(self, image_url: str, cos_key: str, max_retries: int = None) -> UploadResult:
        """带重试机制的上传
        
        Args:
            image_url: 原图片URL
            cos_key: COS对象键
            max_retries: 最大重试次数
            
        Returns:
            UploadResult: 上传结果
        """
        max_retries = max_retries or self.MAX_RETRIES
        result = None
        
        for i in range(max_retries + 1):
            result = self.upload_image_from_url(image_url, cos_key)
            
            if result.success:
                result.retry_count = i
                return result
            
            # 遇到 HTTP 420 等限流错误时，增加等待时间
            if i < max_retries:
                wait_time = 2 * (i + 1)  # 2, 4, 6 秒递增
                logger.info(f"图片上传失败，{wait_time}秒后重试 ({i+1}/{max_retries}): {image_url[:50]}...")
                time.sleep(wait_time)
        
        result.retry_count = max_retries
        return result
    
    def batch_upload_images(
        self, 
        image_urls: List[str], 
        prefix: str = 'products',
        progress_callback: Optional[callable] = None
    ) -> Dict[str, str]:
        """批量上传图片
        
        Args:
            image_urls: 图片URL列表
            prefix: 前缀目录
            progress_callback: 进度回调 (current, total, message)
            
        Returns:
            Dict[str, str]: {原URL: 新URL} 映射
        """
        if not self.config.is_configured():
            logger.warning("COS未配置，跳过图片上传")
            return {}
        
        if not COS_SDK_AVAILABLE:
            logger.warning("COS SDK未安装，跳过图片上传")
            return {}
        
        results = {}
        total = len(image_urls)
        completed = 0
        success_count = 0
        fail_count = 0
        
        unique_urls = list(set(url for url in image_urls if url))
        actual_total = len(unique_urls)
        
        print(f"[COS] 开始上传 {actual_total} 张图片...")
        
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            future_to_url = {
                executor.submit(
                    self.upload_with_retry, 
                    url, 
                    self.generate_cos_key(url, prefix)
                ): url
                for url in unique_urls
            }
            
            for future in as_completed(future_to_url):
                original_url = future_to_url[future]
                try:
                    upload_result = future.result(timeout=60)
                    if upload_result.success:
                        results[original_url] = upload_result.url
                        success_count += 1
                        print(f"[COS] 上传成功 ({success_count}/{actual_total}): {original_url[:60]}...")
                    else:
                        results[original_url] = original_url
                        fail_count += 1
                        print(f"[COS] 上传失败 ({fail_count}): {original_url[:60]}... - {upload_result.error_message}")
                except Exception as e:
                    results[original_url] = original_url
                    fail_count += 1
                    print(f"[COS] 上传异常 ({fail_count}): {original_url[:60]}... - {e}")
                
                completed += 1
                if progress_callback:
                    progress_callback(completed, actual_total, f"正在上传图片 ({completed}/{actual_total})...")
        
        print(f"[COS] 上传完成: 成功 {success_count} 张, 失败 {fail_count} 张")
        
        return results


tencent_cos_uploader = TencentCosUploader()
