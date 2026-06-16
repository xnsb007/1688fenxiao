# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class OpenAgentDeepSearchParam(BaseApi):
    """${api.doc}


    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)

    def get_api_uri(self):
        return '1/com.alibaba.ai/open.agent.deepSearch'

    def get_required_params(self):
        return []

    def get_multipart_params(self):
        return []

    def need_sign(self):
        return False

    def need_timestamp(self):
        return False

    def need_auth(self):
        return False

    def need_https(self):
        return False

    def is_inner_api(self):
        return False
