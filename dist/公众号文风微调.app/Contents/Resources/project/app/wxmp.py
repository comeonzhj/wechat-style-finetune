#!/usr/bin/env python3
"""公众号文章一键导出：借公众号后台的列表接口，把某个号的历史文章全抓下来。

原理
----
公众号后台（mp.weixin.qq.com）有个「图文素材/发表记录」用的内部接口：

    GET /cgi-bin/appmsg?action=list_ex&fakeid=<账号ID>&begin=0&count=5
                        &type=9&token=<登录态token>&lang=zh_CN&f=json&ajax=1

带上有登录态的 Cookie 就能分页拉到该作者所有文章的标题和链接。

所以用户只需要从浏览器复制两样东西：
1. **Cookie**（登录公众号后台后，F12 → Network → 任意 cgi-bin 请求 → Request Headers 里的 Cookie）
2. **Token**（后台地址栏里的 `token=xxxxx`）

接口是内部的，会变、会限流。所以：
- 每页之间 sleep 1 秒，避免触发风控（返回 200013 就是太频繁）
- 失败要给出人话错误，不能让用户对着 ret=200003 发呆
"""

from __future__ import annotations

import json
import random
import time
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

BASE = "https://mp.weixin.qq.com"
LIST_API = BASE + "/cgi-bin/appmsg"
SEARCH_API = BASE + "/cgi-bin/searchbiz"

RET_HINT = {
    0: "",
    200003: "登录态失效：Cookie 或 token 过期了，重新登录后台复制一次",
    200013: "请求太频繁，被限流了：等 10 分钟再来，或者把每页间隔调大",
    200002: "参数不对：检查 fakeid / token 是否完整",
    -6: "需要验证：在浏览器里打开一次后台页面，过掉验证码再来",
}


class MPError(Exception):
    pass


def _http(api, cookie, params, timeout=20) -> str:
    """只负责发请求拿原始文本，便于测试时替换。"""
    url = api + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Cookie": cookie,
        "Referer": BASE + "/cgi-bin/appmsg?t=media/appmsg_edit&action=edit&lang=zh_CN",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _get(api, cookie, params, timeout=20):
    raw = _http(api, cookie, params, timeout)
    try:
        data = json.loads(raw)
    except Exception:
        snippet = raw[:200].replace("\n", " ")
        raise MPError("接口没返回 JSON（多半是登录态失效被重定向）：%s" % snippet)
    ret = (data.get("base_resp") or {}).get("ret", 0)
    if ret != 0:
        raise MPError(RET_HINT.get(ret) or ("接口报错 ret=%s %s" % (ret, data.get("base_resp"))))
    return data


def search_account(name, token, cookie, count=5):
    """按公众号名称搜 fakeid。用户不确定账号 ID 时用。"""
    data = _get(SEARCH_API, cookie, {
        "action": "search_biz", "begin": 0, "count": count,
        "query": name, "token": token, "lang": "zh_CN", "f": "json", "ajax": 1,
    })
    out = []
    for item in data.get("list") or []:
        out.append({
            "fakeid": item.get("fakeid"),
            "nickname": item.get("nickname"),
            "alias": item.get("alias") or "",
            "signature": (item.get("signature") or "")[:60],
        })
    return out


def fetch_page(fakeid, token, cookie, begin=0, count=5):
    data = _get(LIST_API, cookie, {
        "action": "list_ex", "begin": begin, "count": count, "fakeid": fakeid,
        "type": 9, "query": "", "token": token, "lang": "zh_CN", "f": "json", "ajax": 1,
    })
    total = data.get("app_msg_cnt") or data.get("total_cnt") or 0
    items = []
    for it in data.get("app_msg_list") or []:
        items.append({
            "title": it.get("title") or "",
            "link": it.get("link") or "",
            "create_time": it.get("create_time") or 0,
            "aid": str(it.get("aid") or ""),
        })
    return total, items


def export_links(fakeid, token, cookie, limit=None, page_size=5, delay=1.2, log=print):
    """分页拉全量文章链接。返回 {total, links:[{title, link, create_time}]}。"""
    links, seen, begin = [], set(), 0
    total, _ = fetch_page(fakeid, token, cookie, 0, 1)
    log("该公众号共有 %d 篇文章" % total)

    while True:
        if limit and len(links) >= limit:
            break
        try:
            _, items = fetch_page(fakeid, token, cookie, begin, page_size)
        except MPError as e:
            if begin == 0:
                raise
            log("第 %d 页失败（%s），继续后面的页" % (begin // page_size + 1, e))
            items = []
        if not items:
            break
        for it in items:
            if it["link"] and it["link"] not in seen and "/s/" in it["link"]:
                seen.add(it["link"])
                links.append(it)
        log("已取 %d / %d 篇" % (len(links), total or "?"))
        begin += page_size
        if begin > (total or 0) + page_size * 2:   # 总数不准时的兜底
            break
        time.sleep(delay + random.uniform(0, 0.4))
    return {"total": total, "links": links}


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(1)
    cookie, token = sys.argv[1], sys.argv[2]
    name = sys.argv[3] if len(sys.argv) > 3 else ""
    print(json.dumps(search_account(name, token, cookie), ensure_ascii=False, indent=1))
