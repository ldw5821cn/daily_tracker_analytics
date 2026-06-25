#!/usr/bin/env python3
"""
微信推送模块 - 企业微信群机器人
用于发送 ETF 投资规划报告到微信
"""

import os
import json
import urllib.request
from datetime import datetime
from typing import Optional

class WeChatPusher:
    """企业微信机器人推送器"""
    
    def __init__(self, webhook_url: Optional[str] = None):
        """
        初始化微信推送器
        
        Args:
            webhook_url: 企业微信群机器人 Webhook 地址
                        格式: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY
        """
        self.webhook_url = webhook_url or os.getenv('WECHAT_WEBHOOK_URL')
        if not self.webhook_url:
            print("⚠️ 未配置 WECHAT_WEBHOOK_URL，微信推送功能不可用")
    
    def send_text(self, content: str) -> bool:
        """发送纯文本消息"""
        if not self.webhook_url:
            return False
        
        data = {
            "msgtype": "text",
            "text": {
                "content": content
            }
        }
        return self._send(data)
    
    def send_markdown(self, content: str) -> bool:
        """发送 Markdown 格式消息"""
        if not self.webhook_url:
            return False
        
        # 企业微信 markdown 限制 4096 字节
        if len(content.encode('utf-8')) > 4000:
            content = content[:2000] + "\n\n...（内容已截断，详见完整报告）"
        
        data = {
            "msgtype": "markdown",
            "markdown": {
                "content": content
            }
        }
        return self._send(data)
    
    def send_report_summary(self, report_path: str, etf_name: str, etf_code: str, 
                           score: float, signal: str, action: str) -> bool:
        """发送报告摘要"""
        if not self.webhook_url:
            return False
        
        # 读取报告内容
        try:
            with open(report_path, 'r', encoding='utf-8') as f:
                report_content = f.read()
        except Exception as e:
            print(f"读取报告失败: {e}")
            return False
        
        # 构建摘要
        summary = f"""📊 **{etf_name} ({etf_code}) 日报** 
> **日期**: {datetime.now().strftime('%Y-%m-%d')}

**综合评分**: {score}/100
**信号状态**: {signal}
**操作建议**: {action}

---

{report_content[:1500]}...

---

💡 完整报告请查看附件或 GitHub 仓库
"""
        
        return self.send_markdown(summary)
    
    def send_image(self, image_path: str) -> bool:
        """发送图片消息（如趋势图）"""
        if not self.webhook_url:
            return False
        
        import base64
        import hashlib
        
        try:
            with open(image_path, 'rb') as f:
                image_data = f.read()
            
            base64_data = base64.b64encode(image_data).decode('utf-8')
            md5 = hashlib.md5(image_data).hexdigest()
            
            data = {
                "msgtype": "image",
                "image": {
                    "base64": base64_data,
                    "md5": md5
                }
            }
            return self._send(data)
        except Exception as e:
            print(f"发送图片失败: {e}")
            return False
    
    def _send(self, data: dict) -> bool:
        """发送请求到企业微信"""
        try:
            req = urllib.request.Request(
                self.webhook_url,
                data=json.dumps(data, ensure_ascii=False).encode('utf-8'),
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            
            resp = urllib.request.urlopen(req, timeout=10)
            result = json.loads(resp.read().decode('utf-8'))
            
            if result.get('errcode') == 0:
                print("✅ 微信推送成功")
                return True
            else:
                print(f"❌ 微信推送失败: {result}")
                return False
                
        except Exception as e:
            print(f"❌ 微信推送异常: {e}")
            return False


def send_daily_report(report_path: str, etf_name: str = "稀土ETF嘉实", 
                      etf_code: str = "516150", score: float = 0, 
                      signal: str = "", action: str = "") -> bool:
    """
    发送每日报告到微信的便捷函数
    
    使用环境变量 WECHAT_WEBHOOK_URL 配置 Webhook 地址
    """
    pusher = WeChatPusher()
    return pusher.send_report_summary(report_path, etf_name, etf_code, score, signal, action)


if __name__ == "__main__":
    # 测试推送
    pusher = WeChatPusher()
    pusher.send_text("🚀 ETF 跟踪系统测试消息\n\n如果您收到这条消息，说明微信推送配置成功！")
