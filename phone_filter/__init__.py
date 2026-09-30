"""客户号码剔除工具包。

包含两个独立应用：
- ``phone_filter.app``        数据库版剔除器（SQLite + 模糊匹配，GUI）
- ``phone_filter.us_filter``  美国号码高速版剔除器（numpy 向量化，GUI）
"""

__version__ = "5.0.0"
