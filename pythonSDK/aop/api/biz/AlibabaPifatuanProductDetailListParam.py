# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaPifatuanProductDetailListParam(BaseApi):
    """精选货源商品详情批量查询去除没有retailPrice的sku

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.fenxiao&n=alibaba.pifatuan.product.detail.list&v=2&cat=

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.offerIds = None

    def get_api_uri(self):
        return '2/com.alibaba.fenxiao/alibaba.pifatuan.product.detail.list'

    def get_required_params(self):
        return ['offerIds']

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
