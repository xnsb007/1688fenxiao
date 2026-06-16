# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class ProductKeywordsSearchParam(BaseApi):
    """国内分销词搜选品


    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.param = None

    def get_api_uri(self):
        return '1/com.alibaba.fenxiao/product.keywords.search'

    def get_required_params(self):
        return ['param']

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
