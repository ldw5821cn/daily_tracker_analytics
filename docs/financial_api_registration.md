# 同花顺 Financial-API 注册指南

## 注册流程

### 步骤 1：访问官网

**网址**: https://fuyao.aicubes.cn/

### 步骤 2：登录/注册

点击右上角 **「登录」** 按钮

**登录方式**：
- 使用 **同花顺账号** 登录（统一账号体系）
- 如果没有账号，需要先注册同花顺账号

### 步骤 3：获取 API Key

登录后，访问 **「API Key 管理」** 页面：
- https://fuyao.aicubes.cn/admin/

点击 **「创建 API Key」** 按钮，生成专属 API Key

---

## 注册方式推测

根据同花顺常规流程，可能支持：

| 方式 | 说明 |
|------|------|
| **手机号 + 验证码** | 最常见，短信验证码登录 |
| **账号 + 密码** | 传统方式 |
| **第三方登录** | 微信/QQ/支付宝等 |

> 具体以官网实际显示为准

---

## 可能遇到的问题

### 问题 1：Nginx 403 Forbidden

**原因**：
- IP 被限制（海外 IP、代理、VPN）
- 访问频率过高
- 浏览器环境问题

**解决**：
1. 切换网络（关闭代理/VPN）
2. 使用国内网络访问
3. 清除浏览器缓存
4. 更换浏览器（推荐 Chrome/Edge）
5. 联系官方客服

### 问题 2：无法收到验证码

**解决**：
1. 检查手机信号
2. 检查短信拦截
3. 等待 60 秒后重试
4. 联系客服

---

## 替代方案

### 方案 A：同花顺 App 注册

1. 下载 **同花顺 App**（iOS/Android）
2. 使用手机号注册账号
3. 在 App 内访问「金融数据 API」入口
4. 获取 API Key

### 方案 B：联系客服

- 官网右下角 **「反馈交流渠道」** 按钮
- 或发送邮件至官方邮箱

### 方案 C：GitHub Issues

在项目仓库提交 Issue 求助：
- https://github.com/HiThink-Tech/Financial-API/issues

---

## 注册后配置

### 获取 API Key

1. 登录后访问：https://fuyao.aicubes.cn/admin/
2. 点击「创建 API Key」
3. 复制生成的 API Key（格式类似 `htk_xxxxxxxxxxxx`）

### 配置到项目

```bash
# 方式 1：环境变量
export HITHINK_API_KEY="htk_xxxxxxxxxxxx"

# 方式 2：.env 文件
echo "HITHINK_API_KEY=htk_xxxxxxxxxxxx" >> .env
```

### 测试 API

```bash
# 安装 CLI
npm install -g hithink-finance-cli

# 配置
hithink-finance config set api-key htk_xxxxxxxxxxxx

# 测试查询
hithink-finance quote --thscode 000001.SZ
```

---

## 免费额度（待确认）

注册后建议查看：
- 官网「定价」页面
- API 文档中的「配额说明」
- 用户后台的「用量统计」

**推测**：
- 实时行情：有限次/日
- 历史K线：较大额度
- 财务报表：较大额度

---

## 注意事项

1. **API Key 安全**：不要泄露，不要提交到 Git
2. **额度控制**：注意免费额度，避免超额收费
3. **IP 白名单**：如有需要，配置 IP 白名单增强安全
4. **备用方案**：保留现有数据源作为 fallback

---

## 快速开始（注册后）

```bash
# 1. 克隆项目
git clone https://github.com/HiThink-Tech/Financial-API.git
cd Financial-API

# 2. 安装 Python SDK
cd python
pip install -e .

# 3. 配置 API Key
export HITHINK_API_KEY="your-api-key"

# 4. 测试
python -c "
from hithink_finance import HiThinkFinance
client = HiThinkFinance()
quote = client.stock_quote('000001.SZ')
print(quote)
"
```

---

**最后更新**: 2026-10-07  
**官网**: https://fuyao.aicubes.cn/
