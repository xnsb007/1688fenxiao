# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class DkeyGetParam(BaseApi):
    """分销生成dkey，用于测试

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.fenxiao&n=dkey.get&v=1&cat=

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.offerIds = None
        self.entranceCode = None

    def get_api_uri(self):
        return '1/com.alibaba.fenxiao/dkey.get'

    def get_required_params(self):
        return ['offerIds', 'entranceCode']

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
