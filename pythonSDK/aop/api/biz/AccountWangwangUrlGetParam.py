# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AccountWangwangUrlGetParam(BaseApi):
    """获取唤起旺旺聊天的链接

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.account&n=account.wangwangUrl.get&v=1&cat=ACCOUNT_INFO

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.toOpenUid = None

    def get_api_uri(self):
        return '1/com.alibaba.account/account.wangwangUrl.get'

    def get_required_params(self):
        return ['toOpenUid']

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
