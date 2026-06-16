# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaLogisticsMyFreightTemplateListGetParam(BaseApi):
    """根据物流模版ID获取卖家的物流模板。运费模板ID为0表示运费说明，为1表示卖家承担运费

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.logistics&n=alibaba.logistics.myFreightTemplate.list.get&v=1&cat=my_logistics_meta

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.access_token = None
        self.templateId = None
        self.querySubTemplate = None
        self.queryRate = None

    def get_api_uri(self):
        return '1/com.alibaba.logistics/alibaba.logistics.myFreightTemplate.list.get'

    def get_required_params(self):
        return ['templateId', 'querySubTemplate', 'queryRate']

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
