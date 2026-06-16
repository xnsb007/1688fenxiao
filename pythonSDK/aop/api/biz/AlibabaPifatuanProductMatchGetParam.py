# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaPifatuanProductMatchGetParam(BaseApi):
    """精选货源同款商品匹配

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.fenxiao&n=alibaba.pifatuan.product.match.get&v=1&cat=

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.outPersonId = None
        self.outOfferId = None

    def get_api_uri(self):
        return '1/com.alibaba.fenxiao/alibaba.pifatuan.product.match.get'

    def get_required_params(self):
        return ['outPersonId', 'outOfferId']

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
