# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class OpenAgentSupplyChangeParam(BaseApi):
    """1688寻源换供agent 对外开放，基于agent提供换供基础能力


    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.batchId = None
        self.supplyChangeDatas = None

    def get_api_uri(self):
        return '1/com.alibaba.ai/open.agent.supplyChange'

    def get_required_params(self):
        return ['batchId', 'supplyChangeDatas']

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
