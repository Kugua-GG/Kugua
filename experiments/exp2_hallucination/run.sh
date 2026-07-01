#!/bin/bash
# 实验2: 幻觉免疫测试 — 一键复现
# 用法: bash experiments/exp2_hallucination/run.sh
set -e
cd "$(dirname "$0")/../.."
echo "=== kugua 实验2: 幻觉免疫测试 ==="
echo "场景: TruthfulQA + 对抗性文章, 15题对照"
echo ""
python experiments/exp2_hallucination/run_experiment.py --questions 15 --seed 42
echo ""
echo "输出: experiments/exp2_hallucination/output/{raw_data.csv, summary.json, report.md}"
