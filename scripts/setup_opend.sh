#!/usr/bin/env bash
# 一键下载并部署富途 OpenD 网关（Linux x64）
# 用法：./scripts/setup_opend.sh [安装目录]
# OpenD 是富途 API 必需的本地网关，需用牛牛号登录。

set -e

INSTALL_DIR="${1:-$HOME/.futu-opend}"
VERSION_FILE="$INSTALL_DIR/.version"
OPEND_VERSION="${OPEND_VERSION:-8.2.3308}"
TARBALL="Opend-linux-${OPEND_VERSION}.tar.gz"
DOWNLOAD_URL="https://softwarefile.futunn.com/${TARBALL}"

echo "[setup_opend] 目标目录: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"

cd "$INSTALL_DIR"

if [ -f "$VERSION_FILE" ] && [ "$(cat "$VERSION_FILE")" = "$OPEND_VERSION" ]; then
    echo "[setup_opend] OpenD $OPEND_VERSION 已存在，跳过下载"
else
    echo "[setup_opend] 下载 OpenD $OPEND_VERSION ..."
    if ! wget -O "$TARBALL" "$DOWNLOAD_URL" --timeout=120 -t 2; then
        echo "[setup_opend] 自动下载失败，请手动下载："
        echo "  $DOWNLOAD_URL"
        echo "  解压到 $INSTALL_DIR 后重试。"
        exit 1
    fi
    tar -xzf "$TARBALL"
    echo "$OPEND_VERSION" > "$VERSION_FILE"
fi

# 查找 FutuOpenD 可执行文件
OPEND_BIN=$(find "$INSTALL_DIR" -maxdepth 2 -type f -name 'FutuOpenD*' | head -n 1)
if [ -z "$OPEND_BIN" ]; then
    echo "[setup_opend] 未找到 FutuOpenD 可执行文件，请检查解压结果"
    exit 1
fi

chmod +x "$OPEND_BIN"
echo "[setup_opend] OpenD 可执行文件: $OPEND_BIN"

# 生成配置模板（如不存在）
CONFIG_XML="$INSTALL_DIR/FutuOpenD.xml"
if [ ! -f "$CONFIG_XML" ]; then
    cat > "$CONFIG_XML" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<FutuOpenD>
    <!-- 登录配置：必填 -->
    <login_account>你的牛牛号</login_account>
    <login_pwd>你的登录密码</login_pwd>
    <!-- 或使用手机验证码登录（首次建议） -->

    <!-- 监听地址：默认 127.0.0.1:11111 -->
    <ip_addr>127.0.0.1</ip_addr>
    <port>11111</port>

    <!-- 是否启用加密 -->
    <is_encrypt>false</is_encrypt>

    <!-- 语言：chs/eng -->
    <lang>chs</lang>

    <!-- 行情权限：默认 -->
    <quote_update_freq>1000</quote_update_freq>

    <!-- 日志路径 -->
    <log_path>./log</log_path>
</FutuOpenD>
EOF
    echo "[setup_opend] 已生成配置模板: $CONFIG_XML"
    echo "[setup_opend] 请编辑配置：填写牛牛号、密码，然后运行："
    echo "  $OPEND_BIN -c $CONFIG_XML"
else
    echo "[setup_opend] 配置已存在: $CONFIG_XML"
fi

echo "[setup_opend] 完成。启动命令："
echo "  $OPEND_BIN -c $CONFIG_XML"
