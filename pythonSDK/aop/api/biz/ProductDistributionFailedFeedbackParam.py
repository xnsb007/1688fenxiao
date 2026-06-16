# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class ProductDistributionFailedFeedbackParam(BaseApi):
    """铺货工具铺货失败信息回传


    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.offerId = None
        self.channel = None
        self.category = None
        self.errorInfo = None
        self.eventTime = None
        self.hitLabId = None
        self.aiMaterialDetailFields = None

    def get_api_uri(self):
        return '1/com.alibaba.fenxiao/product.distributionFailed.feedback'

    def get_required_params(self):
        return ['offerId', 'channel', 'category', 'errorInfo', 'eventTime', 'hitLabId', 'aiMaterialDetailFields']

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
