# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaPifatuanProductSearchTagListParam(BaseApi):
    """精选货源商品查询标获取

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.fenxiao&n=alibaba.pifatuan.product.search.tag.list&v=1&cat=

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None

    def get_api_uri(self):
        return '1/com.alibaba.fenxiao/alibaba.pifatuan.product.search.tag.list'

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
