#!/usr/bin/env python3
"""
Windsurf Cascade Response Logger Hook
记录和分析 Cascade 的响应内容
"""

import json
import sys
import os
import re
from datetime import datetime
from pathlib import Path

def main():
    try:
        # 从 stdin 读取 hook 输入
        input_data = sys.stdin.read()
        if not input_data.strip():
            print("[Cascade Logger Error] 没有输入数据", file=sys.stderr)
            return 1
            
        try:
            hook_input = json.loads(input_data)
        except json.JSONDecodeError as e:
            print(f"[Cascade Logger Error] JSON 解析失败: {e}", file=sys.stderr)
            return 1
        
        # 提取关键信息
        agent_action = hook_input.get('agent_action_name')
        trajectory_id = hook_input.get('trajectory_id')
        execution_id = hook_input.get('execution_id')
        timestamp = hook_input.get('timestamp')
        model_name = hook_input.get('model_name', 'Unknown')
        response = hook_input.get('tool_info', {}).get('response', '')
        
        # 验证响应内容
        if not isinstance(response, str):
            print("[Cascade Logger Error] response 不是字符串类型", file=sys.stderr)
            return 1
        
        # 验证必需字段
        if agent_action != 'post_cascade_response':
            print(f"[Cascade Logger Error] 错误的 hook 类型: {agent_action}", file=sys.stderr)
            return 1
            
        if 'tool_info' not in hook_input:
            print("[Cascade Logger Error] 缺少 tool_info 字段", file=sys.stderr)
            return 1
        
        # 创建日志目录
        log_dir = Path('.windsurf/logs')
        log_dir.mkdir(exist_ok=True, parents=True)
        
        # 创建日志文件
        log_file = log_dir / 'cascade_responses.log'
        
        # 准备日志条目
        log_entry = {
            'timestamp': timestamp,
            'trajectory_id': trajectory_id,
            'execution_id': execution_id,
            'model_name': model_name,
            'response_length': len(response),
            'response_full': response  # 存储完整响应
        }
        
        # 写入日志
        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                # 使用更安全的 JSON 序列化方式，处理Unicode字符
                json_str = json.dumps(log_entry, ensure_ascii=True, default=str)
                f.write(json_str + '\n')
        except Exception as e:
            print(f"[Cascade Logger] 写入日志失败: {e}", file=sys.stderr)
            # 备用方案：使用 repr 确保可写
            try:
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(repr(log_entry) + '\n')
            except Exception:
                pass  # 最后的备用方案也失败，静默处理
        
        # 创建简单的统计报告
        stats_file = log_dir / 'cascade_stats.json'
        update_statistics(stats_file, log_entry)
        
        # 输出摘要（可选，用于调试）
        print(f"[Cascade Logger] 记录响应: {len(response_clean)} 字符, 模型: {model_name}")
        
        return 0
        
    except Exception as e:
        print(f"[Cascade Logger Error] {str(e)}", file=sys.stderr)
        return 1

def update_statistics(stats_file, log_entry):
    """更新统计信息"""
    try:
        stats = {}
        
        # 读取现有统计
        if stats_file.exists():
            try:
                with open(stats_file, 'r', encoding='utf-8') as f:
                    stats = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                print(f"[Cascade Logger] 读取统计文件失败，重新创建: {e}", file=sys.stderr)
                stats = {}
        
        # 初始化统计字段
        today = datetime.now().strftime('%Y-%m-%d')
        if today not in stats:
            stats[today] = {
                'total_responses': 0,
                'total_characters': 0,
                'responses_with_code': 0,
                'responses_with_files': 0,
                'responses_with_commands': 0,
                'models_used': []
            }
        
        # 更新统计
        day_stats = stats[today]
        day_stats['total_responses'] += 1
        day_stats['total_characters'] += log_entry['response_length']
        # 简化统计，移除复杂检测
        day_stats['responses_with_code'] = day_stats.get('responses_with_code', 0) + (1 if '```' in log_entry['response_full'] else 0)
        day_stats['responses_with_files'] = day_stats.get('responses_with_files', 0) + (1 if any(keyword in log_entry['response_full'].lower() for keyword in ['created file', 'modified file', 'deleted file']) else 0)
        day_stats['responses_with_commands'] = day_stats.get('responses_with_commands', 0) + (1 if any(keyword in log_entry['response_full'].lower() for keyword in ['`npm', '`git', '`python', '```bash']) else 0)
        
        # 避免重复添加模型名
        if log_entry['model_name'] not in day_stats['models_used']:
            day_stats['models_used'].append(log_entry['model_name'])
        
        # 保存统计（使用临时文件避免竞争条件）
        temp_file = stats_file.with_suffix('.tmp')
        try:
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(stats, f, ensure_ascii=False, indent=2)
            # 原子性替换
            temp_file.replace(stats_file)
        except Exception as e:
            print(f"[Cascade Logger] 保存统计文件失败: {e}", file=sys.stderr)
            # 清理临时文件
            if temp_file.exists():
                temp_file.unlink()
                
    except Exception as e:
        print(f"[Cascade Logger] 更新统计时出错: {e}", file=sys.stderr)

if __name__ == '__main__':
    sys.exit(main())
