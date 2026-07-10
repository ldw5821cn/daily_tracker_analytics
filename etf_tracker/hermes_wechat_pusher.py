#!/usr/bin/env python3
"""
Hermes 微信通道推送模块 - 增强版
使用 Hermes 内置的微信通道发送 ETF 投资规划报告
支持：
1. 通过 Hermes Web UI API 发送消息
2. 自动获取微信配置
3. 报告摘要生成
4. 图片推送（K线图、资金流向图）
5. 批量推送多只股票预警
"""

import os
import sys
import json
import urllib.request
import urllib.parse
from datetime import datetime
from typing import Optional, List, Dict
import base64

# 添加 akshare 路径 (仅在非虚拟环境时)

class HermesWeChatPusher:
    """使用 Hermes 微信通道发送消息"""
    
    def __init__(self):
        """初始化微信推送器"""
        # 从 Hermes 配置中读取微信配置
        self.config = self._load_hermes_config()
        self.weixin_config = self.config.get('platforms', {}).get('weixin', {})
        self.token = self.weixin_config.get('token', '')
        self.account_id = self.weixin_config.get('extra', {}).get('account_id', '')
        self.base_url = self.weixin_config.get('extra', {}).get('base_url', 'https://ilinkai.weixin.qq.com')
        
        # 备用：从环境变量或运行时配置获取
        if not self.token:
            self.token = os.environ.get('HERMES_WEIXIN_TOKEN', '')
        if not self.account_id:
            self.account_id = os.environ.get('HERMES_WEIXIN_ACCOUNT_ID', '')
        
        # 最终备用：从 Hermes 配置目录读取（仅本地，不入 git）
        if not self.token:
            self.token = os.environ.get('HERMES_WEIXIN_TOKEN', '')
            self.account_id = os.environ.get('HERMES_WEIXIN_ACCOUNT_ID', '')
        
        # 获取 Hermes API 基础URL
        self.hermes_api_base = self._get_hermes_api_base()
        
        if not self.token:
            print("⚠️ 未找到微信配置，请检查 Hermes 配置")
    
    def _load_hermes_config(self) -> dict:
        """加载 Hermes 配置文件"""
        config_path = os.path.expanduser('~/.hermes/config.yaml')
        try:
            # 尝试使用 yaml 模块
            try:
                import yaml
                with open(config_path, 'r') as f:
                    return yaml.safe_load(f)
            except ImportError:
                pass
            
            # 备用：手动解析 platforms.weixin 配置
            config = {'platforms': {'weixin': {}}}
            with open(config_path, 'r') as f:
                lines = f.readlines()
            
            in_platforms = False
            in_weixin = False
            in_extra = False
            
            for i, line in enumerate(lines):
                stripped = line.rstrip()
                indent = len(line) - len(line.lstrip())
                
                # 进入 platforms 块
                if stripped == 'platforms:':
                    in_platforms = True
                    continue
                
                if not in_platforms:
                    continue
                
                # 离开 platforms 块（遇到相同或更低缩进的顶级键）
                if stripped and indent <= 0 and not stripped.startswith('#'):
                    if ':' in stripped and not stripped.startswith('platforms'):
                        break
                
                # 进入 weixin 块
                if stripped.startswith('weixin:') and indent == 2:
                    in_weixin = True
                    in_extra = False
                    continue
                
                if not in_weixin:
                    continue
                
                # 离开 weixin 块
                if stripped and indent <= 2 and not stripped.startswith('#'):
                    if ':' in stripped and not stripped.startswith('weixin'):
                        break
                
                # 解析 token
                if stripped.startswith('token:') and indent == 4:
                    value = stripped.split(':', 1)[1].strip().strip('"').strip("'")
                    config['platforms']['weixin']['token'] = value
                
                # 进入 extra 块
                if stripped.startswith('extra:') and indent == 4:
                    in_extra = True
                    continue
                
                if in_extra and indent == 6:
                    if stripped.startswith('account_id:'):
                        value = stripped.split(':', 1)[1].strip().strip('"').strip("'")
                        if 'extra' not in config['platforms']['weixin']:
                            config['platforms']['weixin']['extra'] = {}
                        config['platforms']['weixin']['extra']['account_id'] = value
                    elif stripped.startswith('base_url:'):
                        value = stripped.split(':', 1)[1].strip().strip('"').strip("'")
                        if 'extra' not in config['platforms']['weixin']:
                            config['platforms']['weixin']['extra'] = {}
                        config['platforms']['weixin']['extra']['base_url'] = value
            
            return config
        except Exception as e:
            print(f"⚠️ 读取 Hermes 配置失败: {e}")
            return {}
    
    def _get_hermes_api_base(self) -> str:
        """获取 Hermes API 基础URL"""
        # 尝试从环境变量或配置中获取
        api_base = os.environ.get('HERMES_API_BASE', 'http://127.0.0.1:8080')
        return api_base
    
    def send_text_message(self, content: str) -> bool:
        """
        发送文本消息到微信
        
        在 Hermes cron job 中，agent 的输出会自动被捕获并发送到微信（deliver=origin）。
        因此本地脚本只需要将消息内容输出到 stdout 即可。
        
        Args:
            content: 消息内容
        """
        if not self.token:
            print("❌ 微信未配置，无法发送消息")
            return False
        
        try:
            # 在 cron job 环境下，直接输出内容即可被 Hermes 捕获并推送
            # 添加标记便于识别
            print(f"\n{'='*60}")
            print("📱 微信推送内容")
            print(f"{'='*60}")
            print(content)
            print(f"{'='*60}\n")
            print("✅ 微信消息已输出，等待 Hermes 推送")
            return True
                
        except Exception as e:
            print(f"❌ 发送异常: {e}")
            return False
    
    def send_markdown_message(self, content: str) -> bool:
        """发送 Markdown 格式消息"""
        # 将 Markdown 转换为简单文本格式
        text_content = content.replace('# ', '【').replace('\n# ', '\n【').replace('**', '')
        return self.send_text_message(text_content)
    
    def send_image_message(self, image_path: str, caption: str = "") -> bool:
        """发送图片消息"""
        if not os.path.exists(image_path):
            print(f"❌ 图片不存在: {image_path}")
            return False
        
        try:
            # 读取图片并转为base64
            with open(image_path, 'rb') as f:
                image_data = base64.b64encode(f.read()).decode('utf-8')
            
            # 发送图片（通过 Hermes API）
            url = f"{self.hermes_api_base}/api/hermes/weixin/send_image"
            
            data = {
                "token": self.token,
                "account_id": self.account_id,
                "image_data": image_data,
                "caption": caption
            }
            
            headers = {'Content-Type': 'application/json'}
            
            req = urllib.request.Request(
                url,
                data=json.dumps(data, ensure_ascii=False).encode('utf-8'),
                headers=headers,
                method='POST'
            )
            
            resp = urllib.request.urlopen(req, timeout=15)
            result = json.loads(resp.read().decode('utf-8'))
            
            if result.get('success') or result.get('errcode') == 0:
                print(f"✅ 图片发送成功: {image_path}")
                return True
            else:
                print(f"❌ 图片发送失败: {result}")
                return False
                
        except Exception as e:
            print(f"❌ 图片发送异常: {e}")
            return False
    
    def send_report_summary(self, report_path: str, etf_name: str, etf_code: str,
                           score: float, signal: str, action: str,
                           predictions: Dict = None, alerts: List[Dict] = None) -> bool:
        """发送报告摘要到微信"""
        try:
            with open(report_path, 'r', encoding='utf-8') as f:
                report_content = f.read()
        except Exception as e:
            print(f"读取报告失败: {e}")
            return False
        
        # 构建摘要消息（精简版，避免微信频率限制）
        summary = f"""📊 {etf_name} ({etf_code}) 日报

📅 日期: {datetime.now().strftime('%Y-%m-%d')}
📈 综合评分: {score}/100
🎯 信号状态: {signal}
💡 操作建议: {action}
"""
        
        # 添加预测信息
        if predictions and 'error' not in predictions:
            summary += "\n🔮 趋势预测:\n"
            for day_key in ['day_1', 'day_3', 'day_5']:
                if day_key in predictions:
                    pred = predictions[day_key]
                    day_num = day_key.replace('day_', '')
                    summary += f"  {day_num}日: {pred['trend']} (置信度: {pred['confidence']:.0%})\n"
        
        # 添加预警信息
        if alerts and len(alerts) > 0:
            summary += f"\n🚨 预警 ({len(alerts)}条):\n"
            for alert in alerts[:5]:  # 最多显示5条
                emoji = "⚠️" if alert['level'] == 'warning' else "ℹ️"
                summary += f"  {emoji} [{alert['type']}] {alert['message']}\n"
        
        summary += "\n---\n💡 完整报告请查看 GitHub 仓库\n"
        
        return self.send_text_message(summary)
    
    def send_stock_alerts(self, alerts: List[Dict]) -> bool:
        """发送个股预警消息"""
        if not alerts:
            return True
        
        # 按类型分组
        warning_alerts = [a for a in alerts if a['level'] == 'warning']
        info_alerts = [a for a in alerts if a['level'] == 'info']
        
        message = "🚨 个股预警提醒\n\n"
        
        if warning_alerts:
            message += "⚠️ 高风险预警:\n"
            for alert in warning_alerts[:10]:
                message += f"  • {alert['message']}\n"
            message += "\n"
        
        if info_alerts:
            message += "ℹ️ 提示信息:\n"
            for alert in info_alerts[:10]:
                message += f"  • {alert['message']}\n"
        
        return self.send_text_message(message)
    
    def send_daily_report_with_images(self, report_path: str, image_paths: List[str],
                                     etf_name: str, etf_code: str,
                                     score: float, signal: str, action: str) -> bool:
        """发送带图片的日报"""
        # 先发送文字摘要
        success = self.send_report_summary(report_path, etf_name, etf_code, score, signal, action)
        
        # 再发送图片
        if success and image_paths:
            for img_path in image_paths[:3]:  # 最多3张图片
                if os.path.exists(img_path):
                    self.send_image_message(img_path)
        
        return success


def send_daily_report(report_path: str, etf_name: str = "稀土ETF嘉实",
                      etf_code: str = "516150", score: float = 0,
                      signal: str = "", action: str = "",
                      predictions: Dict = None, alerts: List[Dict] = None) -> bool:
    """
    发送每日报告到微信的便捷函数
    """
    pusher = HermesWeChatPusher()
    return pusher.send_report_summary(report_path, etf_name, etf_code, score, signal, action, predictions, alerts)


def send_stock_alerts(alerts: List[Dict]) -> bool:
    """发送个股预警的便捷函数"""
    pusher = HermesWeChatPusher()
    return pusher.send_stock_alerts(alerts)


if __name__ == "__main__":
    # 测试推送
    pusher = HermesWeChatPusher()
    pusher.send_text_message("🚀 ETF 跟踪系统测试消息\n\n如果您收到这条消息，说明 Hermes 微信推送配置成功！")
