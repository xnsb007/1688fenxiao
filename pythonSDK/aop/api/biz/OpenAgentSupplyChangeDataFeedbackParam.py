# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class OpenAgentSupplyChangeDataFeedbackParam(BaseApi):
    """1688寻源换供agent 对外开放效果数据回流


    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.relationId = None
        self.offerId = None
        self.skuId = None
        self.reason = None
        self.skuName = None
        self.accepted = None

    def get_api_uri(self):
        return '1/com.alibaba.ai/open.agent.supplyChangeDataFeedback'

    def get_required_params(self):
        return ['relationId', 'offerId', 'skuId', 'reason', 'skuName', 'accepted']

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
