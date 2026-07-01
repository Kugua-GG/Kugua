#!/bin/bash
# 实验1: 安全门控混沌测试 — 一键复现
# 用法: bash experiments/exp1_safety_gate/run.sh
set -e
cd "$(dirname "$0")/../.."
echo "=== kugua 实验1: 安全门控混沌测试 ==="
echo "场景: 工业配方调整Agent, 200次对照试验"
echo ""
python experiments/exp1_safety_gate/run_experiment.py --runs 200 --seed 42
echo ""
echo "输出: experiments/exp1_safety_gate/output/{raw_data.csv, summary.json, report.md}"
