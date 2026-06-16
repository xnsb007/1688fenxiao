# -*- coding: utf-8 -*-
from aop.api.base import BaseApi

class AlibabaFeedbackOutProductAddParam(BaseApi):
    """下游热销商品匹配1688同款代发商品输出

    References
    ----------
    https://open.1688.com/api/api.htm?ns=com.alibaba.fenxiao&n=alibaba.feedback.out.product.add&v=1&cat=

    """

    def __init__(self, domain=None):
        BaseApi.__init__(self, domain)
        self.outUserId = None
        self.channel = None
        self.outShopId = None
        self.outItemId = None
        self.title = None
        self.url = None
        self.imgUrl = None
        self.skuName = None
        self.skuImgUrl = None
        self.brand = None
        self.skuDesc = None
        self.categoryLv1Name = None
        self.categoryLv2Name = None
        self.categoryLv3Name = None
        self.features = None
        self.avgSalePrice30d = None
        self.lowestSalePrice30d = None
        self.saleQuantity1d = None
        self.saleAmount1d = None

    def get_api_uri(self):
        return '1/com.alibaba.fenxiao/alibaba.feedback.out.product.add'

    def get_required_params(self):
        return ['outUserId', 'channel', 'outShopId', 'outItemId', 'title', 'url', 'imgUrl', 'skuName', 'skuImgUrl', 'brand', 'skuDesc', 'categoryLv1Name', 'categoryLv2Name', 'categoryLv3Name', 'features', 'avgSalePrice30d', 'lowestSalePrice30d', 'saleQuantity1d', 'saleAmount1d']

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
