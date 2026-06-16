# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class SupplySimilarOfferSearchParam(BaseApi):
    """提供同款换供&搭配推荐能力


    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.imgBase64 = None
        self.imgUrl = None
        self.keywords = None
        self.platform = None
        self.platformItemId = None
        self.originalItemId = None
        self.scene = None

    def get_api_uri(self):
        return '1/com.alibaba.fenxiao/supply.similarOffer.search'

    def get_required_params(self):
        return ['imgBase64', 'imgUrl', 'keywords', 'platform', 'platformItemId', 'originalItemId', 'scene']

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
