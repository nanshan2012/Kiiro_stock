"""
离线测试:用 MockProvider 端到端验证流水线,无需 API Key / 网络。
运行:  python -m pytest -q   或   python tests/test_cross_validate.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.cross_validate import (  # noqa: E402
    BudgetExceededError,
    Engine,
    ModelSpec,
    PipelineConfig,
    default_mock_config,
    extract_citations,
    parse_json,
    run_pipeline,
    validate_keys,
)


def test_parse_json_from_fenced_block() -> None:
    txt = 'foo\n```json\n{"a": 1, "b": [2,3]}\n```\nbar'
    assert parse_json(txt) == {"a": 1, "b": [2, 3]}


def test_parse_json_bare_object() -> None:
    assert parse_json('noise {"x": true} trailing')["x"] is True


def test_validate_keys() -> None:
    assert validate_keys({"a": 1}, ["a", "b"]) == ["b"]
    assert validate_keys({"a": 1, "b": 2}, ["a", "b"]) == []


def test_extract_citations_dedup() -> None:
    urls = extract_citations("see https://a.com x", "https://a.com and https://b.org")
    assert urls == ["https://a.com", "https://b.org"]


def test_config_validation_rejects_small_panel() -> None:
    cfg = PipelineConfig(
        models={"m": ModelSpec("m", "mock", "x")},
        panel=["m"],
        judge="m",
    )
    try:
        Engine(cfg)
    except ValueError as exc:
        assert "panel" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("应当因 panel < 2 而报错")


def test_config_validation_rejects_unknown_provider() -> None:
    cfg = PipelineConfig(
        models={
            "a": ModelSpec("a", "nope", "x"),
            "b": ModelSpec("b", "mock", "y"),
        },
        panel=["a", "b"],
        judge="b",
    )
    try:
        Engine(cfg)
    except ValueError as exc:
        assert "provider" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("应当因未知 provider 而报错")


def test_budget_guard_trips() -> None:
    # 价格极高 + 预算极低 -> 第一次调用即熔断
    cfg = PipelineConfig(
        models={
            "a": ModelSpec("a", "mock", "x", price_out_per_mtok=1e9),
            "b": ModelSpec("b", "mock", "y", price_out_per_mtok=1e9),
            "j": ModelSpec("j", "mock", "z"),
        },
        panel=["a", "b"],
        judge="j",
        max_usd=1e-9,
        max_retries=0,
    )

    async def _run() -> None:
        await run_pipeline("会爆预算吗?", cfg)

    try:
        asyncio.run(_run())
    except BudgetExceededError:
        pass
    else:  # pragma: no cover
        raise AssertionError("应当触发预算熔断")


def test_pipeline_end_to_end_structure() -> None:
    cfg = default_mock_config()
    report = asyncio.run(run_pipeline("示例问题:1+1=?", cfg, context="算术"))

    # 顶层结构
    for key in (
        "question",
        "tldr",
        "verdict",
        "answers",
        "reviews",
        "verification",
        "cost_usd",
    ):
        assert key in report, f"报告缺少字段 {key}"

    # TL;DR 必须可直接给人看
    tldr = report["tldr"]
    for key in ("answer", "confidence", "models_agree", "next_action", "abstain"):
        assert key in tldr

    # 两个作答者都应有答案
    assert set(report["answers"].keys()) == {"gpt", "gemini"}
    # 互评应为 2 条(A评B、B评A)
    assert len(report["reviews"]) == 2
    # 裁判结论字段齐全(mock 给的是完整 JSON)
    assert report["schema_issues"]["judge_missing_keys"] == []
    # 成本应被记录
    assert report["cost_usd"] >= 0.0


def test_self_consistency_with_samples() -> None:
    cfg = default_mock_config()
    cfg.samples = 3
    report = asyncio.run(run_pipeline("采样一致性测试", cfg))
    for name in ("gpt", "gemini"):
        sc = report["answers"][name]["self_consistency"]
        assert 0.0 <= sc <= 1.0


def _run_all() -> int:
    fns = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
