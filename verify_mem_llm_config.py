"""验证 Mem LLM 配置统一修复 (扩展版).

覆盖:
  1. memai.model_config.resolve_mem_llm_client() 是唯一凭据解析点
  2. memory_service.py 6 处 LLM 调用
  3. endogenous_drive.py (监督者) LLM 调用
  4. tier1_to_tier2_bridge.py (Tier1→Tier2) LLM 调用
  5. CLI save_provider_config 同步写 memory.llm.*
"""
import os
import re
import sys
import tempfile

sys.path.insert(0, r'f:\My_code\Traecode\VoidCube')
# memai package lives at Mem/src/memai — add Mem/src so we can import it
sys.path.insert(0, r'f:\My_code\Traecode\VoidCube\Mem\src')


def _check(name: str, ok: bool, detail: str = '') -> bool:
    mark = 'PASS' if ok else 'FAIL'
    print(f'  [{mark}] {name}' + (f' — {detail}' if detail else ''))
    return ok


def test_shared_resolver_exists():
    """memai.model_config.resolve_mem_llm_client 必须存在"""
    print('TEST 1: memai.model_config.resolve_mem_llm_client 共享函数存在')
    src = open(
        r'f:\My_code\Traecode\VoidCube\Mem\src\memai\model_config.py',
        encoding='utf-8',
    ).read()
    has_fn = bool(
        re.search(r'def resolve_mem_llm_client\(role: str = "default"\):', src)
    )
    return _check('resolve_mem_llm_client(role: str = "default") 存在', has_fn)


def test_resolver_logic():
    """共享 resolver 必须有正确的优先级和 fallback 逻辑"""
    print()
    print('TEST 2: 共享 resolver 逻辑')
    src = open(
        r'f:\My_code\Traecode\VoidCube\Mem\src\memai\model_config.py',
        encoding='utf-8',
    ).read()
    m = re.search(
        r'def resolve_mem_llm_client\(role: str = "default"\):.*?(?=\n\ndef )',
        src, re.S,
    )
    if not m:
        return _check('函数体可定位', False)
    body = m.group(0)

    checks = [
        ('调用 load_voidcube_mem_model_config_set',
         'load_voidcube_mem_model_config_set' in body),
        ('通过 mem_cfg.api_key_env 取 key',
         'mem_cfg.api_key_env' in body),
        ('通过 mem_cfg.base_url 取 URL',
         'mem_cfg.base_url' in body),
        ('通过 mem_cfg.model 取模型',
         'mem_cfg.model' in body),
        ('回退到 DEEPSEEK_API_KEY / OPENAI_API_KEY',
         'DEEPSEEK_API_KEY' in body and 'OPENAI_API_KEY' in body),
        ('回退到 MEMAI_LLM_BASE_URL',
         'MEMAI_LLM_BASE_URL' in body),
        ('无 key 时返回 (None, model)',
         'return None' in body),
        ('返回 (client, model) 元组',
         'return client, model' in body),
    ]
    all_ok = True
    for name, ok in checks:
        all_ok &= _check(name, ok)
    return all_ok


def test_resolver_runtime():
    """运行时 import + 解析路径"""
    print()
    print('TEST 3: 运行时 resolver 行为')
    try:
        from memai.model_config import resolve_mem_llm_client
        _check('导入 resolve_mem_llm_client', True)
    except Exception as exc:
        return _check(f'导入 resolve_mem_llm_client — {exc}', False)

    # 清空 env, 用真实 config
    for k in ('DEEPSEEK_API_KEY', 'OPENAI_API_KEY', 'MEMAI_LLM_BASE_URL', 'MEMAI_LLM_MODEL'):
        os.environ.pop(k, None)

    client, model = resolve_mem_llm_client(role='default')
    _check(
        '无 env 时 client=None',
        client is None,
        detail=f'client={client}, model={model!r}' if client is not None else f'model={model!r} (从 memory.llm.* 读出)',
    )

    # 用 fake env 测 happy path. 当 voidcube config 加载失败时, resolver
    # 会回退到 MemModelConfig() 默认值 (api_key_env="OPENAI_API_KEY"),
    # 所以这里要设 OPENAI_API_KEY 让 happy path 走通.
    os.environ['OPENAI_API_KEY'] = 'fake-key'
    client2, model2 = resolve_mem_llm_client(role='default')
    return _check(
        '有 env 时 client 不为 None',
        client2 is not None,
        detail=f'client type: {type(client2).__name__}, model={model2!r}',
    )


def test_no_direct_env_reads():
    """在 3 个被改文件中，helper 之外没有直接的 DEEPSEEK/OPENAI/MEMAI env 读取"""
    print()
    print('TEST 4: 3 个文件内 helper 之外无直接 env 读取')

    files = [
        (
            r'f:\My_code\Traecode\VoidCube\systems\memory\memory_service.py',
            r'def _resolve_mem_llm_client\(self\):.*?(?=\n    async def _app_lifespan|\n    def [a-z])',
        ),
        (
            r'f:\My_code\Traecode\VoidCube\systems\supervisor\endogenous_drive.py',
            r'def _llm_generate_learning_topics\(.*?(?=\n    def [a-zA-Z_])',
        ),
        (
            r'f:\My_code\Traecode\VoidCube\systems\memory\tier1_to_tier2_bridge.py',
            r'def _build_pipeline\(self\):.*?(?=\n    def [a-zA-Z_])',
        ),
    ]
    all_ok = True
    for path, helper_pattern in files:
        src = open(path, encoding='utf-8').read()
        m = re.search(helper_pattern, src, re.S)
        if not m:
            all_ok &= _check(f'{path}: helper 主体可定位', False)
            continue
        helper_body = m.group(0)
        rest = src.replace(helper_body, '')
        bad = re.findall(
            r'os\.environ\.get\(\s*[\'"](?:DEEPSEEK_API_KEY|OPENAI_API_KEY|MEMAI_LLM_)',
            rest,
        )
        all_ok &= _check(
            f'{os.path.basename(path)}: helper 外无 env 直读',
            len(bad) == 0,
            detail=f'残留 {len(bad)} 处' if bad else '',
        )
    return all_ok


def test_resolver_callers():
    """3 个文件必须都 import resolve_mem_llm_client"""
    print()
    print('TEST 5: 3 个文件都 import 并使用 resolve_mem_llm_client')

    files = [
        r'f:\My_code\Traecode\VoidCube\systems\memory\memory_service.py',
        r'f:\My_code\Traecode\VoidCube\systems\supervisor\endogenous_drive.py',
        r'f:\My_code\Traecode\VoidCube\systems\memory\tier1_to_tier2_bridge.py',
    ]
    all_ok = True
    for path in files:
        src = open(path, encoding='utf-8').read()
        imports_resolver = (
            'from memai.model_config import resolve_mem_llm_client' in src
        )
        calls_resolver = 'resolve_mem_llm_client(' in src
        all_ok &= _check(
            f'{os.path.basename(path)}: import + 调用',
            imports_resolver and calls_resolver,
        )
    return all_ok


def test_cli_mirror():
    """CLI save_provider_config 必须同步 memory.llm.*"""
    print()
    print('TEST 6: CLI save_provider_config 同步到 memory.llm.*')
    src = open(
        r'f:\My_code\Traecode\VoidCube\VoidCube_cli\api_config.py',
        encoding='utf-8',
    ).read()
    start = src.find('def save_provider_config(')
    end = src.find('def test_api_connection', start)
    func = src[start:end]

    checks = [
        ('有 also_apply_to_memory 参数',
         'also_apply_to_memory' in func),
        ('写到 memory.llm.provider',
         'memory.llm.provider' in func),
        ('写到 memory.llm.model',
         'memory.llm.model' in func),
        ('写到 memory.llm.base_url',
         'memory.llm.base_url' in func),
        ('写到 memory.llm.api_key_env',
         'memory.llm.api_key_env' in func),
    ]
    all_ok = True
    for name, ok in checks:
        all_ok &= _check(name, ok)
    return all_ok


def test_six_sites_unified():
    """memory_service.py 内 6 个 LLM 调用点都走 _resolve_mem_llm_client"""
    print()
    print('TEST 7: memory_service.py 6 个 LLM 调用点')
    src = open(
        r'f:\My_code\Traecode\VoidCube\systems\memory\memory_service.py',
        encoding='utf-8',
    ).read()
    sites = [
        ('_llm_escalate_summary',  'escalate'),
        ('_llm_purge_review',      'purge_review'),
        ('_check_llm_health',      'health_check'),
        ('_build_compression_pipeline', 'build_pipeline'),
        ('_call_llm_for_summary',  'summarize'),
        ('_generate_embedding',    'embedding'),
    ]
    all_ok = True
    for func_name, label in sites:
        m = re.search(rf'    (?:async )?def {func_name}\(', src)
        if not m:
            all_ok &= _check(f'{label}: 函数未找到', False)
            continue
        start = m.start()
        rest = src[m.end():]
        nxt = re.search(r'\n    (?:async )?def |\nclass ', rest)
        body = rest[: nxt.start() if nxt else len(rest)]
        uses_helper = '_resolve_mem_llm_client' in body
        all_ok &= _check(f'{label}: 走 _resolve_mem_llm_client', uses_helper)
    return all_ok


def main() -> int:
    results = [
        test_shared_resolver_exists(),
        test_resolver_logic(),
        test_resolver_runtime(),
        test_no_direct_env_reads(),
        test_resolver_callers(),
        test_cli_mirror(),
        test_six_sites_unified(),
    ]
    print()
    print('=' * 60)
    if all(results):
        print('所有验证通过！')
        return 0
    print(f'失败: {sum(1 for r in results if not r)}/{len(results)} 项')
    return 1


if __name__ == '__main__':
    sys.exit(main())
