# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaAccountBasicParam(BaseApi):
    """获取授权用户的基本信息

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.account&n=alibaba.account.basic&v=1&cat=ACCOUNT_INFO

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None

    def get_api_uri(self):
        return '1/com.alibaba.account/alibaba.account.basic'

    def get_required_params(self):
        return []

    def get_multipart_params(self):
        return []

    def need_sign(self):
        return True

    def need_timestamp(self):
        return False

    def need_auth(self):
        return True

    def need_https(self):
        return False

    def is_inner_api(self):
        return False
