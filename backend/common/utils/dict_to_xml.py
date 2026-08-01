"""字典转 XML 工具。

sqlbot_xpack（企业版扩展）的 custom_prompt 模块依赖此模块。
基于 dicttoxml 库封装，提供 dict_to_xml()。
"""

import dicttoxml


def dict_to_xml(data, custom_root: str = "root", ids: bool = False) -> str:
    """将 dict 转换为 XML 字符串。

    Args:
        data: 要转换的 dict（可嵌套 list/dict）。
        custom_root: 根节点名，默认 "root"。
        ids: 是否给元素加 id 属性（dicttoxml 的 item_wrap/attr_type 相关）。

    Returns:
        XML 字符串（utf-8 解码）。
    """
    xml_bytes = dicttoxml.dicttoxml(data, custom_root=custom_root, ids=ids)
    return xml_bytes.decode("utf-8")
