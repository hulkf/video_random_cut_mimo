#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
综合视频下载核心模块
支持链接类型:
  1. 淘宝/天猫商品链接 (短链/完整链接) → 解析页面提取videoId
  2. 淘宝视频直链 (cloud.video.taobao.com) → 直接提取contentId下载
  3. 抖音商品链接 (v.douyin.com 短链 / haohuo.jinritemai.com) → 解析API提取视频URL
"""

import os
import re
import time
import json
import atexit
import threading
import urllib.request
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

SCRIPT_DIR = Path(__file__).parent.parent
AUTH_FILE = SCRIPT_DIR / "taobao_auth.json"
GENERIC_URL = "https://cloud.video.taobao.com/play/u/0/p/1/e/6/t/1/{vid}.mp4"

# === 链接类型常量 ===
LINK_TYPE_TAOBAO_DIRECT = "taobao_direct"      # 淘宝视频直链
LINK_TYPE_TAOBAO_PRODUCT = "taobao_product"     # 淘宝/天猫商品页链接
LINK_TYPE_DOUYIN = "douyin"                     # 抖音商品链接
LINK_TYPE_UNKNOWN = "unknown"


def detect_link_type(url):
    """识别链接类型"""
    url_lower = url.lower()

    # 淘宝视频直链: cloud.video.taobao.com/play/u/.../xxx.mp4
    if re.search(r'cloud\.video\.taobao\.com/play/u/\d+/p/\d+/e/\d+/t/\d+/(\d+)\.mp4', url_lower):
        return LINK_TYPE_TAOBAO_DIRECT

    # 抖音商品链接 (短链 + 完整链接)
    if any(k in url_lower for k in ['v.douyin.com', 'douyin.com', 'haohuo.jinritemai.com',
                                     'fenbi.jinritemai.com', 'buyin.jinritemai.com',
                                     'dy.com', 'jinritemai.com']):
        return LINK_TYPE_DOUYIN

    # 淘宝/天猫商品链接
    if any(k in url_lower for k in ['taobao.com', 'tmall.com', 'tb.cn', 'e.tb.cn',
                                     'm.tb.cn', 't.cn', 'a.m.taobao.com']):
        return LINK_TYPE_TAOBAO_PRODUCT

    return LINK_TYPE_UNKNOWN


def extract_direct_video_id(url):
    """从淘宝视频直链中提取 contentId"""
    m = re.search(r'cloud\.video\.taobao\.com/play/u/\d+/p/\d+/e/\d+/t/\d+/(\d+)\.mp4', url, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def _extract_target_from_short_page(html):
    """
    从短链落地页 HTML 中提取目标商品 URL。
    覆盖三种格式:
      1. itemIds=123456          (跳转参数式)
      2. var url = 'https://item.taobao.com/item.htm?id=123456...'  (直写目标地址式)
      3. 任意 ?id=1234567890     (兜底，要求至少10位数字，避免误匹配短参数)
    """
    m = re.search(r'itemIds=(\d+)', html)
    if m:
        return f"https://detail.tmall.com/item.htm?id={m.group(1)}"
    m = re.search(r'(item\.taobao\.com|detail\.tmall\.com|detail\.taobao\.com)[^\'"\s]*?[?&]id=(\d{10,})', html)
    if m:
        return f"https://{m.group(1)}/item.htm?id={m.group(2)}"
    m = re.search(r'[?&]id=(\d{10,})', html)
    if m:
        return f"https://detail.tmall.com/item.htm?id={m.group(1)}"
    return None


def resolve_short_url(url):
    if any(k in url for k in ['e.tb.cn', 'tb.cn', 'm.tb.cn', 't.cn']):
        try:
            import requests as req_lib
            resp = req_lib.get(url, allow_redirects=True, timeout=15,
                             headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
            final_url = resp.url
            if any(k in final_url for k in ['tb.cn', 'e.tb.cn']):
                target = _extract_target_from_short_page(resp.text)
                if target:
                    final_url = target
            return final_url
        except ImportError:
            try:
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                })
                resp = urllib.request.urlopen(req, timeout=15)
                final_url = resp.url
                if 'tb.cn' in final_url:
                    content = resp.read().decode('utf-8', errors='ignore')
                    target = _extract_target_from_short_page(content)
                    if target:
                        final_url = target
                return final_url
            except Exception:
                return url
        except Exception:
            return url
    return url


def extract_item_id(url):
    for pattern in [r'[?&]id=(\d+)', r'/i(\d+)\.htm', r'itemIds=(\d+)']:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def format_size(size_bytes):
    if not size_bytes:
        return "未知"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / 1024 / 1024:.2f} MB"


def download_file(url, filepath, progress_callback=None, referer=None, retries=3):
    """
    下载文件，带重试。
    返回 (success, error_msg)，error_msg 为 None 表示成功。
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    if referer:
        headers['Referer'] = referer
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                total = int(resp.headers.get('Content-Length', 0))
                downloaded = 0
                chunk_size = 65536
                with open(filepath, 'wb') as f:
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total > 0:
                            pct = downloaded * 100 // total
                            progress_callback(pct, downloaded, total)
            # 校验: 文件非空且是 mp4 (ftyp 头)
            if os.path.getsize(filepath) < 1024:
                last_err = f"文件过小({os.path.getsize(filepath)}B)，可能不是有效视频"
                continue
            with open(filepath, 'rb') as f:
                head = f.read(16)
            if b'ftyp' not in head:
                last_err = "文件头不含 ftyp，不是有效 MP4（可能返回了错误页）"
                continue
            return True, None
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    # 清理失败残留
    try:
        if os.path.exists(filepath) and os.path.getsize(filepath) < 1024:
            os.remove(filepath)
    except Exception:
        pass
    return False, last_err or "未知错误"


# === 浏览器复用（线程内单例，批量下载时避免每个链接重启浏览器） ===

_thread_state = threading.local()


def _new_browser_context(storage_state_path=None, headless=True):
    """创建新的 playwright 浏览器上下文"""
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=headless, args=[
        '--disable-blink-features=AutomationControlled',
        '--no-sandbox',
    ])
    kwargs = dict(
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        viewport={'width': 1920, 'height': 1080},
        locale='zh-CN',
    )
    if storage_state_path:
        kwargs['storage_state'] = str(storage_state_path)
    context = browser.new_context(**kwargs)
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    page = context.new_page()
    page.set_default_timeout(20000)
    return {'p': p, 'browser': browser, 'context': context, 'page': page,
            'net_ids': [], 'net_urls': [], 'net_hooked': False}


def _close_ctx(ctx):
    if not ctx:
        return
    try:
        ctx['browser'].close()
    except Exception:
        pass
    try:
        ctx['p'].stop()
    except Exception:
        pass


def get_shared_browser(storage_state_path=None, headless=True):
    """
    获取当前线程复用的浏览器上下文。
    一批下载只启动一次浏览器，显著降低风控命中率并提升速度。
    注意: headless 参数仅在首次创建时生效；已有复用实例时直接返回。
    """
    ctx = getattr(_thread_state, 'tb_ctx', None)
    if ctx:
        try:
            if ctx['browser'].is_connected() and not ctx['page'].is_closed():
                if ctx.get('headless') == headless:
                    return ctx
                # 需要切换有头/无头模式 → 重建
                _close_ctx(ctx)
                _thread_state.tb_ctx = None
        except Exception:
            _close_ctx(ctx)
            _thread_state.tb_ctx = None
    ctx = _new_browser_context(storage_state_path, headless=headless)
    ctx['headless'] = headless
    _thread_state.tb_ctx = ctx
    return ctx


def reset_shared_browser(storage_state_path=None, headless=True):
    """强制重建浏览器（提取失败后重试用）"""
    old = getattr(_thread_state, 'tb_ctx', None)
    _close_ctx(old)
    _thread_state.tb_ctx = None
    return get_shared_browser(storage_state_path, headless=headless)


def close_shared_browser():
    """公开接口：批量任务结束后释放浏览器资源"""
    old = getattr(_thread_state, 'tb_ctx', None)
    _close_ctx(old)
    _thread_state.tb_ctx = None


def _close_all_browsers_at_exit():
    try:
        close_shared_browser()
    except Exception:
        pass


atexit.register(_close_all_browsers_at_exit)


def _validate_taobao_session(cookies):
    """通过HTTP请求验证淘宝登录session是否真正有效"""
    try:
        cookie_str = '; '.join(f"{c['name']}={c['value']}" for c in cookies)
        # 使用需要登录的"我的淘宝"页面验证，session过期会302重定向到登录页
        req = urllib.request.Request(
            'https://i.taobao.com/my_taobao.htm',
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Cookie': cookie_str,
            }
        )
        resp = urllib.request.urlopen(req, timeout=10)
        final_url = resp.url
        # 如果被重定向到登录页，说明session已过期
        if 'login' in final_url.lower():
            return False
        return True
    except Exception:
        # 请求失败时无法确定，返回True让后续Playwright流程自行处理
        return True


def check_auth_file():
    if not AUTH_FILE.exists():
        return False, "未找到登录状态文件"
    try:
        with open(AUTH_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        cookies = data.get('cookies', [])
        if len(cookies) == 0:
            return False, "Cookie 为空"
        cookie_names = [c['name'] for c in cookies]
        has_login = any(name in cookie_names for name in ['_tb_token_', 'unb', 'cookie2', 'sgcookie'])
        if not has_login:
            return False, "缺少登录凭证"

        # 验证session是否真正有效
        if _validate_taobao_session(cookies):
            return True, f"登录状态正常（{len(cookies)} 个 cookie）"
        else:
            return False, "登录已过期，请重新登录淘宝"
    except Exception as e:
        return False, f"读取登录状态失败: {e}"


def login_and_save(progress_callback=None):
    if not HAS_PLAYWRIGHT:
        raise RuntimeError("未安装 Playwright，请运行: pip install playwright && playwright install chromium")

    from playwright.sync_api import sync_playwright

    def log(msg):
        if progress_callback:
            progress_callback(msg)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=[
            '--disable-blink-features=AutomationControlled',
            '--no-sandbox',
        ])
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
            locale='zh-CN',
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        page = context.new_page()

        log("正在打开淘宝登录页...")
        # 用JavaScript导航，不等待任何加载状态（避免天猫页面追踪脚本导致卡死）
        try:
            page.evaluate('window.location.href = "https://detail.tmall.com/item.htm?id=1062426563110"')
        except Exception:
            pass  # 导航后页面会卸载，evaluate抛异常是正常的

        log("浏览器已打开，请在页面中完成登录...")

        logged_in = False
        login_cookie_detected = False
        for i in range(300):
            time.sleep(1)
            try:
                current_url = page.url
                cookies = context.cookies()
                cookie_names = [c['name'] for c in cookies]
                has_login_cookie = any(name in cookie_names for name in ['_tb_token_', 'unb', 'cookie2', 'sgcookie'])
                is_on_product_page = 'tmall.com' in current_url.lower() and 'login' not in current_url.lower()
                is_on_login_page = 'login' in current_url.lower()

                # 阶段1: 检测到登录cookie出现
                if has_login_cookie and not login_cookie_detected:
                    login_cookie_detected = True
                    log("检测到登录cookie，等待登录态稳定...")

                # 阶段2: 登录cookie已出现，且页面已回到商品页
                if login_cookie_detected and is_on_product_page:
                    # 再等3秒让所有cookie完全写入
                    time.sleep(3)
                    # 重新获取cookie
                    cookies = context.cookies()
                    cookie_names = [c['name'] for c in cookies]
                    has_login_cookie = any(name in cookie_names for name in ['_tb_token_', 'unb', 'cookie2', 'sgcookie'])

                    if has_login_cookie:
                        # 验证: 用HTTP请求检查session是否真正有效
                        cookie_str = '; '.join(f"{c['name']}={c['value']}" for c in cookies)
                        try:
                            req = urllib.request.Request(
                                'https://i.taobao.com/my_taobao.htm',
                                headers={
                                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                                    'Cookie': cookie_str,
                                }
                            )
                            resp = urllib.request.urlopen(req, timeout=10)
                            if 'login' not in resp.url.lower():
                                logged_in = True
                                log("登录状态验证有效！")
                                break
                            else:
                                log("HTTP验证: session仍无效，继续等待...")
                                login_cookie_detected = False
                        except Exception:
                            # HTTP验证失败，但cookie已存在，仍然认为登录成功
                            logged_in = True
                            log("登录cookie已获取（HTTP验证跳过）")
                            break
                    else:
                        log("cookie未完全建立，继续等待...")
                        login_cookie_detected = False

                # 打印进度
                if i > 0 and i % 10 == 0:
                    if is_on_login_page:
                        log(f"等待登录中... ({i}秒)")
                    elif login_cookie_detected:
                        log(f"等待登录态稳定... ({i}秒)")
                    else:
                        log(f"等待中... ({i}秒)")

            except Exception:
                pass

        # 保存状态
        context.storage_state(path=str(AUTH_FILE))
        cookie_count = len(context.cookies())
        log(f"已保存 {cookie_count} 个cookie")
        browser.close()

        if logged_in:
            return True, "登录成功！状态已保存"
        else:
            return True, "已保存当前状态（如下载失败请重新登录）"

# === 淘宝商品页解析 ===

def _check_session_valid(page):
    """检查页面是否仍在登录状态"""
    url_lower = page.url.lower()
    if 'login' in url_lower:
        return False
    try:
        page_text = page.evaluate('() => document.body ? document.body.innerText.substring(0, 1000) : ""')
        if page_text and ('\u4eb2\uff0c\u8bf7\u767b\u5f55' in page_text or '\u626b\u7801\u767b\u5f55' in page_text):
            return False
    except:
        pass
    return True


def _extract_video_id_from_url(url):
    """从任意视频URL格式中提取videoId"""
    if not url:
        return None
    # 旧格式: cloud.video.taobao.com/play/u/0/p/1/e/6/t/1/1234567890.mp4
    m = re.search(r'play/u/\d+/p/\d+/e/\d+/t/\d+/(\d+)\.mp4', url)
    if m:
        return m.group(1)
    # 新格式: gw.alicdn.com/bao/uploaded//play/u/null/p/1/d/hd/e/6/t/1/1234567890.mp4
    m = re.search(r'play/u/null/p/\d+/d/\w+/e/\d+/t/\d+/(\d+)\.mp4', url)
    if m:
        return m.group(1)
    # 通用: 任意 play/u/.../{数字}.mp4
    m = re.search(r'play/u/[^/]+/[^"]*?/(\d{8,})\.mp4', url)
    if m:
        return m.group(1)
    return None


def _extract_video_from_context(page):
    """从 __ICE_APP_CONTEXT__ 提取主图视频（优先），同时获取直接下载URL"""
    try:
        result = page.evaluate('''() => {
            const ctx = window.__ICE_APP_CONTEXT__;
            if (!ctx || !ctx.loaderData || !ctx.loaderData.home) return null;
            const res = ctx.loaderData.home.data && ctx.loaderData.home.data.res;
            if (!res) return null;
            // 优先: item.videos (主图视频)
            const videos =
                (res.item && res.item.videos) ||
                (res.componentsVO && res.componentsVO.headImageVO && res.componentsVO.headImageVO.videos) ||
                [];
            if (videos.length > 0 && videos[0].videoId) {
                const v = videos[0];
                const info = {videoId: String(v.videoId), itemId: v.itemId || null, type: 'main'};
                // 同时获取直接下载URL（如果存在）
                if (v.url) info.url = v.url;
                return info;
            }
            return null;
        }''')
        return result
    except Exception:
        return None


def _extract_video_from_loader(page, item_id):
    """通过 __ICE_DATA_LOADER__ 提取主图视频"""
    if not item_id:
        return None
    try:
        result = page.evaluate('''async (itemId) => {
            if (!window.__ICE_DATA_LOADER__ || typeof window.__ICE_DATA_LOADER__.getData !== 'function') return null;
            try {
                const data = await window.__ICE_DATA_LOADER__.getData('home', { itemNumId: itemId });
                const res = data && data.res;
                if (!res) return null;
                const videos =
                    (res.item && res.item.videos) ||
                    (res.componentsVO && res.componentsVO.headImageVO && res.componentsVO.headImageVO.videos) ||
                    [];
                if (videos.length > 0 && videos[0].videoId) {
                    return {videoId: String(videos[0].videoId), itemId: videos[0].itemId || itemId, type: 'main'};
                }
            } catch(e) {}
            return null;
        }''', item_id)
        return result
    except Exception:
        return None


def _extract_video_from_html(page):
    """从页面HTML中提取视频ID，支持新旧URL格式"""
    try:
        html = page.content()
        # 搜索 videoId 字段
        m = re.search(r'"videoId"\s*:\s*"?(\d{10,})"?', html)
        if m:
            return {'videoId': m.group(1), 'itemId': None, 'type': 'main'}
        # 旧格式URL: cloud.video.taobao.com/play/u/0/p/1/e/6/t/1/xxx.mp4
        m = re.search(r'cloud\.video\.taobao\.com/play/u/\d+/p/\d+/e/\d+/t/\d+/(\d+)\.mp4', html)
        if m:
            return {'videoId': m.group(1), 'itemId': None, 'type': 'main'}
        # 新格式URL: play/u/null/p/1/d/hd/e/6/t/1/xxx.mp4 (评价视频)
        m = re.search(r'play/u/null/p/\d+/d/\w+/e/\d+/t/\d+/(\d+)\.mp4', html)
        if m:
            return {'videoId': m.group(1), 'itemId': None, 'type': 'review'}
    except Exception:
        pass
    return None


def _extract_video_from_rate_vo(page):
    """从评价区(rateVO)提取买家评价视频作为fallback"""
    try:
        result = page.evaluate('''() => {
            const ctx = window.__ICE_APP_CONTEXT__;
            if (!ctx || !ctx.loaderData || !ctx.loaderData.home) return null;
            const res = ctx.loaderData.home.data && ctx.loaderData.home.data.res;
            if (!res || !res.componentsVO || !res.componentsVO.rateVO) return null;
            const rateVO = res.componentsVO.rateVO;
            // rateVO.group.items[].media[].videoUrl
            if (rateVO.group && rateVO.group.items) {
                for (const item of rateVO.group.items) {
                    if (item.media && Array.isArray(item.media)) {
                        for (const media of item.media) {
                            const vurl = media.videoUrl || '';
                            if (vurl && vurl.includes('.mp4')) {
                                // 提取videoId: play/u/.../{数字}.mp4
                                const m = vurl.match(/(\\d{8,})\\.mp4/);
                                if (m) {
                                    return {videoId: m[1], itemId: null, type: 'review'};
                                }
                            }
                        }
                    }
                }
            }
            return null;
        }''')
        return result
    except Exception:
        return None


def _extract_video_from_all_vo(page):
    """深度搜索所有 componentsVO 子项中的视频URL"""
    try:
        result = page.evaluate('''() => {
            const ctx = window.__ICE_APP_CONTEXT__;
            if (!ctx || !ctx.loaderData || !ctx.loaderData.home) return null;
            const res = ctx.loaderData.home.data && ctx.loaderData.home.data.res;
            if (!res || !res.componentsVO) return null;
            const vo = res.componentsVO;
            // 遍历所有子VO，搜索包含 play/u/ 的视频URL
            for (const key of Object.keys(vo)) {
                if (key === 'rateVO') continue; // 评价视频单独处理
                try {
                    const str = JSON.stringify(vo[key]);
                    // 搜索 play/u/.../数字.mp4
                    const matches = str.match(/play\\/u\\/[^"]*?(\\d{8,})\\.mp4/g);
                    if (matches && matches.length > 0) {
                        const vidMatch = matches[0].match(/(\\d{8,})\\.mp4/);
                        if (vidMatch) {
                            return {videoId: vidMatch[1], itemId: null, type: key};
                        }
                    }
                } catch(e) {}
            }
            return null;
        }''')
        return result
    except Exception:
        return None


def _extract_video_from_video_tags(page):
    """从 video 标签提取视频信息，支持 CDN URL 和 play/u/ 格式"""
    try:
        result = page.evaluate('''() => {
            const videos = document.querySelectorAll('video');
            for (const v of videos) {
                const src = v.src || v.currentSrc || v.getAttribute('src') || '';
                if (!src) continue;
                // 匹配 cloudvideocdn.taobao.com URL: .../{videoId}_published_mp4_264_hd_taobao.mp4
                let m = src.match(/(\\d{10,})_published_mp4/);
                if (m) return {videoId: m[1], itemId: null, type: 'main', url: src};
                // 匹配 play/u/.../数字.mp4 格式
                m = src.match(/play\\/u\\/[^/]+\\/[^"]*?\\/(\\d{8,})\\.mp4/);
                if (m) return {videoId: m[1], itemId: null, type: 'main'};
                // 匹配通用 数字.mp4
                m = src.match(/(\\d{8,})\\.mp4/);
                if (m) return {videoId: m[1], itemId: null, type: 'main'};
            }
            return null;
        }''')
        return result
    except Exception:
        return None


def _check_video_triggered(page, network_video_ids=None, network_video_urls=None):
    """检查视频是否已被触发加载（video标签出现 或 CDN请求已发出）"""
    try:
        video = page.query_selector('video')
        if video:
            return True
    except Exception:
        pass
    if network_video_urls and len(network_video_urls) > 0:
        return True
    if network_video_ids and len(network_video_ids) > 0:
        return True
    return False


def _trigger_main_video(page, log_callback=None, network_video_ids=None, network_video_urls=None):
    """
    模拟用户鼠标移动到第一张主图，触发懒加载的主图视频。
    淘宝主图视频是懒加载机制: 只有鼠标移到第一张主图缩略图上时才创建video元素并请求CDN。
    """
    def log(msg):
        if log_callback:
            log_callback(msg)

    # 淘宝2025新版页面的主图缩略图选择器（按优先级排列）
    thumb_selectors = [
        '[class*="PicGallery"] [class*="thumbItem"]',
        '[class*="PicGallery"] [class*="ThumbItem"]',
        '[class*="picGallery"] [class*="thumb"]',
        '[class*="thumbItem"]:not([class*="video"])',
        '[class*="ThumbItem"]',
        '[class*="mainPic"]',
        '[class*="main-pic"]',
        '[class*="MainPic"]',
        '#J_ImgBooth',
        '.tb-thumb a',
        '#J_ThumbList li',
        '.pic-list li',
        '[class*="PicBooth"]',
        '[class*="picBooth"]',
    ]

    triggered = False

    for selector in thumb_selectors:
        try:
            elements = page.query_selector_all(selector)
            if not elements:
                continue

            element = elements[0]
            log(f"找到主图元素: {selector}（共{len(elements)}个），模拟鼠标悬停...")

            # 方式1: hover模拟鼠标移动到元素上
            element.hover(timeout=5000)
            time.sleep(1)

            if _check_video_triggered(page, network_video_ids, network_video_urls):
                triggered = True
                log("鼠标悬停成功触发视频加载！")
                break

            # 方式2: 点击元素
            element.click(timeout=3000)
            time.sleep(1)

            if _check_video_triggered(page, network_video_ids, network_video_urls):
                triggered = True
                log("点击主图成功触发视频加载！")
                break

        except Exception:
            continue

    # 注: 曾有的"按尺寸定位主图区域"和"遍历缩略图列表"两种兜底方式在实测中从未成功过
    # （主图视频是否存在由页面 SSR 数据决定，不依赖鼠标触发），已移除以缩短单条耗时。

    return triggered


def _hook_network_capture(ctx):
    """给共享 page 挂一次性的网络视频捕获（只挂一次，结果写入 ctx['net_ids']/['net_urls']）"""
    if ctx.get('net_hooked'):
        return
    page = ctx['page']

    def on_response(response):
        try:
            rurl = response.url
            net_ids = ctx['net_ids']
            net_urls = ctx['net_urls']
            # 1. 拦截视频CDN请求 (cloudvideocdn.taobao.com / cloud.video.taobao.com)
            if 'cloudvideocdn.taobao.com' in rurl or 'cloud.video.taobao.com' in rurl:
                if '.mp4' in rurl:
                    net_urls.append(rurl)
                    vid_match = re.search(r'(\d{10,})_published_mp4', rurl)
                    if not vid_match:
                        vid_match = re.search(r'/([0-9]{8,})\.mp4', rurl)
                    if not vid_match:
                        vid_match = re.search(r'play/u/[^/]+/[^"]*?/(\d{8,})\.mp4', rurl)
                    if vid_match and vid_match.group(1) not in net_ids:
                        net_ids.append(vid_match.group(1))
            # 2. 拦截API请求中的视频数据
            if any(k in rurl.lower() for k in ['mtop', 'h5api', 'acs.m']):
                body = response.text()
                if 'videoId' in body:
                    vid_match = re.search(r'"videoId"\s*:\s*"?(\d{10,})"?', body)
                    if vid_match and vid_match.group(1) not in net_ids:
                        net_ids.append(vid_match.group(1))
                if 'play/u/' in body:
                    for um in re.findall(r'play/u/[^"]*?(\d{8,})\.mp4', body):
                        if um not in net_ids:
                            net_ids.append(um)
        except Exception:
            pass

    page.on('response', on_response)
    ctx['net_hooked'] = True


def _extract_video_once(item_id, log, fresh=False, page_url=None, headless=True,
                        wait_for_human=False):
    """单次提取尝试。fresh=True 时强制重建浏览器。返回 (video_info, session_expired)"""
    ctx = (reset_shared_browser(AUTH_FILE, headless=headless) if fresh
           else get_shared_browser(AUTH_FILE, headless=headless))
    _hook_network_capture(ctx)
    page = ctx['page']

    # 清空上一次任务的网络捕获
    network_video_ids = ctx['net_ids']
    network_video_urls = ctx['net_urls']
    network_video_ids.clear()
    network_video_urls.clear()

    video_info = None
    session_expired = False

    tmall_url = page_url or f"https://detail.tmall.com/item.htm?id={item_id}"
    log(f"加载商品页: {tmall_url}")
    try:
        page.goto(tmall_url, wait_until='domcontentloaded', timeout=20000)
        time.sleep(2)

        if not _check_session_valid(page):
            session_expired = True
            log(f"登录会话已过期！当前URL: {page.url[:80]}")
            log("请重新登录淘宝后再试")
        else:
            page.evaluate('''() => {
                document.querySelectorAll('.baxia-dialog, .baxia-dialog-mask, [class*="baxia"]').forEach(d => d.remove());
            }''')

            # 等待 SSR 数据 (__ICE_APP_CONTEXT__) 就绪，最多 10 秒
            for _ in range(10):
                has_ctx = page.evaluate('''() => {
                    const ctx = window.__ICE_APP_CONTEXT__;
                    return !!(ctx && ctx.loaderData && ctx.loaderData.home &&
                              ctx.loaderData.home.data && ctx.loaderData.home.data.res);
                }''')
                if has_ctx:
                    break
                time.sleep(1)

            # 关键: 模拟鼠标移动到第一张主图，触发懒加载的主图视频
            _trigger_main_video(page, log_callback=log,
                               network_video_ids=network_video_ids,
                               network_video_urls=network_video_urls)

            # 优先1: 网络CDN拦截（反爬时最可靠，因为视频文件仍会被请求）
            if network_video_ids:
                video_info = {'videoId': network_video_ids[0], 'itemId': item_id, 'type': 'main'}
                if network_video_urls:
                    video_info['url'] = network_video_urls[0]
                log(f"从网络CDN请求中提取到视频 (ID: {network_video_ids[0]})")

            # 优先2: 页面上下文
            if not video_info:
                video_info = _extract_video_from_context(page)
                if video_info:
                    log("从页面上下文提取到主图视频")

            # 优先3: 数据加载器
            if not video_info:
                video_info = _extract_video_from_loader(page, item_id)
                if video_info:
                    log("通过数据加载器提取到主图视频")

            # 优先4: 搜索所有 componentsVO 子项
            if not video_info:
                video_info = _extract_video_from_all_vo(page)
                if video_info:
                    log(f"从 {video_info.get('type', 'unknown')} 提取到视频")

            # 优先5: video标签（反爬时video标签可能仍有src）
            if not video_info:
                video_info = _extract_video_from_video_tags(page)
                if video_info:
                    log("从video标签中提取到视频")

            # 优先6: HTML搜索
            if not video_info:
                video_info = _extract_video_from_html(page)
                if video_info:
                    vtype = "主图视频" if video_info.get('type') == 'main' else "评价视频"
                    log(f"从页面HTML中提取到{vtype}")

            # 优先7: API网络请求中的视频ID
            if not video_info and network_video_ids:
                video_info = {'videoId': network_video_ids[0], 'itemId': item_id, 'type': 'main'}
                log("从API网络请求中提取到视频")

            # Fallback: 评价视频
            if not video_info:
                video_info = _extract_video_from_rate_vo(page)
                if video_info:
                    log("未找到主图视频，提取到买家评价视频作为替代")

            # 降级页识别: 没拿到主图视频时，检查页面数据完整性
            # （被识别为爬虫时淘宝返回降级页: 无视频/主图不全/无评论）
            if not video_info or video_info.get('type') == 'review':
                try:
                    completeness = page.evaluate('''() => {
                        const ctx = window.__ICE_APP_CONTEXT__;
                        if (!ctx || !ctx.loaderData || !ctx.loaderData.home) return {noCtx: true};
                        const res = ctx.loaderData.home.data && ctx.loaderData.home.data.res;
                        if (!res) return {noRes: true};
                        const vo = res.componentsVO || {};
                        return {
                            imageCount: (res.item && res.item.images) ? res.item.images.length : 0,
                            hasRate: !!vo.rateVO,
                            voCount: Object.keys(vo).length,
                        };
                    }''')
                    if (completeness.get('noCtx') or completeness.get('noRes')
                            or completeness.get('imageCount', 0) < 5
                            or not completeness.get('hasRate')):
                        log(f"⚠ 页面数据不完整（主图{completeness.get('imageCount', '?')}张/"
                            f"评论{'有' if completeness.get('hasRate') else '无'}），"
                            f"疑似被反爬降级，视频字段被服务端剥离")
                except Exception:
                    pass

    except Exception as e:
        log(f"页面加载失败: {e}")

    # 有头模式 + 未拿到主图视频 → 等待人工完成滑块验证（最多90秒）
    # 降级页/滑块页由真人在弹出的窗口里完成后，页面会自动跳回商品页且数据恢复完整
    need_main = not video_info or video_info.get('type') == 'review'
    if wait_for_human and need_main and not session_expired:
        log("=" * 40)
        log("如果弹出的浏览器窗口中出现滑块/验证码，请手动完成验证")
        log("工具会等待 90 秒，完成后自动继续提取...")
        log("=" * 40)
        for waited in range(0, 90, 3):
            time.sleep(3)
            try:
                # 验证完成后页面通常会跳回商品页，检查上下文中是否出现视频
                found = page.evaluate('''() => {
                    const ctx = window.__ICE_APP_CONTEXT__;
                    if (!ctx || !ctx.loaderData || !ctx.loaderData.home) return false;
                    const res = ctx.loaderData.home.data && ctx.loaderData.home.data.res;
                    if (!res) return false;
                    if (res.item && res.item.videos && res.item.videos.length) return true;
                    const h = res.componentsVO && res.componentsVO.headImageVO;
                    if (h && h.videos && h.videos.length) return true;
                    return false;
                }''')
                if found:
                    log(f"检测到验证已通过（等待{waited+3}秒），重新提取主图视频...")
                    video_info = (_extract_video_from_context(page)
                                  or _extract_video_from_loader(page, item_id)
                                  or _extract_video_from_all_vo(page))
                    if video_info:
                        video_info['type'] = 'main'
                        log("人工验证后成功拿到主图视频！")
                    break
            except Exception:
                # 页面可能正在跳转，继续等
                pass
        else:
            log("等待超时，未检测到验证完成")

    # PC 页没找到视频，或只找到评价视频 → 再试移动版页面找主图视频（评价视频留作兜底）
    need_h5 = (not session_expired) and (not video_info or video_info.get('type') == 'review')
    if need_h5:
        backup_review = video_info if video_info and video_info.get('type') == 'review' else None
        h5_url = f"https://h5.m.taobao.com/awp/core/detail.htm?id={item_id}"
        log("尝试移动版页面...")
        try:
            page.goto(h5_url, wait_until='domcontentloaded', timeout=15000)
            time.sleep(5)

            h5_info = None
            if network_video_ids:
                h5_info = {'videoId': network_video_ids[-1], 'itemId': item_id, 'type': 'main'}
                if network_video_urls:
                    h5_info['url'] = network_video_urls[-1]
                log("从移动版网络请求中提取到视频")

            if not h5_info:
                h5_info = _extract_video_from_html(page)
                if h5_info:
                    log("从移动版页面HTML中提取到视频")

            if not h5_info:
                h5_info = _extract_video_from_video_tags(page)
                if h5_info:
                    log("从移动版video标签中提取到视频")

            # 移动版找到的若是主图视频则采用；评价视频则保留原兜底
            if h5_info and h5_info.get('type') == 'main':
                video_info = h5_info
            elif not video_info:
                video_info = h5_info

        except Exception as e:
            log(f"移动版页面加载失败: {e}")

        if not video_info and backup_review:
            video_info = backup_review

    return video_info, session_expired


def extract_video_info(item_id, log_callback=None, page_url=None):
    """提取视频信息，优先主图视频，fallback到评价视频。失败自动重建浏览器重试一次"""
    if not HAS_PLAYWRIGHT:
        return None, "未安装 Playwright"

    valid, msg = check_auth_file()
    if not valid:
        return None, msg

    def log(msg):
        if log_callback:
            log_callback(msg)

    log("验证登录状态...")

    video_info, session_expired = _extract_video_once(item_id, log, fresh=False, page_url=page_url)

    # 没找到视频、或只找到评价视频 → 重试。
    # 淘宝会对疑似爬虫返回「降级页」（无视频/主图不全/无评论），此时无头浏览器拿到的
    # SSR 数据里根本没有视频字段。重试换成【有头浏览器】（会短暂弹出窗口），
    # 有头模式的指纹更接近真人，实测可通过大部分降级判定。
    need_retry = (not session_expired) and (not video_info or video_info.get('type') == 'review')
    if need_retry:
        log("未拿到主图视频，可能触发了反爬降级页，启用有头浏览器重试（会短暂弹出窗口）...")
        try:
            retry_info, session_expired = _extract_video_once(
                item_id, log, fresh=True, page_url=page_url, headless=False,
                wait_for_human=True)
            # 重试拿到主图视频则替换；否则保留原来的（含评价视频兜底）
            if retry_info and retry_info.get('type') == 'main':
                video_info = retry_info
                log("有头模式成功拿到主图视频！")
            elif not video_info:
                video_info = retry_info
        except Exception as e:
            log(f"有头重试异常: {e}")
        finally:
            # 恢复无头共享实例，避免后续条目沿用有头窗口
            try:
                reset_shared_browser(AUTH_FILE, headless=True)
            except Exception:
                pass

    if session_expired:
        return None, "登录会话已过期，请重新登录"
    if not video_info:
        return None, "未找到视频，该商品可能没有视频（部分商品仅APP端有主图视频）"
    return video_info, None


# === 抖音商品页解析 ===

def _extract_douyin_video_from_api(page):
    """从抖音页面API响应中提取视频URL (通过拦截网络请求)"""
    captured_video_url = []

    def on_response(response):
        try:
            rurl = response.url
            if 'promotion/pack' in rurl and 'h5' in rurl:
                body = response.text()
                data = json.loads(body)
                promotion = data.get('promotion_h5', {})
                head_figure = promotion.get('head_figure_data', {})
                media_list = head_figure.get('media_list', [])
                for media in media_list:
                    if media.get('type') == 'video':
                        content_list = media.get('content_list', [])
                        for content in content_list:
                            video_url = content.get('url', '')
                            if video_url and 'douyinvod.com' in video_url:
                                captured_video_url.append(video_url)
                                break
        except:
            pass

    page.on('response', on_response)
    return captured_video_url


def _extract_douyin_video_from_tag(page):
    """从抖音页面的 video 标签中提取视频URL"""
    try:
        result = page.evaluate('''() => {
            const videos = document.querySelectorAll('video');
            for (const v of videos) {
                const src = v.src || v.currentSrc || v.getAttribute('src') || '';
                if (src && src.includes('douyinvod.com')) {
                    return src;
                }
            }
            return null;
        }''')
        return result
    except Exception:
        return None


def extract_douyin_video(url, log_callback=None):
    """提取抖音商品视频URL"""
    if not HAS_PLAYWRIGHT:
        return None, "未安装 Playwright"

    def log(msg):
        if log_callback:
            log_callback(msg)

    video_url = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        page = context.new_page()
        page.set_default_timeout(30000)

        captured_urls = []
        def on_response(response):
            try:
                rurl = response.url
                if 'promotion/pack' in rurl and 'h5' in rurl:
                    body = response.text()
                    data = json.loads(body)
                    promotion = data.get('promotion_h5', {})
                    head_figure = promotion.get('head_figure_data', {})
                    media_list = head_figure.get('media_list', [])
                    for media in media_list:
                        if media.get('type') == 'video':
                            content_list = media.get('content_list', [])
                            for content in content_list:
                                vurl = content.get('url', '')
                                if vurl:
                                    captured_urls.append(vurl)
                                    break
            except:
                pass

        page.on('response', on_response)

        log(f"加载抖音商品页: {url[:80]}")
        try:
            page.goto(url, wait_until='networkidle', timeout=30000)
            time.sleep(3)

            # 方法1: 从API响应中提取
            if captured_urls:
                video_url = captured_urls[0]
                log("从API响应中提取到视频URL")

            # 方法2: 点击视频元素后从 video 标签提取
            if not video_url:
                log("尝试触发视频加载...")
                try:
                    page.evaluate('''() => {
                        const el = document.querySelector('[class*="video"]') || document.querySelector('video');
                        if (el) el.click();
                    }''')
                    time.sleep(3)

                    tag_url = _extract_douyin_video_from_tag(page)
                    if tag_url:
                        video_url = tag_url
                        log("从video标签中提取到视频URL")
                except:
                    pass

            # 方法3: 从页面HTML中搜索
            if not video_url:
                html = page.content()
                m = re.search(r'(https?://[^"\s\\]+douyinvod\.com[^"\s\\]+)', html)
                if m:
                    video_url = m.group(1).replace('\\/', '/')
                    log("从页面HTML中提取到视频URL")

        except Exception as e:
            log(f"页面加载失败: {e}")

        browser.close()

    if not video_url:
        return None, "未找到视频，该商品可能没有视频"
    return video_url, None


# === 下载入口函数 ===

def _download_direct_taobao(url, output_dir, log_callback=None, progress_callback=None):
    """处理淘宝视频直链: 直接提取contentId并下载"""
    def log(msg):
        if log_callback:
            log_callback(msg)

    video_id = extract_direct_video_id(url)
    if not video_id:
        return False, "无法从直链中提取视频ID"

    log(f"链接类型: 淘宝视频直链")
    log(f"contentId: {video_id}")

    download_url = GENERIC_URL.replace('{vid}', video_id)
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, f"taobao_video_{video_id}.mp4")

    log(f"开始下载...")
    success, err = download_file(download_url, filepath, progress_callback=progress_callback)
    if success:
        size = os.path.getsize(filepath)
        log(f"下载完成! 大小: {format_size(size)}")
        return True, filepath
    else:
        return False, f"下载失败: {err}"


def _download_taobao_product(url, output_dir, log_callback=None, progress_callback=None):
    """处理淘宝/天猫商品链接: 解析页面提取videoId后下载"""
    def log(msg):
        if log_callback:
            log_callback(msg)

    final_url = resolve_short_url(url)
    item_id = extract_item_id(final_url)
    if item_id:
        log(f"链接类型: 淘宝商品页")
        log(f"商品ID: {item_id}")
    else:
        return False, "无法解析商品ID"

    # 淘宝集市店商品用 item.taobao.com 打开（天猫 URL 对集市商品会跳错误页）
    if 'taobao.com' in final_url and 'tmall' not in final_url:
        page_url = f"https://item.taobao.com/item.htm?id={item_id}"
    else:
        page_url = f"https://detail.tmall.com/item.htm?id={item_id}"

    log("提取视频信息...")
    video_info, err = extract_video_info(item_id, log_callback=log, page_url=page_url)
    if err:
        return False, err

    video_id = video_info['videoId']
    video_type = video_info.get('type', 'main')
    direct_url = video_info.get('url')  # 可能从页面数据或网络CDN获取到直接URL
    log(f"视频ID: {video_id}")

    if video_type == 'review':
        log("提示: 该商品未找到主图视频，已提取买家评价视频")

    # 候选下载URL: 优先页面/网络拦截到的真实CDN直链，通用模板兜底
    candidate_urls = []
    if direct_url and direct_url.startswith('http') and '.mp4' in direct_url:
        candidate_urls.append(direct_url)
        log(f"使用直接获取的视频URL")
    generic_url = GENERIC_URL.replace('{vid}', video_id)
    if generic_url not in candidate_urls:
        candidate_urls.append(generic_url)
    os.makedirs(output_dir, exist_ok=True)

    # 评价视频用不同文件名前缀
    if video_type == 'review':
        filepath = os.path.join(output_dir, f"taobao_review_{item_id}.mp4")
    else:
        filepath = os.path.join(output_dir, f"taobao_video_{item_id}.mp4")

    log(f"开始下载...")
    last_err = None
    for i, download_url in enumerate(candidate_urls):
        if i > 0:
            log(f"换用备选下载地址重试...")
        success, err = download_file(download_url, filepath, progress_callback=progress_callback)
        if success:
            size = os.path.getsize(filepath)
            log(f"下载完成! 大小: {format_size(size)}")
            return True, filepath
        last_err = err
        log(f"该地址下载失败: {err}")
    return False, f"下载失败: {last_err}"


def _download_douyin(url, output_dir, log_callback=None, progress_callback=None):
    """处理抖音商品链接: 解析页面提取视频URL后下载"""
    def log(msg):
        if log_callback:
            log_callback(msg)

    log(f"链接类型: 抖音商品")

    log("提取视频信息...")
    video_url, err = extract_douyin_video(url, log_callback=log)
    if err:
        return False, err

    log(f"视频URL: {video_url[:80]}...")

    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, f"douyin_video_{int(time.time())}.mp4")

    log(f"开始下载...")
    success, err = download_file(video_url, filepath, progress_callback=progress_callback,
                           referer='https://haohuo.jinritemai.com/')
    if success:
        size = os.path.getsize(filepath)
        log(f"下载完成! 大小: {format_size(size)}")
        return True, filepath
    else:
        return False, f"下载失败: {err}"


def download_video(url, output_dir, log_callback=None, progress_callback=None):
    """
    综合视频下载入口
    自动识别链接类型并选择对应下载方式
    """
    def log(msg):
        if log_callback:
            log_callback(msg)

    link_type = detect_link_type(url)

    if link_type == LINK_TYPE_TAOBAO_DIRECT:
        return _download_direct_taobao(url, output_dir, log_callback, progress_callback)
    elif link_type == LINK_TYPE_TAOBAO_PRODUCT:
        return _download_taobao_product(url, output_dir, log_callback, progress_callback)
    elif link_type == LINK_TYPE_DOUYIN:
        return _download_douyin(url, output_dir, log_callback, progress_callback)
    else:
        log(f"未知链接类型: {url[:80]}")
        return False, "不支持的链接类型，目前支持淘宝/天猫商品链接、淘宝视频直链和抖音商品链接"
