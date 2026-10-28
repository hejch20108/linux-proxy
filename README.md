# Linux sing-box 自动部署与使用

本项目支持机场订阅链接和本地配置文件上传解析，两条路径会合并去重成一份最终的配置文件，供 `sing-box` 使用。
部分机场会拦截 Linux 的请求，或云服务器无法与机场订阅握手，导致订阅链接无法读取和解析，这属于外部限制，非本项目问题。推荐机场： [山海](https://shanhai.me/#/dashboard) 。

## 一、连接远程云服务器

通过 Termius、VS Code 等终端工具连接远程云服务器

## 二、下载自动化脚本和分流规则

- 下载

  ```bash
  curl -fsSL https://gh-proxy.org/https://raw.githubusercontent.com/hejch20108/linux-proxy/main/sing_box_auto.py -o 'sing_box_auto.py' && \
  curl -fsSL https://gh-proxy.org/https://raw.githubusercontent.com/hejch20108/linux-proxy/main/custom_config.yaml -o "custom_config.yaml"
  ```

- 验证是否下载成功

  ```bash
  ls -l sing_box_auto.py custom_config.yaml
  ```

## 三、首次部署

- 执行如下命令

  ```bash
  sudo install -d -m 755 /etc/apt/sources.list.d && \
  sudo python3 sing_box_auto.py deploy && \
  rm -f sing_box_auto.py custom_config.yaml
  ```

`deploy` 会完成：

1. 检查 Debian/Ubuntu、CPU 架构和 systemd；
2. 安装 Python、PyYAML、curl 和 sing-box；
3. 创建 `/etc/sing-box-auto`、`/var/lib/sing-box-auto`；
4. 安装 `sing-box-auto.service` 和可选更新 timer；
5. 将脚本复制到 `/usr/local/libexec/sing-box-auto.py`。

部署过程中会显示 `[阶段 1/7]` 到 `[阶段 7/7]`，每个阶段结束时显示耗时。APT 安装阶段会保留包管理器输出；如果某一阶段长时间没有结束，可以根据最后一个阶段判断卡在环境检查、软件包下载、订阅输入还是 systemd 配置，可以按 `Ctrl + C` 终止任务，然后重新执行 `deploy`。

如果没有保存过订阅，脚本会交互式询问：

1. `请输入或粘贴订阅链接...`：粘贴订阅链接，输入内容不会回显，按回车提交；
2. `请给你刚才输入的订阅链接起个名字...`：输入 `shanhai`；
3. 再次询问订阅链接时直接按回车结束。

> **建议优先尝试机场订阅地址能否解析成功，输入一个或多个订阅。如果无法解析成功，再考虑本地配置上传的方案**

## 四、优先使用订阅链接

部署完成后，在 Linux 服务器执行：

```bash
sudo python3 /usr/local/libexec/sing-box-auto.py update
```

`update` 会读取订阅、解析节点、下载规则集、生成最终配置、执行 `sing-box check`，然后重启服务。
订阅下载每次尝试的超时为 10 秒；只有至少一个来源成功时才会生成新配置，所有来源都失败时才复用已有最终配置。

如果第 4 阶段显示所有订阅来源都不可用，或云服务器无法访问订阅入口，转到下一节的本地上传方案。若远程订阅失败但本地文件成功，更新会继续使用本地文件。

## 五、订阅失败时上传本地配置

本节的操作分为 **Mac 本地终端** 和 **Linux 服务器终端**。配置文件在 Mac 准备并上传。

### 5.1 在 Mac 准备本地配置文件（本教程以 Karing 为例）

打开 `Karing` -> `我的配置` -> `选中某一配置` -> `点击分享按钮` -> `拷贝` -> `在 Mac Finder 任意找一文件夹` -> `粘贴`

### 5.2 从 Mac 上传到服务器临时目录

通过 `Termius` 等工具 `SFTP` 将配置文件上传到 Linux 云服务器的 `Home` 目录，可以重复上传多个文件，例如 `shanhai.json` 和 `yep.json`。

### 5.3 在 Linux 安装本地配置文件

以下命令把上传文件移动到服务使用的受保护目录，并设置 `root:sing-box` 和 `0640` 权限。

**文件名和路径要按实际上传的文件填写。** 即下方 `~/shanhai.json` 两处位置，如有多个文件，可以在 `~/shanhai.json` 两处位置后面空格增加。

```bash
sudo install -d -m 0750 -o root -g sing-box /var/lib/sing-box-auto/subscriptions-cache && \
sudo install -m 0640 -o root -g sing-box ~/shanhai.json /var/lib/sing-box-auto/subscriptions-cache/ && \
rm -f ~/shanhai.json
```

### 5.4 在 Linux 准备来源清单

在 Linux 服务器终端执行如下命令，创建来源清单。不要保留不存在的文件或不可访问的订阅地址：

```bash
cat > ~/subscriptions.yaml <<'EOF'
subscriptions:
  - name: shanhai-remote  # 远程订阅名称(任意名称)
    source: url
    source_group: shanhai  # 组别(用于识别同一个订阅的不同获取方式)
    priority: 10  # 统一组别数字越小优先级越高(建议优先 URL 订阅链接的方式)
    url: "https://YOUR_SUBSCRIPTION_HOST/YOUR_SUBSCRIPTION_PATH"  # 订阅链接

  - name: shanhai-local  # 本地配置文件名称(任意名称)
    source: file
    source_group: shanhai  # 组别(用于识别同一个订阅的不同获取方式)
    priority: 20  # 统一组别数字越小优先级越高
    path: /var/lib/sing-box-auto/subscriptions-cache/shanhai.json  # 路径必须跟实际保存路径一致

  - name: yep-local  # 本地配置文件名称(任意名称)，如果有多个本地文件的情况才填写，否则可以将这一组删除
    source: file
    path: /var/lib/sing-box-auto/subscriptions-cache/yep.json
EOF
```

如果远程订阅和本地文件是同一个订阅的两种获取方式，应该把它们放入同一个 `source_group`，并设置优先级。数字越小越优先；同组只会选择一个成功来源，不会把两份同源节点重复合并：

### 5.5 在 Linux 执行如下命令，安装来源清单并生成配置

```bash
sudo install -m 0600 -o root -g root ~/subscriptions.yaml /etc/sing-box-auto/subscriptions.yaml && \
rm -f ~/subscriptions.yaml && \
sudo python3 /usr/local/libexec/sing-box-auto.py update
```

本地文件、订阅链接和多个本地文件会统一解析，按连接参数去重；节点标签、来源名称和导入配置中的 DNS 解析器标签不参与去重，最后只生成一份：

```text
/etc/sing-box-auto/config.json
```

sing-box 服务始终读取这份最终配置。同一 `source_group` 只选择一个成功来源；不同来源组会按连接参数合并去重。若部分来源暂时不可用，更新会记录不可用来源并继续使用成功来源；若所有来源暂时不可用，会复用之前通过校验的最终配置。若没有任何可用来源且不存在可用旧配置，会明确报错。

## 六、检查服务和代理

```bash
sudo systemctl enable --now sing-box-auto && \
sudo systemctl status sing-box-auto --no-pager && \
sudo ss -lntp | grep ':1080'
```

入口默认只监听：`127.0.0.1:1080`

## 七、测试

### 7.1 服务器本机测试代理

返回 HTTP 204 或其他成功响应，表示代理链路正常。

```bash
curl -x socks5h://127.0.0.1:1080 --max-time 20 -I https://www.gstatic.com/generate_204
```

### 7.2 测试分流规则

下面的命令使用 HTTP 代理访问域名，避免域名先在本机解析成 IP，导致域名规则无法匹配。
命令只输出每条规则的 PASS/FAIL、HTTP 状态和实际路由类型。

```bash
run_case() {
    local name="$1"
    local url="$2"
    local expected="$3"
    local host="${url#https://}"
    local start
    local http_code
    local route

    start="$(date --iso-8601=seconds)"
    http_code="$(curl -x http://127.0.0.1:1080 \
      --max-time 15 -sS -o /dev/null \
      -w '%{http_code}' -I "$url" 2>/dev/null || true)"
    route="$(sudo journalctl -u sing-box-auto --since "$start" --no-pager -o cat 2>/dev/null \
      | grep "$host" | grep -E 'outbound/(block|direct|anytls|vless)' | tail -1 || true)"

    case "$expected" in
      block)  ok="$(printf '%s' "$route" | grep -q 'outbound/block' && echo yes || echo no)"; label="block" ;;
      direct) ok="$(printf '%s' "$route" | grep -q 'outbound/direct' && echo yes || echo no)"; label="direct" ;;
      proxy)  ok="$(printf '%s' "$route" | grep -Eq 'outbound/(anytls|vless)' && echo yes || echo no)"; label="proxy" ;;
    esac

    if [ "$ok" = yes ]; then
      echo "PASS  $name  [$label]  HTTP=$http_code"
    else
      echo "FAIL  $name  [期望 $label]  HTTP=$http_code"
      echo "      最近路由: ${route:-未找到}"
    fi
}

run_case "广告拦截" "https://ads.google.com" block
run_case "美国 AI" "https://openai.com" proxy
run_case "新加坡" "https://ocbc.com" proxy
run_case "台湾" "https://www.okx.com" proxy
run_case "英国" "https://www.gov.uk" proxy
run_case "国内直连" "https://www.baidu.com" direct
run_case "默认代理" "https://www.wikipedia.org" proxy
```

### 7.3 查看当前 `all_auto` 的实际出口 IP

```bash
echo "=== 代理出口 IP 查询 ===" && \
IP=$(curl -x http://127.0.0.1:1080 --max-time 20 -fsS https://api.ipify.org) && \
echo "IP      : $IP" && \
curl --max-time 20 -fsS "https://api.iping.cc/v1/query?ip=$IP&language=zh" | \
jq -r '"国家    : \(.data.country)\n地区    : \(.data.region)\n城市    : \(.data.city)"'
```

当前 `us_auto`、`sg_auto` 等区域组使用 URLTest，日志会显示最终选中的节点。若某个区域组只有一个节点，日志只能确认走了代理节点，不能仅凭节点名称区分它属于哪个 URLTest 组。

## 八、持久化代理

写入 `~/.bashrc`

```bash
cat >> ~/.bashrc <<'EOF'

# sing-box 本机代理
export http_proxy='http://127.0.0.1:1080'
export https_proxy='http://127.0.0.1:1080'
export all_proxy='socks5://127.0.0.1:1080'
export HTTP_PROXY="$http_proxy"
export HTTPS_PROXY="$https_proxy"
export ALL_PROXY="$all_proxy"
EOF

source ~/.bashrc
```

检查变量是否生效：

```bash
env | grep -iE '^(http|https|all)_proxy='
```

## 九、更新

### 9.1 订阅日常手动更新

#### 订阅链接模式

```bash
sudo python3 /usr/local/libexec/sing-box-auto.py update
```

#### 本地文件模式

先在 Mac 更新配置文件，再重复第五章节操作。

### 9.2 订阅自动更新

自动更新默认关闭。订阅链接模式确认手动更新和代理测试稳定后，才建议启用：

```bash
sudo systemctl enable --now sing-box-auto-update.timer && \
sudo systemctl list-timers sing-box-auto-update.timer && \
sudo systemctl status sing-box-auto-update.timer --no-pager && \
sudo journalctl -u sing-box-auto-update.service -e --no-pager
```

本地文件模式不会自动从 Mac 上传新文件，因此不建议仅为本地文件模式启用 timer。

### 9.3 分流规则更新

如果需要修改分流规则，请在 Mac 本地修改项目中的 `custom_config.yaml`，然后提交并推送到 `GitHub`

```bash
curl -fsSL https://gh-proxy.org/https://raw.githubusercontent.com/hejch20108/linux-proxy/main/custom_config.yaml -o "custom_config.yaml" && \
sudo install -m 0640 -o root -g sing-box custom_config.yaml /etc/sing-box-auto/custom_config.yaml && \
rm -f custom_config.yaml && \
sudo python3 /usr/local/libexec/sing-box-auto.py update
```

## 十、服务管理

```bash
sudo systemctl start sing-box-auto
sudo systemctl stop sing-box-auto
sudo systemctl restart sing-box-auto
sudo systemctl enable sing-box-auto
sudo systemctl disable sing-box-auto
sudo systemctl status sing-box-auto --no-pager
```

## 十一、回滚

如果更新失败且目录中保留了本次更新前的配置备份，可以尝试：

```bash
sudo python3 /usr/local/libexec/sing-box-auto.py rollback
```

如果没有可用备份，先查看日志，不要删除 `/etc/sing-box-auto` 或 `/var/lib/sing-box-auto`。

## 十二、配置和权限

| 路径 | 用途与权限 |
| --- | --- |
| `/etc/sing-box-auto/subscriptions.yaml` | 订阅来源（URL 或本地文件），`root:root`，`0600` |
| `/etc/sing-box-auto/custom_config.yaml` | 分流规则 |
| `/etc/sing-box-auto/config.json` | 生成配置，`root:sing-box`，`0640` |
| `/var/lib/sing-box-auto/rulesets/` | `.srs` 规则集缓存 |
| `/var/lib/sing-box-auto/subscriptions-cache/` | 本地订阅正文，`root:sing-box`，目录 `0770`，文件 `0640` |
| `/var/backups/sing-box-auto/` | 更新过程中的配置备份 |
| `/usr/local/libexec/sing-box-auto.py` | systemd 使用的脚本副本 |
| `/etc/systemd/system/` | `sing-box-auto.service` 和更新 timer 单元 |

服务由 `sing-box` 用户运行。更新脚本会自动设置生成配置和规则集的读取权限，以及运行缓存目录的写入权限。

## 十三、支持范围

支持识别：

- Sing-box JSON；
- Clash/Mihomo YAML；
- 明文分享链接；
- 外层 Base64 分享链接。

首版生成支持 AnyTLS 和 VLESS。未知格式、缺少必要字段或暂不支持协议时会停止生成，不会静默跳过节点。

GitHub 相关规则集下载统一经过 `gh-proxy.org`。默认规则使用 SagerNet 的 `geoip-cn.srs` 和 `geosite-cn.srs`。

默认不会把代理入口暴露到公网。如需远程客户端访问，必须另行设计认证、防火墙和 TLS，不要直接暴露无认证 mixed 入口。
