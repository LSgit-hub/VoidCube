#!/usr/bin/env python3
"""
本地网络搜索工具 - 无需云服务

替代 Exa/Parallel/Firecrawl 等云搜索服务，使用本地爬虫实现。

功能:
- DuckDuckGo 搜索 (无需 API Key)
- 直接网页抓取
- 内容提取和摘要
- 支持多语言

使用:
    from tools.web_tools_local import local_web_search, local_web_extract
    
    # 搜索
    results = local_web_search("Python 教程", limit=5)
    
    # 提取网页内容
    content = local_web_extract("https://example.com")
"""

import logging
import re
import time
from typing import List, Dict, Any, Optional
from urllib.parse import quote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

DEFAULT_TIMEOUT = 30.0
MAX_CONTENT_LENGTH = 50000


def _clean_text(text: str) -> str:
    """清理文本：移除多余空白和特殊字符"""
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', text)
    return text.strip()


def _is_valid_url(url: str) -> bool:
    """检查 URL 是否有效"""
    try:
        result = urlparse(url)
        return all([result.scheme in ('http', 'https'), result.netloc])
    except Exception:
        return False


def _extract_main_content(soup: BeautifulSoup, url: str) -> str:
    """提取网页主要内容"""
    for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'advertisement']):
        tag.decompose()
    
    main_content = None
    
    for selector in ['article', 'main', '[role="main"]', '.content', '.post-content', '.article-content', '#content']:
        main_content = soup.select_one(selector)
        if main_content:
            break
    
    if not main_content:
        main_content = soup.find('body') or soup
    
    for tag in main_content.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
        tag.string = f"\n## {tag.get_text(strip=True)}\n"
    
    text = main_content.get_text(separator='\n', strip=True)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return _clean_text(text)[:MAX_CONTENT_LENGTH]


def _extract_metadata(soup: BeautifulSoup, url: str) -> Dict[str, str]:
    """提取网页元数据"""
    metadata = {'url': url}
    
    title = soup.find('title')
    if title:
        metadata['title'] = _clean_text(title.get_text())
    
    for meta in soup.find_all('meta'):
        name = meta.get('name') or meta.get('property', '')
        content = meta.get('content', '')
        
        if name in ('description', 'og:description') and content:
            metadata['description'] = _clean_text(content)
        elif name in ('keywords',) and content:
            metadata['keywords'] = _clean_text(content)
        elif name in ('author', 'article:author') and content:
            metadata['author'] = _clean_text(content)
    
    return metadata


def _request_html(url: str, timeout: float) -> str:
    """Fetch HTML with browser-like headers."""
    headers = {
        'User-Agent': USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.text


def _search_duckduckgo(
    query: str,
    limit: int,
    region: str,
    timeout: float,
) -> List[Dict[str, Any]]:
    """Search via DuckDuckGo HTML."""
    search_url = f"https://duckduckgo.com/html/?q={quote(query)}&kl={region}"
    soup = BeautifulSoup(_request_html(search_url, timeout), 'lxml')

    results: List[Dict[str, Any]] = []
    for result in soup.select('.result')[:limit * 2]:
        title_elem = result.select_one('.result__a')
        if not title_elem:
            continue

        url = title_elem.get('href', '')
        title = title_elem.get_text(strip=True)
        if not url or not title or 'duckduckgo.com' in url:
            continue

        desc_elem = result.select_one('.result__snippet')
        description = desc_elem.get_text(strip=True) if desc_elem else ""

        results.append({
            'title': _clean_text(title),
            'url': url,
            'description': _clean_text(description),
        })
        if len(results) >= limit:
            break

    return results


def _search_bing(
    query: str,
    limit: int,
    timeout: float,
) -> List[Dict[str, Any]]:
    """Search via Bing HTML."""
    search_url = f"https://www.bing.com/search?q={quote(query)}"
    soup = BeautifulSoup(_request_html(search_url, timeout), 'lxml')

    results: List[Dict[str, Any]] = []
    for item in soup.select('li.b_algo')[:limit * 3]:
        title_elem = item.select_one('h2 a')
        if not title_elem:
            continue

        url = title_elem.get('href', '')
        title = title_elem.get_text(" ", strip=True)
        if not url or not title:
            continue

        desc_elem = item.select_one('.b_caption p') or item.select_one('p')
        description = desc_elem.get_text(" ", strip=True) if desc_elem else ""

        results.append({
            'title': _clean_text(title),
            'url': url,
            'description': _clean_text(description),
        })
        if len(results) >= limit:
            break

    return results


def local_web_search(
    query: str,
    limit: int = 5,
    region: str = "cn-CN",
    timeout: float = DEFAULT_TIMEOUT,
) -> List[Dict[str, Any]]:
    """
    使用本地公开搜索引擎进行网络搜索 (无需 API Key)
    
    Args:
        query: 搜索关键词
        limit: 返回结果数量
        region: 地区设置 (cn-CN 为中文)
        timeout: 请求超时时间
    
    Returns:
        搜索结果列表，每项包含 title, url, description
    """
    if not query or not query.strip():
        return []
    
    providers = (
        ("duckduckgo", lambda: _search_duckduckgo(query, limit, region, timeout)),
        ("bing", lambda: _search_bing(query, limit, timeout)),
    )

    last_error: Optional[Exception] = None
    for provider_name, provider in providers:
        try:
            results = provider()
            if results:
                logger.info("本地搜索 '%s' 通过 %s 返回 %d 条结果", query, provider_name, len(results))
                return results[:limit]
            logger.warning("本地搜索 '%s' 通过 %s 返回 0 条结果", query, provider_name)
        except Exception as e:
            last_error = e
            logger.warning("本地搜索 provider %s 失败: %s", provider_name, e)

    if last_error is not None:
        logger.error("本地搜索最终失败: %s", last_error)
    return []


def local_web_extract(
    url: str,
    timeout: float = DEFAULT_TIMEOUT,
    extract_links: bool = False,
) -> Dict[str, Any]:
    """
    提取网页内容
    
    Args:
        url: 网页 URL
        timeout: 请求超时时间
        extract_links: 是否提取链接
    
    Returns:
        包含 title, content, metadata 的字典
    """
    if not _is_valid_url(url):
        return {'error': 'Invalid URL', 'url': url}
    
    try:
        headers = {
            'User-Agent': USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }
        
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            
            soup = BeautifulSoup(resp.text, 'lxml')
            
            metadata = _extract_metadata(soup, url)
            content = _extract_main_content(soup, url)
            
            result = {
                'url': url,
                'title': metadata.get('title', ''),
                'content': content,
                'metadata': metadata,
                'success': True,
            }
            
            if extract_links:
                links = []
                for link in soup.find_all('a', href=True):
                    href = link['href']
                    if href.startswith('http'):
                        links.append({
                            'url': urljoin(url, href),
                            'text': link.get_text(strip=True)[:100],
                        })
                result['links'] = links[:50]
            
            logger.info(f"成功提取 {url}，内容长度: {len(content)}")
            return result
            
    except httpx.TimeoutException:
        logger.error(f"请求超时: {url}")
        return {'error': 'Timeout', 'url': url, 'success': False}
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP 错误: {e}")
        return {'error': f'HTTP {e.response.status_code}', 'url': url, 'success': False}
    except Exception as e:
        logger.error(f"提取失败: {url}, 错误: {e}")
        return {'error': str(e), 'url': url, 'success': False}


def local_web_crawl(
    url: str,
    max_depth: int = 1,
    max_pages: int = 10,
    timeout: float = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    爬取网站多个页面
    
    Args:
        url: 起始 URL
        max_depth: 最大深度
        max_pages: 最大页面数
        timeout: 请求超时时间
    
    Returns:
        包含所有页面内容的字典
    """
    if not _is_valid_url(url):
        return {'error': 'Invalid URL', 'url': url}
    
    base_url = urlparse(url)
    visited = set()
    pages: list = []
    queue = [(url, 0)]
    
    while queue and len(pages) < max_pages:
        current_url, depth = queue.pop(0)
        
        if current_url in visited or depth > max_depth:
            continue
        
        visited.add(current_url)
        
        page = local_web_extract(current_url, timeout, extract_links=(depth < max_depth))
        
        if page.get('success'):
            pages.append(page)
            
            if depth < max_depth and 'links' in page:
                for link in page['links'][:20]:
                    link_url = link['url']
                    link_parsed = urlparse(link_url)
                    
                    if link_parsed.netloc == base_url.netloc:
                        queue.append((link_url, depth + 1))
        
        time.sleep(0.5)
    
    return {
        'url': url,
        'pages': pages,
        'total': len(pages),
        'success': True,
    }


def local_web_search_and_extract(
    query: str,
    limit: int = 3,
    timeout: float = DEFAULT_TIMEOUT,
) -> List[Dict[str, Any]]:
    """
    搜索并提取内容 (组合操作)
    
    Args:
        query: 搜索关键词
        limit: 结果数量
        timeout: 请求超时时间
    
    Returns:
        包含搜索结果和页面内容的列表
    """
    search_results = local_web_search(query, limit=limit, timeout=timeout)
    
    enriched_results = []
    for result in search_results:
        url = result.get('url', '')
        if url:
            page = local_web_extract(url, timeout=timeout)
            result['content'] = page.get('content', '')[:2000]
        enriched_results.append(result)
    
    return enriched_results


if __name__ == "__main__":
    print("=== 本地网络搜索测试 ===\n")
    
    results = local_web_search("Python 教程", limit=3)
    print(f"搜索结果: {len(results)} 条\n")
    
    for i, r in enumerate(results, 1):
        print(f"{i}. {r['title']}")
        print(f"   URL: {r['url']}")
        print(f"   描述: {r['description'][:100]}...\n")
    
    if results:
        print("=== 提取第一个页面 ===\n")
        page = local_web_extract(results[0]['url'])
        print(f"标题: {page.get('title', '')}")
        print(f"内容长度: {len(page.get('content', ''))}")
        print(f"内容预览:\n{page.get('content', '')[:500]}...")
