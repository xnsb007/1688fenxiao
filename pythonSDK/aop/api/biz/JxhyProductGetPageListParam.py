# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class JxhyProductGetPageListParam(BaseApi):
    """精选货源商品列表查询（使用主搜引擎查询，和图搜、词搜能力保持一致）


    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.pageNum = None
        self.pageSize = None
        self.keyword = None
        self.ruleIds = None
        self.categoryId = None
        self.priceStart = None
        self.priceEnd = None
        self.filters = None

    def get_api_uri(self):
        return '1/com.alibaba.fenxiao/jxhy.product.getPageList'

    def get_required_params(self):
        return ['pageNum', 'pageSize']

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
