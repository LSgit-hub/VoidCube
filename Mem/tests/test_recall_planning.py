from __future__ import annotations

from memai.application.recall import _lexical_score, build_recall_plan


def test_cjk_constraint_query_preserves_discriminative_tail_anchor() -> None:
    plan = build_recall_plan("晚上几点以后不要再通知这件事情")

    assert "通知" in plan.terms
    score, matched = _lexical_score(
        plan,
        "夜间通知偏好 晚上十点之后请勿推送通知 晚上 通知 勿扰",
    )

    assert score >= 0.5
    assert {"通知", "constraint_signal"} <= set(matched)


def test_verbose_cjk_query_rewards_multiple_distinct_exact_anchors() -> None:
    plan = build_recall_plan(
        "请完整回忆我们之前讨论记忆召回质量时形成的方案，特别是词法索引如何"
        "筛选候选、语义相似度怎样参与，以及用户反馈最终如何影响排序结果"
    )

    score, matched = _lexical_score(
        plan,
        "记忆召回排序策略 记忆召回先使用词法索引筛选候选，再融合语义相似度"
        "和用户反馈完成排序。词法索引 语义相似度 用户反馈 排序",
    )

    assert score >= 0.35
    assert len(set(matched)) >= 3
