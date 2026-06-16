# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaCategoryGetParam(BaseApi):
    """类目查询。如果需要获取所有1688类目信息，需要从根类目开始遍历获取整个类目树。即：先传0获取所有一级类目ID，然后在通过获取到的一级类目ID遍历获取所二级类目，最后通过遍历二级类目ID获取三级类目。注意：1688类目仅三级，三级类目即发布商品所需的叶子类目。

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.product&n=alibaba.category.get&v=1&cat=category

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.categoryID = None

    def get_api_uri(self):
        return '1/com.alibaba.product/alibaba.category.get'

    def get_required_params(self):
        return ['categoryID']

    def get_multipart_params(self):
        return []

    def need_sign(self):
        return True

    def need_timestamp(self):
        return False

    def need_auth(self):
        return False

    def need_https(self):
        return False

    def is_inner_api(self):
        return False
