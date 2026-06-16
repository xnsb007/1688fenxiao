# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class PoolProductPullParam(BaseApi):
    """通过商品池ID直接批量拉取池中商品数据


    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.offerPoolQueryParam = None

    def get_api_uri(self):
        return '1/com.alibaba.fenxiao.crossborder/pool.product.pull'

    def get_required_params(self):
        return ['offerPoolQueryParam']

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
